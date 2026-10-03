"""REST API for project ingestion and inspection."""
from concurrent.futures import Executor
from pathlib import Path
from threading import Lock
import mimetypes

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from ..config import get_settings
from ..db import get_db
from ..models import Clip, Moment, Project, Video
from ..schemas import (
    ClipResponse,
    ClipsResponse,
    IngestRequest,
    IngestResponse,
    MomentResponse,
    MomentsResponse,
    ProjectResponse,
    StatusResponse,
    TranscriptResponse,
)
from ..services.youtube import InvalidYouTubeURL, canonical_youtube_url, validate_youtube_url

router = APIRouter(prefix="/api/v1")
# The MVP has one API process and its existing thread executor. Serialize
# submissions so double clicks cannot enqueue the same project twice.
_submission_lock = Lock()


def _project_or_404(project_id: str, session: Session) -> Project:
    project = session.query(Project).options(joinedload(Project.video)).filter(Project.id == project_id).one_or_none()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.post("/ingest", response_model=IngestResponse, status_code=status.HTTP_202_ACCEPTED)
def ingest(payload: IngestRequest, request: Request, session: Session = Depends(get_db)) -> IngestResponse:
    try:
        video_id = validate_youtube_url(payload.url.strip())
        source_url = canonical_youtube_url(payload.url.strip())
    except InvalidYouTubeURL as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    with _submission_lock:
        # Video IDs also match older projects whose URL was not canonicalized.
        project = session.query(Project).outerjoin(Video).filter(or_(
            Video.youtube_id == video_id,
            Project.source_url.in_([source_url, payload.url.strip()]),
        )).order_by(Project.created_at.desc()).first()
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
        project = project or Project(source_url=source_url)
        project.status = "QUEUED"
        project.status_message = "Waiting for processing"
        project.error_message = None
        session.add(project)
        session.commit()
        try:
            executor.submit(runner, project.id)
        except RuntimeError as exc:
            project.status = "FAILED"
            project.error_message = "Processing is unavailable; try again"
            session.commit()
            raise HTTPException(status_code=503, detail=project.error_message) from exc
        return IngestResponse(project_id=project.id, status="queued")


@router.get("/projects", response_model=list[ProjectResponse])
def list_projects(session: Session = Depends(get_db)) -> list[Project]:
    return session.query(Project).options(joinedload(Project.video)).order_by(Project.created_at.desc()).all()


@router.get("/projects/{project_id}", response_model=ProjectResponse)
def get_project(project_id: str, request: Request, session: Session = Depends(get_db)) -> ProjectResponse:
    project = _project_or_404(project_id, session)
    response = ProjectResponse.model_validate(project)
    if response.video is not None and _media_path(project_id, project.video.source_path, "source"):
        response.video.media_url = str(request.base_url).rstrip("/") + f"/api/v1/projects/{project_id}/media"
    return response


@router.get("/projects/{project_id}/media")
def get_media(project_id: str, session: Session = Depends(get_db)) -> FileResponse:
    project = _project_or_404(project_id, session)
    if project.video is None or not project.video.source_path:
        raise HTTPException(status_code=404, detail="Source video is not available")
    source_path = _media_path(project_id, project.video.source_path, "source")
    if source_path is None:
        raise HTTPException(status_code=404, detail="Source video file is not available")
    media_type = mimetypes.guess_type(source_path.name)[0] or "application/octet-stream"
    return FileResponse(source_path, media_type=media_type, filename=source_path.name, content_disposition_type="inline")


@router.get("/projects/{project_id}/status", response_model=StatusResponse)
def get_status(project_id: str, session: Session = Depends(get_db)) -> StatusResponse:
    project = _project_or_404(project_id, session)
    return StatusResponse(project_id=project.id, status=project.status, message=project.status_message, error=project.error_message)


@router.get("/projects/{project_id}/transcript", response_model=TranscriptResponse)
def get_transcript(project_id: str, session: Session = Depends(get_db)) -> TranscriptResponse:
    project = _project_or_404(project_id, session)
    return TranscriptResponse(project_id=project.id, segments=project.transcript_segments)


@router.get("/projects/{project_id}/moments", response_model=MomentsResponse)
def get_moments(project_id: str, session: Session = Depends(get_db)) -> MomentsResponse:
    project = _project_or_404(project_id, session)
    moments = [MomentResponse.model_validate(moment) for moment in project.moments]
    return MomentsResponse(project_id=project.id, moments=moments)


def _media_path(project_id: str, stored_path: str | None, directory: str) -> Path | None:
    """Resolve only persisted files inside this project's media directory."""
    if not stored_path:
        return None
    root = (get_settings().storage_dir / "projects" / project_id / directory).resolve()
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
        response = ClipResponse.model_validate(clip)
        if clip.status == "READY":
            if _media_path(project_id, clip.output_path, "clips"):
                response.media_url = str(request.base_url).rstrip("/") + f"/api/v1/projects/{project_id}/clips/{clip.id}/media"
                response.download_url = response.media_url + "?download=true"
            else:
                # Keep GETs read-only, but do not advertise an unplayable file.
                response.status = "FAILED"
                response.error_message = "Clip file is missing; retry clip generation"
        responses.append(response)
    return ClipsResponse(project_id=project_id, clips=responses)


@router.post("/projects/{project_id}/clips", response_model=IngestResponse, status_code=status.HTTP_202_ACCEPTED)
def generate_clips(project_id: str, request: Request, session: Session = Depends(get_db)) -> IngestResponse:
    with _submission_lock:
        project = _project_or_404(project_id, session)
        if project.status not in {"READY", "FAILED"}:
            raise HTTPException(status_code=409, detail="Project processing is already in progress")
        if not project.moments:
            raise HTTPException(status_code=409, detail="No saved moments are available to render")
        if project.video is None or _media_path(project_id, project.video.source_path, "source") is None:
            raise HTTPException(status_code=409, detail="The downloaded source video is missing")
        executor = getattr(request.app.state, "pipeline_executor", None)
        runner = getattr(request.app.state, "clip_runner", None)
        if executor is None or runner is None:
            raise HTTPException(status_code=503, detail="Clip processing is unavailable")
        project.status = "RENDERING"
        project.status_message = "Waiting to render vertical clips"
        project.error_message = None
        session.commit()
        try:
            executor.submit(runner, project_id)
        except RuntimeError as exc:
            project.status = "READY"
            project.status_message = "Clip processing is unavailable; try again"
            session.commit()
            raise HTTPException(status_code=503, detail=project.status_message) from exc
        return IngestResponse(project_id=project_id, status="rendering")


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
