"""FFmpeg wrapper for deterministic audio extraction."""
from pathlib import Path
import subprocess

from ..config import Settings


class FFmpegError(RuntimeError):
    pass


class MediaService:
    _FFMPEG_TIMEOUT_SECONDS = 300

    def __init__(self, settings: Settings):
        self.settings = settings

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
                timeout=self._FFMPEG_TIMEOUT_SECONDS,
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
