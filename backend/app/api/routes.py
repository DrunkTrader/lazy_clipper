"""REST API for project ingestion and inspection."""
from concurrent.futures import Executor
from pathlib import Path
import mimetypes

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session, joinedload

from ..db import get_db
from ..models import Project
from ..schemas import (
    IngestRequest,
    IngestResponse,
    MomentResponse,
    MomentsResponse,
    ProjectResponse,
    StatusResponse,
    TranscriptResponse,
)
from ..services.youtube import InvalidYouTubeURL, validate_youtube_url

router = APIRouter(prefix="/api/v1")


def _project_or_404(project_id: str, session: Session) -> Project:
    project = session.query(Project).options(joinedload(Project.video)).filter(Project.id == project_id).one_or_none()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.post("/ingest", response_model=IngestResponse, status_code=status.HTTP_202_ACCEPTED)
def ingest(payload: IngestRequest, request: Request, session: Session = Depends(get_db)) -> IngestResponse:
    try:
        validate_youtube_url(payload.url)
    except InvalidYouTubeURL as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    project = Project(source_url=payload.url.strip(), status="QUEUED", status_message="Waiting for processing")
    session.add(project)
    session.commit()
    session.refresh(project)

    executor: Executor | None = getattr(request.app.state, "pipeline_executor", None)
    runner = getattr(request.app.state, "pipeline_runner", None)
    if executor is None or runner is None:
        project.status = "FAILED"
        project.status_message = "Processing unavailable"
        project.error_message = "The in-process pipeline is not configured"
        session.commit()
        raise HTTPException(status_code=503, detail=project.error_message)
    executor.submit(runner, project.id)
    return IngestResponse(project_id=project.id, status=project.status.lower())


@router.get("/projects", response_model=list[ProjectResponse])
def list_projects(session: Session = Depends(get_db)) -> list[Project]:
    return session.query(Project).options(joinedload(Project.video)).order_by(Project.created_at.desc()).all()


@router.get("/projects/{project_id}", response_model=ProjectResponse)
def get_project(project_id: str, request: Request, session: Session = Depends(get_db)) -> ProjectResponse:
    project = _project_or_404(project_id, session)
    response = ProjectResponse.model_validate(project)
    if response.video is not None:
        response.video.media_url = str(request.base_url).rstrip("/") + f"/api/v1/projects/{project_id}/media"
    return response


@router.get("/projects/{project_id}/media")
def get_media(project_id: str, session: Session = Depends(get_db)) -> FileResponse:
    project = _project_or_404(project_id, session)
    if project.video is None or not project.video.source_path:
        raise HTTPException(status_code=404, detail="Source video is not available")
    source_path = Path(project.video.source_path)
    if not source_path.is_file():
        raise HTTPException(status_code=404, detail="Source video file is not available")
    # The path is created by the pipeline under the configured storage root;
    # only that persisted path is ever served.
    media_type = mimetypes.guess_type(source_path.name)[0] or "application/octet-stream"
    return FileResponse(source_path, media_type=media_type, filename=source_path.name)


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
