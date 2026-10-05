"""REST API for project ingestion and inspection."""
from concurrent.futures import Executor
from contextlib import contextmanager
from pathlib import Path
from threading import Lock

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from ..config import get_settings
from ..db import get_db
from ..errors import CLIENT_MESSAGES, processing_error, stored_failure
from ..logging import log_failure, log_stage
from ..models import Clip, Moment, Project, TranscriptSegment
from ..schemas import (
    ClipResponse,
    ClipsResponse,
    CreateClipRequest,
    IngestRequest,
    IngestResponse,
    MomentResponse,
    MomentsResponse,
    ProjectResponse,
    StatusResponse,
    TranscriptResponse,
    TranscriptSegmentResponse,
)
from ..services.youtube import InvalidYouTubeURL, canonical_youtube_url
from ..services.admission import QueueFull
from ..services.storage import (
    StorageBudgetError,
    check_capacity,
    project_root,
    quarantine_project,
    remove_quarantined,
    restore_quarantined,
)

router = APIRouter(prefix="/api/v1")
# The MVP has one API process and its existing thread executor. Serialize
# submissions so double clicks cannot enqueue the same project twice.
_submission_lock = Lock()


def _busy() -> HTTPException:
    return HTTPException(status_code=429, detail=CLIENT_MESSAGES["PROCESSING_BUSY"], headers={"Retry-After": "5"})


@contextmanager
def _submission_guard():
    if not _submission_lock.acquire(timeout=get_settings().submission_lock_timeout_seconds):
        raise _busy()
    try:
        yield
    finally:
        _submission_lock.release()


@contextmanager
def _admit(request: Request, project_id: str | None = None):
    admission = getattr(request.app.state, "admission", None)
    supervisor = getattr(request.app.state, "job_supervisor", None)
    if admission is None or (supervisor is not None and supervisor.stop.is_set()):
        raise HTTPException(status_code=503, detail="Processing is unavailable")
    # A child can commit READY/FAILED just before exiting. Keep the project
    # exclusively owned through group termination and supervisor reconciliation,
    # so a retry cannot be mistaken for that previous job's abandoned state.
    if project_id is not None and admission.owns(project_id):
        raise HTTPException(status_code=409, detail=CLIENT_MESSAGES["PROJECT_BUSY"])
    try:
        with admission.reserve(project_id) as slot:
            state = admission.snapshot()
            check_capacity(get_settings(), state["active"] + state["queued"] + state["reserved"])
            yield slot
    except QueueFull as exc:
        raise _busy() from exc
    except StorageBudgetError as exc:
        raise HTTPException(status_code=507, detail=CLIENT_MESSAGES["STORAGE_BUDGET"]) from exc


@router.get("/processing")
def processing_status(request: Request) -> dict:
    state = request.app.state.admission.snapshot()
    if request.app.state.job_supervisor.stop.is_set():
        state.update(accepting=False, available=0)
    return state


def _project_or_404(project_id: str, session: Session, *, lock: bool = False) -> Project:
    query = session.query(Project).options(joinedload(Project.video)).filter(Project.id == project_id)
    if lock:
        query = query.with_for_update(of=Project)
    project = query.one_or_none()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.post("/ingest", response_model=IngestResponse, status_code=status.HTTP_202_ACCEPTED)
def ingest(payload: IngestRequest, request: Request, session: Session = Depends(get_db)) -> IngestResponse:
    try:
        source_url = canonical_youtube_url(payload.url.strip())
    except InvalidYouTubeURL as exc:
        raise HTTPException(status_code=422, detail=CLIENT_MESSAGES["INVALID_URL"]) from exc
    with _submission_guard():
        # The explicit migration canonicalizes legacy URLs before enforcing the
        # unique lookup key. Lock a saved project while deciding whether to claim it.
        project = session.query(Project).options(joinedload(Project.video)).filter_by(
            source_url=source_url,
        ).with_for_update(of=Project).one_or_none()
        if project is not None and project.status != "FAILED":
            source_missing = project.status == "READY" and (
                project.video is None or _media_path(project.id, project.video.source_path, "source") is None
            )
            if not source_missing:
                return IngestResponse(project_id=project.id, status=project.status.lower())
            # Only an explicit submission repairs deleted media. Reloads stay
            # read-only, and the pipeline retains the saved transcript/moments.
        executor: Executor | None = getattr(request.app.state, "pipeline_executor", None)
        runner = getattr(request.app.state, "pipeline_runner", None)
        if executor is None or runner is None:
            raise HTTPException(status_code=503, detail="The in-process pipeline is not configured")
        with _admit(request, project.id if project is not None else None) as slot:
            created = project is None
            project = project or Project(source_url=source_url)
            project.status = "QUEUED"
            project.status_message = "Waiting for processing"
            project.error_message = None
            session.add(project)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                winner = session.query(Project).filter_by(source_url=source_url).one_or_none() if created else None
                if winner is None:
                    raise
                # Another transaction established this canonical identity. Do
                # not dispatch a second task or rewrite the winning record.
                return IngestResponse(project_id=winner.id, status=winner.status.lower())
            request.state.project_id = project.id
            log_stage(project.id, "ingestion", "created" if created else "queued")
            try:
                slot.submit(executor, runner, project.id, project_id=project.id)
            except Exception as exc:
                project.status = "FAILED"
                project.error_message = processing_error("ingestion").message
                project.status_message = project.error_message
                session.commit()
                raise HTTPException(status_code=503, detail=project.error_message) from exc
        return IngestResponse(project_id=project.id, status="queued")


@router.get("/projects", response_model=list[ProjectResponse])
def list_projects(
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=1_000_000),
    session: Session = Depends(get_db),
) -> list[Project]:
    return session.query(Project).options(joinedload(Project.video)).order_by(
        Project.created_at.desc(), Project.id.desc(),
    ).offset(offset).limit(limit).all()


@router.get("/projects/{project_id}", response_model=ProjectResponse)
def get_project(project_id: str, session: Session = Depends(get_db)) -> ProjectResponse:
    project = _project_or_404(project_id, session)
    # The downloaded source is backend-only for transcription and rendering.
    # Frontend playback uses the YouTube IFrame Player API with youtube_id.
    return ProjectResponse.model_validate(project)


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: str, request: Request, session: Session = Depends(get_db)) -> Response:
    with _submission_guard():
        project = _project_or_404(project_id, session, lock=True)
        admission = getattr(request.app.state, "admission", None)
        if (admission is not None and admission.owns(project_id)) or project.status in {
            "QUEUED", "INGESTING", "TRANSCRIBING", "ANALYZING", "RENDERING",
        }:
            raise HTTPException(status_code=409, detail=CLIENT_MESSAGES["PROJECT_BUSY"])
        root, quarantined = quarantine_project(get_settings().storage_dir, project_id)
        try:
            session.delete(project)
            session.commit()
        except Exception:
            session.rollback()
            # On an ambiguous commit outcome, query the database before restoring.
            # If it is unavailable, leave quarantine intact for startup recovery.
            if session.get(Project, project_id) is not None:
                restore_quarantined(root, quarantined)
            raise
        try:
            remove_quarantined(quarantined)
        except Exception as exc:
            log_failure(exc, project_id=project_id, stage="database", context="STORAGE_CLEANUP")
        return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/projects/{project_id}/status", response_model=StatusResponse)
def get_status(project_id: str, session: Session = Depends(get_db)) -> StatusResponse:
    project = _project_or_404(project_id, session)
    response = StatusResponse(project_id=project.id, status=project.status, message=project.status_message)
    if project.status == "FAILED" or project.error_message:
        response.failed_stage, response.error = stored_failure(project.error_message)
        response.message = response.error.message
    return response


@router.get("/projects/{project_id}/transcript", response_model=TranscriptResponse, response_model_exclude_unset=True)
def get_transcript(project_id: str, include_words: bool = True, session: Session = Depends(get_db)) -> TranscriptResponse:
    _project_or_404(project_id, session)
    fields = [TranscriptSegment.id, TranscriptSegment.start, TranscriptSegment.end,
              TranscriptSegment.text, TranscriptSegment.speaker, TranscriptSegment.segment_index]
    if include_words:
        fields.append(TranscriptSegment.words)
    rows = session.query(*fields).filter_by(project_id=project_id).order_by(TranscriptSegment.segment_index).all()
    return TranscriptResponse(project_id=project_id, segments=[TranscriptSegmentResponse(**row._mapping) for row in rows])


@router.get("/projects/{project_id}/moments", response_model=MomentsResponse)
def get_moments(project_id: str, session: Session = Depends(get_db)) -> MomentsResponse:
    project = _project_or_404(project_id, session)
    moments = [MomentResponse.model_validate(moment) for moment in project.moments]
    return MomentsResponse(project_id=project.id, moments=moments)


def _media_path(project_id: str, stored_path: str | None, directory: str) -> Path | None:
    """Resolve only persisted files inside this project's media directory."""
    if not stored_path:
        return None
    try:
        root = project_root(get_settings().storage_dir, project_id) / directory
    except ValueError:
        return None
    if root.is_symlink():
        return None
    path = Path(stored_path).resolve()
    if path.is_relative_to(root) and path.is_file() and path.stat().st_size > 0:
        return path
    return None


@router.get("/projects/{project_id}/clips", response_model=ClipsResponse)
def get_clips(project_id: str, request: Request, session: Session = Depends(get_db)) -> ClipsResponse:
    _project_or_404(project_id, session)
    clips = session.query(Clip).join(Moment).filter(Clip.project_id == project_id).order_by(Moment.rank).all()
    responses = []
    for clip in clips:
        response = _clip_response(clip, request)
        if clip.status == "READY":
            if not _media_path(project_id, clip.output_path, "clips"):
                # Keep GETs read-only, but do not advertise an unplayable file.
                response.status = "FAILED"
                response.failed_stage = "media"
                response.error = processing_error("media")
                response.error_message = response.error.message
                response.media_url = response.download_url = None
        responses.append(response)
    return ClipsResponse(project_id=project_id, clips=responses)


@router.post("/projects/{project_id}/clips", response_model=ClipResponse, status_code=status.HTTP_202_ACCEPTED)
def create_clip(
    project_id: str,
    payload: CreateClipRequest,
    request: Request,
    session: Session = Depends(get_db),
) -> ClipResponse:
    if payload.end - payload.start > get_settings().max_clip_seconds:
        raise HTTPException(status_code=422, detail=CLIENT_MESSAGES["CLIP_TOO_LONG"])
    with _submission_guard():
        project = _project_or_404(project_id, session, lock=True)
        if project.status == "RENDERING":
            raise HTTPException(status_code=409, detail="Project processing is already in progress")
        if project.status not in {"READY", "FAILED"}:
            raise HTTPException(status_code=409, detail="Project analysis is not complete")
        moment = session.query(Moment).filter_by(id=payload.moment_id, project_id=project_id).one_or_none()
        if moment is None:
            raise HTTPException(status_code=404, detail="Moment not found in this project")
        if project.video is None or _media_path(project_id, project.video.source_path, "source") is None:
            raise HTTPException(status_code=409, detail="The downloaded source video is missing")
        if project.video.duration is not None and payload.end > project.video.duration + 0.1:
            raise HTTPException(status_code=422, detail="Clip timestamps exceed the source video duration")
        executor = getattr(request.app.state, "pipeline_executor", None)
        runner = getattr(request.app.state, "clip_runner", None)
        if executor is None or runner is None:
            raise HTTPException(status_code=503, detail="Clip processing is unavailable")
        clip = session.query(Clip).filter_by(project_id=project_id, moment_id=moment.id).one_or_none()
        if clip is not None and clip.status in {"QUEUED", "RENDERING"}:
            raise HTTPException(status_code=409, detail="This clip is already being rendered")
        if clip is not None and clip.status == "READY" and _media_path(project_id, clip.output_path, "clips") and (
            clip.start == payload.start and clip.end == payload.end
        ):
            return _clip_response(clip, request)
        with _admit(request, project_id) as slot:
            if clip is None:
                clip = Clip(project_id=project_id, moment_id=moment.id)
                session.add(clip)
            clip.start = payload.start
            clip.end = payload.end
            clip.status = "QUEUED"
            clip.output_path = None
            clip.error_message = None
            project.status = "RENDERING"
            project.status_message = "Waiting to render selected clip"
            project.error_message = None
            session.flush()
            session.commit()
            request.state.clip_id = clip.id
            log_stage(project_id, "rendering", "queued", clip.id)
            try:
                slot.submit(executor, runner, project_id, clip.id)
            except Exception as exc:
                clip.status = "FAILED"
                clip.error_message = processing_error("rendering").message
                project.status = "READY"
                project.status_message = clip.error_message
                session.commit()
                raise HTTPException(status_code=503, detail=project.status_message) from exc
        return _clip_response(clip, request)


def _clip_response(clip: Clip, request: Request) -> ClipResponse:
    del request
    response = ClipResponse.model_validate(clip)
    if clip.status == "READY":
        response.media_url = f"/api/v1/projects/{clip.project_id}/clips/{clip.id}/media"
        response.download_url = response.media_url + "?download=true"
    return response


@router.get("/projects/{project_id}/clips/{clip_id}/media")
def get_clip_media(project_id: str, clip_id: str, download: bool = False, session: Session = Depends(get_db)) -> FileResponse:
    clip = session.query(Clip).filter_by(id=clip_id, project_id=project_id).one_or_none()
    if clip is None:
        raise HTTPException(status_code=404, detail="Clip not found in this project")
    if clip.status != "READY":
        raise HTTPException(status_code=409, detail="Clip is not ready")
    path = _media_path(project_id, clip.output_path, "clips")
    if path is None:
        raise HTTPException(status_code=404, detail="Clip file is not available")
    return FileResponse(
        path, media_type="video/mp4", filename=f"lazyclipper-{clip.id}.mp4",
        content_disposition_type="attachment" if download else "inline",
    )
