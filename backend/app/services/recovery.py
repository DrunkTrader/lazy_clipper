"""Startup reconciliation for the supported single-API-process topology.

Run before creating the executor or accepting requests, when no old worker is
alive. This is deliberately not a periodic timeout/lease mechanism: a slow live
worker must never be mistaken for an interrupted one.
"""
from collections.abc import Callable

from sqlalchemy.orm import Session

from ..errors import Stage, interrupted_error
from ..logging import log_stage
from ..models import Clip, Project


INGESTION_STAGES: dict[str, Stage] = {
    "QUEUED": "ingestion",
    "INGESTING": "ingestion",
    "TRANSCRIBING": "transcription",
    "ANALYZING": "analysis",
}


def recover_interrupted_jobs(make_session: Callable[[], Session]) -> None:
    """Atomically make abandoned work explicitly retryable without scheduling it.

    Preserve files, saved transcript/moments, completed clips, and terminal
    project states. A file alone is not proof that a render's READY transaction
    committed, so abandoned clips remain FAILED until explicitly retried.
    Fail startup if reconciliation cannot commit; do not advertise readiness
    with partially repaired state.
    """
    events: list[tuple[str, Stage, str | None]] = []
    with make_session() as session, session.begin():
        clips = session.query(Clip).filter(Clip.status.in_(["QUEUED", "RENDERING"])).all()
        for clip in clips:
            clip.status = "FAILED"
            clip.error_message = interrupted_error("rendering").message
            events.append((clip.project_id, "rendering", clip.id))

        projects = session.query(Project).filter(
            Project.status.in_([*INGESTION_STAGES, "RENDERING"])
        ).all()
        for project in projects:
            if project.status == "RENDERING":
                # Clip failure is isolated; the project's saved analysis stays
                # usable, just as with a normal per-clip rendering exception.
                stage: Stage = "rendering"
                project.status = "READY"
                project.status_message = interrupted_error(stage).message
                project.error_message = None
            else:
                stage = INGESTION_STAGES[project.status]
                project.status = "FAILED"
                project.error_message = interrupted_error(stage).message
                project.status_message = project.error_message
            events.append((project.id, stage, None))

    # Never log successful reconciliation before its transaction commits.
    for project_id, stage, clip_id in events:
        log_stage(project_id, stage, "interrupted", clip_id)
