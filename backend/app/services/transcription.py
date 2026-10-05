"""Word-timestamped Whisper adapter with a lazy import."""
from pathlib import Path
from typing import Any

from ..config import Settings
from ..analysis.transcript import require_word_alignment


class TranscriptionError(RuntimeError):
    pass


class WhisperTimestampedTranscriber:
    """Small, CPU-safe whisper-timestamped adapter for the full source audio."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._model: Any | None = None

    def _load_model(self, whisper: Any) -> Any:
        if self._model is None:
            self._model = whisper.load_model(
                self.settings.whisper_model,
                self.settings.whisper_device,
            )
        return self._model

    def transcribe(self, audio_path: Path) -> list[dict[str, Any]]:
        if not audio_path.is_file() or audio_path.stat().st_size == 0:
            raise TranscriptionError(f"Audio file is missing or empty: {audio_path}")
        try:
            import whisper_timestamped as whisper  # lazy optional heavyweight dependency
        except ImportError as exc:
            raise TranscriptionError("whisper-timestamped is required for transcription") from exc
        try:
            model = self._load_model(whisper)
            result = whisper.transcribe(
                model,
                str(audio_path),
                vad=False,
                compute_word_confidence=True,
                fp16=self.settings.whisper_device != "cpu",
                temperature=0.0,
                verbose=False,
            )
            segments = result.get("segments", []) if isinstance(result, dict) else []
            if not isinstance(segments, list):
                raise TranscriptionError("whisper-timestamped returned invalid segments")
            if not segments:
                raise TranscriptionError("whisper-timestamped returned no transcript segments")
            require_word_alignment(segments)
            # whisper-timestamped supplies per-word text/start/end values. Keep
            # the complete segment objects so normalization can persist them.
            return [dict(segment) for segment in segments]
        except TranscriptionError:
            raise
        except Exception as exc:
            raise TranscriptionError(f"whisper-timestamped transcription failed: {exc}") from exc


# Keep the original import name available to callers from the initial MVP.
WhisperXTranscriber = WhisperTimestampedTranscriber
