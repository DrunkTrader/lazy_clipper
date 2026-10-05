from pathlib import Path
import subprocess
import sys
import types

import pytest

from backend.app.config import Settings
from backend.app.services.media import FFmpegError, MediaService
from backend.app.services.transcription import TranscriptionError, WhisperTimestampedTranscriber
from backend.app.services.youtube import (
    InvalidYouTubeURL,
    MediaDependencyError,
    YoutubeService,
    canonical_youtube_url,
)

VIDEO_ID = "abc123_XYzz"
VIDEO_URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"


def test_canonical_url_and_strict_validation():
    assert canonical_youtube_url(f"https://youtu.be/{VIDEO_ID}?t=30") == VIDEO_URL
    with pytest.raises(InvalidYouTubeURL):
        canonical_youtube_url("https://www.youtube.com/watch?v=abc123")


def test_youtube_options_use_node_and_optional_cookie_file(tmp_path):
    settings = Settings(ytdlp_js_runtime="node", ytdlp_cookie_file=tmp_path / "cookies.txt")
    service = YoutubeService(settings)
    options = service._options(skip_download=True)
    assert options["js_runtimes"] == {"node": {}}
    assert "cookiefile" not in options

    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("# test cookie file\n", encoding="utf-8")
    options = service._options(skip_download=True)
    assert options["cookiefile"] == str(cookie_file)

    with service._options_context(skip_download=True) as runtime_options:
        runtime_cookie = Path(runtime_options["cookiefile"])
        assert runtime_cookie != cookie_file
        assert runtime_cookie.read_text(encoding="utf-8") == cookie_file.read_text(encoding="utf-8")
        assert runtime_cookie.is_file()
    assert not Path(runtime_options["cookiefile"]).exists()


def test_youtube_metadata_uses_canonical_url_and_rejects_live(monkeypatch):
    calls = {}

    class FakeDownloader:
        def __init__(self, options):
            calls["options"] = options

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download):
            calls["url"] = url
            calls["download"] = download
            return {"id": VIDEO_ID, "title": "Speech", "duration": 12, "thumbnail": None}

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=FakeDownloader))
    metadata = YoutubeService().get_metadata(f"https://youtu.be/{VIDEO_ID}?si=tracking")
    assert metadata.youtube_id == VIDEO_ID
    assert calls["url"] == VIDEO_URL
    assert calls["download"] is False
    assert calls["options"]["retries"] == 2
    assert calls["options"]["noplaylist"] is True


def test_youtube_rejects_live_metadata(monkeypatch):
    class LiveDownloader:
        def __init__(self, options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download):
            return {"id": VIDEO_ID, "is_live": True}

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=LiveDownloader))
    with pytest.raises(MediaDependencyError, match="Live streams"):
        YoutubeService().get_metadata(VIDEO_URL)


def test_youtube_download_only_returns_complete_mp4_and_cleans_failure(monkeypatch, tmp_path):
    destination = tmp_path / "source"
    calls = {}

    class FakeDownloader:
        def __init__(self, options):
            calls["options"] = options

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download):
            calls["url"] = url
            (destination / "video.mp4").write_bytes(b"mp4")
            return {"id": VIDEO_ID}

        def prepare_filename(self, info):
            return str(destination / "video.mp4")

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=FakeDownloader))
    result = YoutubeService().download_video(VIDEO_URL, destination)
    assert result == destination / "video.mp4"
    assert calls["options"]["max_filesize"] > 0
    assert "avc1" in calls["options"]["format"]

    class FailingDownloader(FakeDownloader):
        def extract_info(self, url, download):
            (destination / "video.mp4.part").write_bytes(b"partial")
            raise RuntimeError("network failed")

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=FailingDownloader))
    with pytest.raises(MediaDependencyError):
        YoutubeService().download_video(VIDEO_URL, destination)
    assert not list(destination.glob("video.*"))


def test_whisper_timestamped_preserves_word_timestamps(monkeypatch, tmp_path):
    calls = {}

    def fake_transcribe(model, audio, **options):
        calls["transcribe"] = options
        return {
            "segments": [{
                "start": 1.25,
                "end": 2.5,
                "text": "hello",
                "words": [{"text": "hello", "start": 1.25, "end": 2.5, "confidence": 0.9}],
            }],
        }

    fake_whisper = types.SimpleNamespace(
        load_model=lambda *args, **options: calls.setdefault("load", (args, options)) and object(),
        transcribe=fake_transcribe,
    )
    monkeypatch.setitem(sys.modules, "whisper_timestamped", fake_whisper)
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"wav")
    segments = WhisperTimestampedTranscriber(Settings()).transcribe(audio_path)
    assert segments[0]["start"] == 1.25
    assert segments[0]["words"][0]["text"] == "hello"
    assert calls["transcribe"]["compute_word_confidence"] is True
    assert calls["transcribe"]["vad"] is False
    assert calls["transcribe"]["fp16"] is False


def test_whisper_timestamped_requires_real_audio(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "whisper_timestamped", types.SimpleNamespace())
    with pytest.raises(TranscriptionError, match="missing or empty"):
        WhisperTimestampedTranscriber(Settings()).transcribe(tmp_path / "missing.wav")


def test_whisper_timestamped_rejects_empty_transcript(monkeypatch, tmp_path):
    fake_whisper = types.SimpleNamespace(
        load_model=lambda *args, **options: object(),
        transcribe=lambda *args, **options: {"segments": []},
    )
    monkeypatch.setitem(sys.modules, "whisper_timestamped", fake_whisper)
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"wav")
    with pytest.raises(TranscriptionError, match="no transcript segments"):
        WhisperTimestampedTranscriber(Settings()).transcribe(audio_path)


def test_ffmpeg_timeouts_follow_job_budgets_and_remove_stale_output(monkeypatch, tmp_path):
    source = tmp_path / "video.mp4"
    output = tmp_path / "audio.wav"
    source.write_bytes(b"video")
    output.write_bytes(b"stale")
    calls = {}
    settings = Settings(_env_file=None, ingestion_timeout_seconds=600, clip_timeout_seconds=720,
                        media_timeout_margin_seconds=30)

    def fake_run(command, **kwargs):
        calls["command"] = command
        calls["kwargs"] = kwargs
        output.write_bytes(b"wav")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert MediaService(settings).extract_audio(source, output) == output
    assert "-nostdin" in calls["command"]
    assert calls["kwargs"]["timeout"] == 570

    render_output = tmp_path / "rendered.mp4"

    def fake_render(command, **kwargs):
        calls["render_kwargs"] = kwargs
        Path(command[-1]).write_bytes(b"mp4")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_render)
    assert MediaService(settings).render_clip(source, render_output, 0, 1) == render_output
    assert calls["render_kwargs"]["timeout"] == 690

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(FFmpegError, match="timed out"):
        MediaService(settings).extract_audio(source, output)
    assert not output.exists()
