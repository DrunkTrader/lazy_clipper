"""LazyClipper FastAPI application entrypoint."""
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select, text

from .api.routes import router
from .api.errors import install_error_handlers
from .config import get_settings, validate_runtime_settings
from .db import init_db, session_factory
from .logging import configure_logging, log_failure, logger
from .models import Project
from .services.admission import Admission
from .services.execution import JobSupervisor
from .services.recovery import recover_interrupted_jobs
from .services.storage import reconcile_startup


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if sys.platform != "linux":
        raise RuntimeError("Supervised processing requires Linux (or the Compose Linux image)")
    try:
        validate_runtime_settings(settings)
        init_db(settings.database_url)
        recover_interrupted_jobs(session_factory(settings.database_url))
        with session_factory(settings.database_url)() as session:
            project_ids = set(session.scalars(select(Project.id)))
        report = reconcile_startup(settings.storage_dir, project_ids)
        logger.info("[STARTUP] storage artifacts_removed=%s bytes_removed=%s bytes_used=%s",
                    report.files_removed, report.bytes_removed, report.bytes_used)
    except Exception as exc:
        log_failure(exc, project_id=None, stage="database", context="STARTUP")
        raise
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="lazyclipper")
    supervisor = JobSupervisor(settings)
    admission = Admission(workers=2, waiting=settings.max_queued_jobs)
    app.state.pipeline_executor = executor
    app.state.job_supervisor = supervisor
    app.state.admission = admission
    app.state.pipeline_runner = supervisor.run
    app.state.clip_runner = supervisor.run
    try:
        yield
    finally:
        admission.close()
        supervisor.stop.set()
        # Drain the small bounded queue through its stopped runner so accepted
        # jobs become retryable, and reap active process groups before shutdown.
        executor.shutdown(wait=True, cancel_futures=False)


app = FastAPI(
    title="LazyClipper API",
    version="0.1.0",
    description="A small YouTube-to-transcript-to-moments processing pipeline.",
    lifespan=lifespan,
)
configure_logging()
install_error_handlers(app)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Accept", "Content-Type", "Range"],
)
app.include_router(router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
def ready() -> JSONResponse:
    """Cheap local readiness: database plus in-process admission only."""
    try:
        admission = app.state.admission
        if not admission.snapshot()["accepting"] or app.state.job_supervisor.stop.is_set():
            return JSONResponse(status_code=503, content={"status": "not_ready", "reason": "processing_unavailable"})
        with session_factory(get_settings().database_url)() as session:
            session.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(status_code=503, content={"status": "not_ready", "reason": "dependency_unavailable"})
    return JSONResponse(status_code=200, content={"status": "ready", "processing": "accepting"})
