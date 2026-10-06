"""Small rendering smoke and isolated per-clip failure regression checks."""
import json
import shutil
import subprocess

import pytest

from backend.app.config import Settings
from backend.app.services.captions import write_ass
from backend.app.services.media import FFmpegError, MediaService


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg/ffprobe required")
def test_real_vertical_mp4_render(tmp_path):
    source = tmp_path / "source.mp4"
    subprocess.run([
        "ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "3",
        "-c:v", "libx264", "-threads", "2", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source),
    ], check=True, timeout=30)
    media = MediaService(Settings())
    subtitle = write_ass(tmp_path / "captions.ass", [
        {"word": "captions", "start": 0.5, "end": 1.0},
        {"word": "work", "start": 1.0, "end": 1.5},
    ], 0.5, 2)
    output = media.render_clip(source, tmp_path / "clips" / "clip.mp4", 0.5, 2, subtitle_path=subtitle)
    probe = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(output),
    ], timeout=30))
    video = next(stream for stream in probe["streams"] if stream["codec_type"] == "video")
    audio = next(stream for stream in probe["streams"] if stream["codec_type"] == "audio")
    assert (video["width"], video["height"], video["codec_name"]) == (1080, 1920, "h264")
    assert video["pix_fmt"] == "yuv420p"
    assert audio["codec_name"] == "aac"
    assert abs(float(probe["format"]["duration"]) - 1.5) < 0.15
    assert not list(output.parent.glob("*.partial.mp4"))
    with pytest.raises(FFmpegError):
        media.render_clip(source, tmp_path / "invalid.mp4", 2, 1)
    with pytest.raises(FFmpegError):
        media.render_clip(source, tmp_path / "empty.mp4", 10, 12)
    assert not (tmp_path / "empty.mp4").exists()
