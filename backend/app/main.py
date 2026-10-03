"""LazyClipper FastAPI application entrypoint."""
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import router
from .api.errors import install_error_handlers
from .config import get_settings
from .db import init_db
from .logging import configure_logging, log_failure
from .services.pipeline import render_project_clip, run_pipeline


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    try:
        init_db(settings.database_url)
    except Exception as exc:
        log_failure(exc, project_id=None, stage="database", context="STARTUP")
        raise
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="lazyclipper")
    app.state.pipeline_executor = executor
    app.state.pipeline_runner = run_pipeline
    app.state.clip_runner = render_project_clip
    try:
        yield
    finally:
        # Do not block shutdown on a long transcription/yt-dlp task. The task has
        # its own database session and will finish or mark its project failed.
        executor.shutdown(wait=False, cancel_futures=False)


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
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
