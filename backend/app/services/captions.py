"""Stored word selection and simple ASS caption generation."""
from pathlib import Path
import math
from typing import Any, Iterable

from ..analysis.transcript import require_word_alignment


class CaptionError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def require_clip_alignment(segments: list[Any], start: float, end: float) -> None:
    """Check the requested speech range; absent speech is handled separately."""
    if not segments:
        raise CaptionError("CAPTION_ALIGNMENT_MISSING", "No saved transcript is available for caption selection")
    overlapping = [segment for segment in segments
                   if float(_value(segment, "start", 0)) < end and float(_value(segment, "end", 0)) > start]
    try:
        require_word_alignment(overlapping)
    except ValueError as exc:
        raise CaptionError("CAPTION_ALIGNMENT_MISSING", "Saved speech has no usable word alignment") from exc


ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,Arial,80,&H00FFFFFF,&H00FFFFFF,&H00101010,&H99000000,-1,0,0,0,100,100,0,0,3,2,1,2,80,80,170,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def select_words(segments: Iterable[Any], start: float, end: float) -> list[dict[str, Any]]:
    """Return stored words overlapping [start, end), clamped to that range."""
    selected: list[dict[str, Any]] = []
    for segment in segments:
        for word in _value(segment, "words", []) or []:
            raw_text = _value(word, "word", _value(word, "text", ""))
            text = raw_text.strip() if isinstance(raw_text, str) else ""
            try:
                word_start = float(_value(word, "start"))
                word_end = float(_value(word, "end"))
            except (TypeError, ValueError):
                continue
            if not math.isfinite(word_start) or not math.isfinite(word_end):
                continue
            if not text or word_end <= start or word_start >= end or word_end <= word_start:
                continue
            selected.append({
                "word": text,
                "start": max(start, word_start),
                "end": min(end, word_end),
            })
    return sorted(selected, key=lambda word: (word["start"], word["end"]))


def _ass_time(seconds: float) -> str:
    centiseconds = max(0, round(seconds * 100))
    hours, remainder = divmod(centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    seconds, centiseconds = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centiseconds:02d}"


def _escape_ass_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def build_ass(words: list[dict[str, Any]], clip_start: float, clip_end: float, *, window_size: int = 4) -> str:
    """Build one short sliding word window per spoken word."""
    lines = [ASS_HEADER.rstrip("\n")]
    for index, word in enumerate(words):
        relative_start = max(0.0, float(word["start"]) - clip_start)
        relative_end = min(clip_end - clip_start, float(word["end"]) - clip_start)
        if relative_end <= relative_start or round(relative_end * 100) <= round(relative_start * 100):
            continue
        window_start = max(0, min(index - 1, len(words) - window_size))
        window_end = min(len(words), window_start + window_size)
        displayed = []
        for displayed_index in range(window_start, window_end):
            color = "{\\c&H0000BFFF&}" if displayed_index == index else "{\\c&H00FFFFFF&}"
            displayed.append(color + _escape_ass_text(str(words[displayed_index]["word"])))
        lines.append(
            f"Dialogue: 0,{_ass_time(relative_start)},{_ass_time(relative_end)},Caption,,0,0,0,,{' '.join(displayed)}"
        )
    if len(lines) == 1:
        raise CaptionError("CAPTION_NO_SPEECH", "No caption dialogue events exist for the requested range")
    return "\n".join(lines) + "\n"


def write_ass(path: Path, words: list[dict[str, Any]], clip_start: float, clip_end: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_ass(words, clip_start, clip_end), encoding="utf-8")
    return path
