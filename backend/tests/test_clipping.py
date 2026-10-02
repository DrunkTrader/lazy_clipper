"""Small rendering smoke and isolated per-clip failure regression checks."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from backend.app.config import Settings
from backend.app.db import init_db, session_factory
from backend.app.models import Clip, Moment, Project, Video
from backend.app.services.media import FFmpegError, MediaService
from backend.app.services.pipeline import Pipeline


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg/ffprobe required")
def test_real_vertical_mp4_render(tmp_path):
    source = tmp_path / "source.mp4"
    subprocess.run([
        "ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "3",
        "-c:v", "libx264", "-threads", "2", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source),
    ], check=True, timeout=30)
    media = MediaService(Settings())
    output = media.render_clip(source, tmp_path / "clips" / "clip.mp4", 0.5, 2)
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


def test_clip_failure_is_isolated_and_retry_keeps_successes(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'clips.db'}"
    settings = Settings(database_url=database_url, storage_dir=tmp_path)
    init_db(database_url)
    sessions = session_factory(database_url)
    with sessions() as session:
        project = Project(source_url="https://youtu.be/dQw4w9WgXcQ", status="READY")
        session.add(project)
        session.flush()
        root = settings.project_storage(project.id)
        source = root / "source" / "video.mp4"
        source.write_bytes(b"source")
        project.video = Video(source_path=str(source), duration=30)
        for rank in range(2):
            project.moments.append(Moment(title=f"Moment {rank}", description="Description", reason="Reason",
                                          start=rank * 10, end=rank * 10 + 5, score=8, rank=rank))
        session.commit()
        project_id = project.id

    class Renderer:
        fail = True
        calls = []

        def render_clip(self, source_path, output_path, start, end):
            self.calls.append(start)
            if start == 0 and self.fail:
                raise FFmpegError("Encoder unavailable")
            output_path.write_bytes(b"rendered clip")
            return output_path

    renderer = Renderer()
    pipeline = Pipeline(settings, media=renderer, make_session=sessions)
    pipeline.render_clips(project_id)
    with sessions() as session:
        project = session.get(Project, project_id)
        assert project.status == "READY"
        clips = session.query(Clip).order_by(Clip.start).all()
        assert [clip.status for clip in clips] == ["FAILED", "READY"]
        assert clips[0].error_message == "Encoder unavailable"
        assert Path(clips[1].output_path).is_file()
        ready_id = clips[1].id
    renderer.fail = False
    pipeline.render_clips(project_id)
    with sessions() as session:
        clips = session.query(Clip).order_by(Clip.start).all()
        assert [clip.status for clip in clips] == ["READY", "READY"]
        assert clips[1].id == ready_id
    assert renderer.calls == [0, 10, 0]
