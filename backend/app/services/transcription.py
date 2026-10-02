"""WhisperX adapter with a lazy import."""
from pathlib import Path
from typing import Any

from ..config import Settings


class TranscriptionError(RuntimeError):
    pass


class WhisperXTranscriber:
    """Small, CPU-safe WhisperX adapter (without diarization or alignment models)."""

    _CPU_THREADS = 2
    _BATCH_SIZE = 1
    _CHUNK_SIZE_SECONDS = 30

    def __init__(self, settings: Settings):
        self.settings = settings
        self._model: Any | None = None

    def _load_model(self, whisperx: Any) -> Any:
        if self._model is None:
            # Silero is bundled/downloadable VAD and does not require gated
            # pyannote credentials.  Explicit threads/batch limits keep the
            # default small model usable in the API container.
            self._model = whisperx.load_model(
                self.settings.whisper_model,
                self.settings.whisper_device,
                compute_type=self.settings.whisper_compute_type,
                vad_method="silero",
                threads=self._CPU_THREADS,
            )
        return self._model

    def transcribe(self, audio_path: Path) -> list[dict[str, Any]]:
        if not audio_path.is_file() or audio_path.stat().st_size == 0:
            raise TranscriptionError(f"Audio file is missing or empty: {audio_path}")
        try:
            import whisperx  # lazy optional heavyweight dependency
        except ImportError as exc:
            raise TranscriptionError("WhisperX is required for transcription") from exc
        try:
            model = self._load_model(whisperx)
            audio = whisperx.load_audio(str(audio_path))
            result = model.transcribe(
                audio,
                batch_size=self._BATCH_SIZE,
                num_workers=0,
                chunk_size=self._CHUNK_SIZE_SECONDS,
                print_progress=False,
            )
            segments = result.get("segments", []) if isinstance(result, dict) else []
            if not isinstance(segments, list):
                raise TranscriptionError("WhisperX returned invalid segments")
            if not segments:
                raise TranscriptionError("WhisperX returned no transcript segments")
            # Keep WhisperX's start/end and optional words untouched for the
            # downstream clipper; no fake fallback transcript is permitted.
            return [dict(segment) for segment in segments]
        except TranscriptionError:
            raise
        except Exception as exc:
            raise TranscriptionError(f"WhisperX transcription failed: {exc}") from exc
