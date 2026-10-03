"""In-process project processing pipeline with persisted, reusable stages."""
import json
from pathlib import Path
from typing import Callable

from sqlalchemy.orm import Session

from ..analysis.moments import calculate_composite_score
from ..analysis.transcript import chunk_segments, normalize_segments
from ..config import Settings, get_settings
from ..db import session_factory
from ..models import Clip, Moment, Project, TranscriptSegment, Video
from .media import MediaService
from .transcription import WhisperXTranscriber
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
        transcriber: WhisperXTranscriber | None = None,
        analyzer=None,
        make_session: Callable[[], Session] | None = None,
    ):
        self.settings = settings or get_settings()
        self.youtube = youtube or YoutubeService(self.settings)
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
        try:
            project = session.get(Project, project_id)
            if project is None or project.status == "READY":
                return
            root = self.settings.project_storage(project_id)
            video = project.video
            source_path = existing_file(video.source_path) if video else None
            if source_path is None:
                self._status(session, project, "INGESTING", "Reading video metadata")
                metadata = self.youtube.get_metadata(project.source_url)
                video = video or Video(project_id=project.id)
                video.youtube_id = metadata.youtube_id
                video.title = metadata.title
                video.duration = metadata.duration
                video.thumbnail_url = metadata.thumbnail_url
                project.title = metadata.title
                project.video = video
                session.add(video)
                session.commit()

                source_path = self.youtube.download_video(project.source_url, root / "source")
                video.source_path = str(source_path)
                session.commit()

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
                    audio_path = self.media.extract_audio(source_path, root / "audio" / "audio.wav")
                    video.audio_path = str(audio_path)
                    session.commit()

                self._status(session, project, "TRANSCRIBING", "Generating timestamped transcript")
                normalized = normalize_segments(self.transcriber.transcribe(audio_path))
                if not normalized:
                    raise RuntimeError("Transcript normalization produced no valid segments")
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

            if not project.moments:
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
                session.commit()
            self._status(session, project, "READY", f"Analysis complete: {len(project.moments)} moments ready for clipping")
        except Exception as exc:
            self._fail(session, project_id, exc)
        finally:
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
            try:
                if source_path is None:
                    raise RuntimeError("Source video file is missing; clip cannot be rendered")
                duration = project.video.duration
                if duration is not None and (clip.start >= duration or clip.end > duration + 0.1):
                    raise ValueError("Clip timestamps exceed the source video duration")
                output = self.media.render_clip(source_path, root / "clips" / f"{clip.id}.mp4", clip.start, clip.end)
                clip.output_path = str(output)
                clip.status = "READY"
            except Exception as exc:
                # A bad range or encoder failure must not discard other clips.
                clip.status = "FAILED"
                clip.error_message = str(exc)[-4000:]
            session.commit()

        failed = sum(clip.status == "FAILED" for clip in clips.values())
        message = f"Processing complete: {len(clips) - failed} clips ready"
        if failed:
            message += f", {failed} failed (retry clip generation to try again)"
        self._status(session, project, "READY", message)

    def render_clip(self, project_id: str, clip_id: str) -> None:
        """Render one persisted clip request using the downloaded source."""
        with self.make_session() as session:
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
            try:
                if source_path is None:
                    raise RuntimeError("Source video file is missing; clip cannot be rendered")
                duration = project.video.duration if project.video else None
                if duration is not None and (clip.start >= duration or clip.end > duration + 0.1):
                    raise ValueError("Clip timestamps exceed the source video duration")
                output = self.media.render_clip(source_path, root / "clips" / f"{clip.id}.mp4", clip.start, clip.end)
                clip.output_path = str(output)
                clip.status = "READY"
                project.status_message = "Selected clip is ready"
            except Exception as exc:
                clip.status = "FAILED"
                clip.error_message = str(exc)[-4000:]
                project.status_message = "Selected clip rendering failed; retry the clip"
            project.status = "READY"
            session.commit()

    @staticmethod
    def _fail(session: Session, project_id: str, exc: Exception) -> None:
        session.rollback()
        project = session.get(Project, project_id)
        if project is not None:
            project.status = "FAILED"
            project.status_message = "Processing failed"
            project.error_message = str(exc)[-4000:]
            session.commit()

    def render_clips(self, project_id: str) -> None:
        """Explicit backfill/retry for saved moments, never ingest or analyze."""
        with self.make_session() as session:
            try:
                project = session.get(Project, project_id)
                if project is not None:
                    self._render_clips(session, project)
            except Exception as exc:
                self._fail(session, project_id, exc)


def run_pipeline(project_id: str) -> None:
    """Entry point suitable for the existing thread executor."""
    Pipeline().run(project_id)


def render_project_clips(project_id: str) -> None:
    Pipeline().render_clips(project_id)


def render_project_clip(project_id: str, clip_id: str) -> None:
    Pipeline().render_clip(project_id, clip_id)
