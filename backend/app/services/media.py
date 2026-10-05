"""FFmpeg wrappers for audio extraction and deterministic captioned clips."""
from pathlib import Path
import math
import subprocess

from ..config import Settings


class FFmpegError(RuntimeError):
    pass


class MediaService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _subprocess_timeout(self, budget: float) -> float:
        """Leave a configurable margin for the outer process supervisor.

        The supervisor still owns the complete job deadline and the FFmpeg
        process remains in its process group. This inner deadline only avoids
        waiting right up to the supervisor boundary before surfacing a media
        timeout to the pipeline.
        """
        margin = min(self.settings.media_timeout_margin_seconds, budget / 2)
        return budget - margin

    def extract_audio(self, source_path: Path, audio_path: Path) -> Path:
        if not source_path.is_file():
            raise FFmpegError(f"Source media file is missing: {source_path}")
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        # Remove stale output so a failed invocation cannot expose old audio.
        if audio_path.exists():
            audio_path.unlink()
        command = [
            self.settings.ffmpeg_binary,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(audio_path),
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=self._subprocess_timeout(self.settings.ingestion_timeout_seconds),
            )
        except subprocess.TimeoutExpired as exc:
            if audio_path.exists():
                audio_path.unlink()
            raise FFmpegError("FFmpeg audio extraction timed out") from exc
        except OSError as exc:
            raise FFmpegError(f"Could not execute FFmpeg: {exc}") from exc
        if completed.returncode != 0:
            if audio_path.exists():
                audio_path.unlink()
            detail = (completed.stderr or completed.stdout or "unknown FFmpeg error").strip()[-2000:]
            raise FFmpegError(f"FFmpeg audio extraction failed: {detail}")
        if not audio_path.is_file() or audio_path.stat().st_size == 0:
            raise FFmpegError("FFmpeg completed without producing a non-empty audio file")
        return audio_path

    def render_clip(
        self,
        source_path: Path,
        output_path: Path,
        start: float,
        end: float,
        *,
        subtitle_path: Path | None = None,
    ) -> Path:
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise FFmpegError("Clip timestamps must be finite, with 0 <= start < end")
        if not source_path.is_file() or source_path.stat().st_size == 0:
            raise FFmpegError("Source video is missing or empty")
        if subtitle_path is not None and (not subtitle_path.is_file() or subtitle_path.stat().st_size == 0):
            raise FFmpegError("Caption subtitle file is missing or empty")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        # Only publish a completed MP4; an interrupted render is never reusable.
        temporary = output_path.with_name(output_path.stem + ".partial.mp4")
        temporary.unlink(missing_ok=True)
        video_filter = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1"
        if subtitle_path is not None:
            escaped_subtitle_path = str(subtitle_path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
            video_filter += f",subtitles={escaped_subtitle_path}"
        command = [
            self.settings.ffmpeg_binary, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-abort_on", "empty_output", "-ss", str(start), "-i", str(source_path),
            "-t", str(end - start), "-map", "0:v:0", "-map", "0:a:0?",
            "-vf", video_filter,
            "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-pix_fmt", "yuv420p",
            "-r", "30", "-threads", "2", "-filter_threads", "2",
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(temporary),
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=self._subprocess_timeout(self.settings.clip_timeout_seconds),
            )
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or "unknown FFmpeg error").strip()[-2000:]
                raise FFmpegError(f"FFmpeg clip rendering failed: {detail}")
            if not temporary.is_file() or temporary.stat().st_size == 0:
                raise FFmpegError("FFmpeg completed without producing a non-empty clip")
            temporary.replace(output_path)
        except subprocess.TimeoutExpired as exc:
            raise FFmpegError("FFmpeg clip rendering timed out") from exc
        except OSError as exc:
            raise FFmpegError(f"Could not render clip: {exc}") from exc
        finally:
            temporary.unlink(missing_ok=True)
        return output_path
