"""In-process project processing pipeline with persisted, reusable stages."""
import json
from pathlib import Path
from typing import Callable

from sqlalchemy.orm import Session

from ..analysis.moments import calculate_composite_score
from ..analysis.transcript import chunk_segments, normalize_segments, require_word_alignment
from ..config import Settings, get_settings
from ..db import session_factory
from ..errors import CAPTION_MESSAGES, LIMIT_MESSAGES, Stage, WorkloadLimitError, failure_stage, processing_error
from ..logging import log_failure, log_stage
from ..models import Clip, Moment, Project, TranscriptSegment, Video
from .captions import CaptionError, require_clip_alignment, select_words, write_ass
from .media import MediaService
from .limits import require_clip_duration, require_source_duration
from .storage import atomic_write_text
from .transcription import WhisperTimestampedTranscriber
from .youtube import YoutubeService


def existing_file(value: str | None) -> Path | None:
    path = Path(value) if value else None
    return path if path and path.is_file() and path.stat().st_size > 0 else None


class Pipeline:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        youtube: YoutubeService | None = None,
        media: MediaService | None = None,
        transcriber: WhisperTimestampedTranscriber | None = None,
        analyzer=None,
        make_session: Callable[[], Session] | None = None,
    ):
        self.settings = settings or get_settings()
        self.youtube = youtube or YoutubeService(self.settings)
        self.media = media or MediaService(self.settings)
        self.transcriber = transcriber or WhisperTimestampedTranscriber(self.settings)
        self.analyzer = analyzer
        # Defer session/engine setup so worker failures reach the logged boundary.
        self.make_session = make_session or (lambda: session_factory()())

    @staticmethod
    def _status(session: Session, project: Project, status: str, message: str) -> None:
        project.status = status
        project.status_message = message
        project.error_message = None
        session.add(project)
        session.commit()

    def run(self, project_id: str) -> None:
        session = None
        stage: Stage = "ingestion"
        try:
            session = self.make_session()
            project = session.get(Project, project_id)
            if project is None or project.status == "READY":
                return
            root = self.settings.project_storage(project_id)
            video = project.video
            source_path = existing_file(video.source_path) if video else None
            if source_path is None:
                stage = "fetching"
                log_stage(project_id, stage, "started")
                self._status(session, project, "INGESTING", "Reading video metadata")
                metadata = self.youtube.get_metadata(project.source_url)
                require_source_duration(metadata.duration, self.settings.max_source_seconds)
                video = video or Video(project_id=project.id)
                video.youtube_id = metadata.youtube_id
                video.title = metadata.title
                video.duration = metadata.duration
                video.thumbnail_url = metadata.thumbnail_url
                project.title = metadata.title
                project.video = video
                session.add(video)
                session.commit()

                log_stage(project_id, stage, "completed")
                stage = "ingestion"
                log_stage(project_id, stage, "started")
                source_path = self.youtube.download_video(project.source_url, root / "source")
                video.source_path = str(source_path)
                session.commit()
                log_stage(project_id, stage, "completed")

            require_source_duration(video.duration, self.settings.max_source_seconds)
            # Reopening/retrying a project uses its database rows and persisted
            # files. Only an absent stage calls the expensive external service.
            normalized = [
                {"start": row.start, "end": row.end, "text": row.text, "speaker": row.speaker,
                 "words": row.words, "segment_index": row.segment_index}
                for row in project.transcript_segments
            ]
            if not normalized:
                audio_path = existing_file(video.audio_path)
                if audio_path is None:
                    stage = "ingestion"
                    log_stage(project_id, stage, "started")
                    audio_path = self.media.extract_audio(source_path, root / "audio" / "audio.wav")
                    video.audio_path = str(audio_path)
                    session.commit()
                    log_stage(project_id, stage, "completed")

                stage = "transcription"
                log_stage(project_id, stage, "started")
                self._status(session, project, "TRANSCRIBING", "Generating timestamped transcript")
                raw_segments = self.transcriber.transcribe(audio_path)
                require_word_alignment(raw_segments)
                normalized = normalize_segments(raw_segments)
                if not normalized:
                    raise RuntimeError("Transcript normalization produced no valid segments")
                require_word_alignment(normalized)
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
                atomic_write_text(root / "transcript" / "transcript.json", json.dumps(normalized, indent=2))
                session.commit()
                log_stage(project_id, stage, "completed")

            if not project.analysis_completed:
                stage = "analysis"
                log_stage(project_id, stage, "started")
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
                for rank, candidate in enumerate(candidates):
                    project.moments.append(Moment(
                        project_id=project.id,
                        title=candidate.title,
                        description=candidate.description,
                        start=candidate.start,
                        end=candidate.end,
                        score=calculate_composite_score(candidate.scores),
                        reason=candidate.reason,
                        rank=rank,
                        source_segments=candidate.source_segments,
                        dimensions=candidate.scores.model_dump(),
                    ))
                project.analysis_completed = True
                session.commit()
                log_stage(project_id, stage, "completed")
            self._status(session, project, "READY", f"Analysis complete: {len(project.moments)} moments ready for clipping")
            log_stage(project_id, "pipeline", "ready")
        except Exception as exc:
            self._fail(session, project_id, exc, stage)
        finally:
            if session is not None:
                session.close()

    def _render_clips(self, session: Session, project: Project) -> None:
        """Render all saved moments for an explicit legacy/backfill call.

        Normal ingestion never calls this method. New clip requests use
        :meth:`render_clip` so selecting one moment only renders that moment.
        """
        root = self.settings.project_storage(project.id)
        clips = {clip.moment_id: clip for clip in project.clips}
        for moment in project.moments:
            if moment.id not in clips:
                clip = Clip(project_id=project.id, moment_id=moment.id, start=moment.start, end=moment.end)
                session.add(clip)
                clips[moment.id] = clip
        session.commit()

        source_path = existing_file(project.video.source_path) if project.video else None
        for index, clip in enumerate(clips.values(), 1):
            if clip.status == "READY" and existing_file(clip.output_path):
                continue
            clip.status = "RENDERING"
            clip.error_message = None
            clip.output_path = None
            self._status(session, project, "RENDERING", f"Rendering vertical clip {index} of {len(clips)}")
            log_stage(project.id, "rendering", "started", clip.id)
            project_id, clip_id = project.id, clip.id
            try:
                if source_path is None:
                    raise RuntimeError("Source video file is missing; clip cannot be rendered")
                duration = project.video.duration
                if duration is not None and (clip.start >= duration or clip.end > duration + 0.1):
                    raise ValueError("Clip timestamps exceed the source video duration")
                output = self._render_clip_media(project, clip, source_path, root)
                clip.output_path = str(output)
                clip.status = "READY"
                session.commit()
                log_stage(project.id, "rendering", "completed", clip.id)
            except Exception as exc:
                # A bad range or encoder failure must not discard other clips.
                self._fail_clip(session, project_id, clip_id, exc)

        failed = sum(clip.status == "FAILED" for clip in clips.values())
        message = f"Processing complete: {len(clips) - failed} clips ready"
        if failed:
            message += f", {failed} failed (retry clip generation to try again)"
        self._status(session, project, "READY", message)

    def render_clip(self, project_id: str, clip_id: str) -> None:
        """Render one persisted clip request using the downloaded source."""
        session = None
        try:
            session = self.make_session()
            project = session.get(Project, project_id)
            clip = session.get(Clip, clip_id)
            if project is None or clip is None or clip.project_id != project_id:
                return
            if clip.status == "READY" and existing_file(clip.output_path):
                return

            root = self.settings.project_storage(project_id)
            source_path = existing_file(project.video.source_path) if project.video else None
            clip.status = "RENDERING"
            clip.error_message = None
            clip.output_path = None
            project.status = "RENDERING"
            project.status_message = "Rendering selected vertical clip"
            project.error_message = None
            session.commit()
            log_stage(project_id, "rendering", "started", clip_id)
            if source_path is None:
                raise RuntimeError("Source video file is missing; clip cannot be rendered")
            duration = project.video.duration if project.video else None
            if duration is not None and (clip.start >= duration or clip.end > duration + 0.1):
                raise ValueError("Clip timestamps exceed the source video duration")
            output = self._render_clip_media(project, clip, source_path, root)
            clip.output_path = str(output)
            clip.status = "READY"
            project.status_message = "Selected clip is ready"
            project.status = "READY"
            session.commit()
            log_stage(project_id, "rendering", "completed", clip_id)
        except Exception as exc:
            self._fail_clip(session, project_id, clip_id, exc)
        finally:
            if session is not None:
                session.close()

    def _render_clip_media(self, project: Project, clip: Clip, source_path: Path, root: Path) -> Path:
        """Render one user-selected range using only persisted word timestamps."""
        require_clip_duration(clip.start, clip.end, self.settings.max_clip_seconds)
        require_clip_alignment(project.transcript_segments, clip.start, clip.end)
        words = select_words(project.transcript_segments, clip.start, clip.end)
        subtitle_path = root / "clips" / f"{clip.id}.ass"
        try:
            write_ass(subtitle_path, words, clip.start, clip.end)
            return self.media.render_clip(
                source_path,
                root / "clips" / f"{clip.id}.mp4",
                clip.start,
                clip.end,
                subtitle_path=subtitle_path,
            )
        finally:
            subtitle_path.unlink(missing_ok=True)

    @staticmethod
    def _fail(session: Session | None, project_id: str, exc: Exception, stage: Stage = "unknown") -> None:
        stage = failure_stage(exc, stage)
        log_failure(exc, project_id=project_id, stage=stage)
        if session is None:
            return
        try:
            session.rollback()
            project = session.get(Project, project_id)
            if project is not None:
                project.status = "FAILED"
                project.status_message = (
                    LIMIT_MESSAGES[exc.code] if isinstance(exc, WorkloadLimitError) else processing_error(stage).message
                )
                project.error_message = project.status_message
                session.commit()
        except Exception as persist_exc:
            log_failure(persist_exc, project_id=project_id, stage="database", context="PERSIST_FAILURE")

    @staticmethod
    def _fail_clip(session: Session | None, project_id: str, clip_id: str, exc: Exception) -> None:
        stage = failure_stage(exc, "rendering")
        log_failure(exc, project_id=project_id, clip_id=clip_id, stage=stage)
        if session is None:
            return
        try:
            session.rollback()
            clip = session.get(Clip, clip_id)
            project = session.get(Project, project_id)
            if clip is not None and clip.project_id == project_id:
                clip.status = "FAILED"
                clip.output_path = None
                clip.error_message = (
                    CAPTION_MESSAGES.get(exc.code, processing_error(stage).message)
                    if isinstance(exc, CaptionError) else processing_error(stage).message
                )
                if isinstance(exc, WorkloadLimitError):
                    clip.error_message = LIMIT_MESSAGES[exc.code]
                if project is not None:
                    project.status = "READY"
                    project.status_message = "Selected clip rendering failed; retry the clip"
                session.commit()
        except Exception as persist_exc:
            log_failure(persist_exc, project_id=project_id, clip_id=clip_id, stage="database", context="PERSIST_FAILURE")

    def render_clips(self, project_id: str) -> None:
        """Explicit backfill/retry for saved moments, never ingest or analyze."""
        session = None
        try:
            session = self.make_session()
            project = session.get(Project, project_id)
            if project is not None:
                self._render_clips(session, project)
        except Exception as exc:
            self._fail(session, project_id, exc, "rendering")
        finally:
            if session is not None:
                session.close()


def run_pipeline(project_id: str) -> None:
    """Entry point suitable for the existing thread executor."""
    Pipeline().run(project_id)


def render_project_clips(project_id: str) -> None:
    Pipeline().render_clips(project_id)


def render_project_clip(project_id: str, clip_id: str) -> None:
    Pipeline().render_clip(project_id, clip_id)
