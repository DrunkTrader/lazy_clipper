"""In-process project processing pipeline."""
import json
from pathlib import Path
from typing import Callable

from sqlalchemy.orm import Session, sessionmaker

from ..analysis.moments import CandidateMoment, calculate_composite_score
from ..analysis.transcript import chunk_segments, normalize_segments
from ..config import Settings, get_settings
from ..db import session_factory
from ..models import Moment, Project, TranscriptSegment, Video
from .media import MediaService
from .transcription import WhisperXTranscriber
from .youtube import YoutubeService


class Pipeline:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        youtube: YoutubeService | None = None,
        media: MediaService | None = None,
        transcriber: WhisperXTranscriber | None = None,
        analyzer=None,
        make_session: Callable[[], Session] | None = None,
    ):
        self.settings = settings or get_settings()
        self.youtube = youtube or YoutubeService()
        self.media = media or MediaService(self.settings)
        self.transcriber = transcriber or WhisperXTranscriber(self.settings)
        self.analyzer = analyzer
        self.make_session = make_session or session_factory() 

    @staticmethod
    def _status(session: Session, project: Project, status: str, message: str) -> None:
        project.status = status
        project.status_message = message
        project.error_message = None
        session.add(project)
        session.commit()

    def run(self, project_id: str) -> None:
        session = self.make_session()
        project: Project | None = None
        try:
            project = session.get(Project, project_id)
            if project is None:
                return
            self._status(session, project, "INGESTING", "Reading video metadata")
            metadata = self.youtube.get_metadata(project.source_url)
            root = self.settings.project_storage(project_id)
            source_path = self.youtube.download_video(project.source_url, root / "source")
            audio_path = self.media.extract_audio(source_path, root / "audio" / "audio.wav")
            video = project.video or Video(project_id=project.id)
            video.youtube_id = metadata.youtube_id
            video.title = metadata.title
            video.duration = metadata.duration
            video.thumbnail_url = metadata.thumbnail_url
            video.source_path = str(source_path)
            video.audio_path = str(audio_path)
            project.title = metadata.title
            session.add(video)
            session.commit()

            self._status(session, project, "TRANSCRIBING", "Generating timestamped transcript")
            normalized = normalize_segments(self.transcriber.transcribe(audio_path))
            session.query(TranscriptSegment).filter_by(project_id=project.id).delete()
            for index, segment in enumerate(normalized):
                session.add(TranscriptSegment(
                    project_id=project.id,
                    start=float(segment["start"]),
                    end=float(segment["end"]),
                    text=segment["text"],
                    speaker=segment.get("speaker"),
                    segment_index=index,
                    words=segment.get("words"),
                ))
            (root / "transcript" / "transcript.json").write_text(json.dumps(normalized, indent=2), encoding="utf-8")
            session.commit()

            self._status(session, project, "ANALYZING", "Finding and ranking candidate moments")
            chunks = chunk_segments(
                normalized,
                window_seconds=self.settings.transcript_chunk_seconds,
                overlap_seconds=self.settings.transcript_overlap_seconds,
            )
            if self.analyzer is None:
                from .llm import MomentAnalyzer
                self.analyzer = MomentAnalyzer(self.settings)
            candidates = self.analyzer.analyze(chunks)
            session.query(Moment).filter_by(project_id=project.id).delete()
            for rank, candidate in enumerate(candidates):
                score = calculate_composite_score(candidate.scores)
                session.add(Moment(
                    project_id=project.id,
                    title=candidate.title,
                    description=candidate.description,
                    start=candidate.start,
                    end=candidate.end,
                    score=score,
                    reason=candidate.reason,
                    rank=rank,
                    source_segments=candidate.source_segments,
                    dimensions=candidate.scores.model_dump(),
                ))
            self._status(session, project, "READY", "Processing complete")
        except Exception as exc:
            session.rollback()
            if project is None:
                project = session.get(Project, project_id)
            if project is not None:
                project.status = "FAILED"
                project.status_message = "Processing failed"
                project.error_message = str(exc)[-4000:]
                session.add(project)
                session.commit()
        finally:
            session.close()


def run_pipeline(project_id: str) -> None:
    """Entry point suitable for a thread executor."""
    Pipeline().run(project_id)
