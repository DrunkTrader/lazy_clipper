"""Admission and real process cancellation regressions; no external providers."""
import os
from pathlib import Path
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic, sleep

import pytest

from backend.tests.test_api import api_client  # noqa: F401
from backend.tests.test_recovery import runtime, saved_project  # noqa: F401


def test_deadline_kills_stubborn_process_and_its_descendant(tmp_path):
    from backend.app.services.execution import JobTimeout, run_process

    ready = tmp_path / "pids"
    late = tmp_path / "late-write"
    descendant = (
        "import os,signal,time,pathlib; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"pathlib.Path({str(ready)!r}).write_text(str(os.getpid())); "
        f"time.sleep(3); pathlib.Path({str(late)!r}).write_text('still running'); time.sleep(60)"
    )
    command = [sys.executable, "-c", (
        "import signal,subprocess,sys,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"subprocess.Popen([sys.executable, '-c', {descendant!r}]); time.sleep(60)"
    )]
    start = monotonic()
    with pytest.raises(JobTimeout):
        run_process(command, timeout=1, stop=Event(), grace=0.1)
    assert monotonic() - start < 3
    assert ready.exists(), "The descendant must actually start before the deadline"
    pid = int(ready.read_text())
    # A killed orphan may await PID 1 reaping; a zombie cannot run/write.
    stat = Path(f"/proc/{pid}/stat")
    assert not stat.exists() or stat.read_text().split(") ")[1].startswith("Z ")
    assert not late.exists()


def test_job_child_environment_excludes_application_credentials(monkeypatch, tmp_path):
    from backend.app.services.execution import child_environment

    monkeypatch.setenv("DATABASE_URL", "postgresql://private")
    monkeypatch.setenv("DATABASE_PASSWORD_FILE", "/run/secrets/database")
    monkeypatch.setenv("LLM_API_KEY", "private-key")
    monkeypatch.setenv("YTDLP_COOKIE_FILE", "/app/cookies.txt")
    environment = child_environment(str(tmp_path), 1234)
    assert environment["TMPDIR"] == str(tmp_path)
    assert environment["LAZYCLIPPER_PARENT_PID"] == "1234"
    assert "DATABASE_URL" not in environment
    assert "DATABASE_PASSWORD_FILE" not in environment
    assert "LLM_API_KEY" not in environment
    assert "YTDLP_COOKIE_FILE" not in environment


def test_shutdown_stops_active_process_before_returning(tmp_path):
    from backend.app.services.execution import JobInterrupted, run_process

    stop = Event()
    ready = tmp_path / "ready"
    command = [sys.executable, "-c", (
        "import pathlib,time,os; "
        f"pathlib.Path({str(ready)!r}).write_text(str(os.getpid())); time.sleep(60)"
    )]
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(run_process, command, timeout=30, stop=stop, grace=0.1)
        deadline = monotonic() + 5
        while not ready.exists() and monotonic() < deadline:
            sleep(0.01)
        assert ready.exists()
        stop.set()
        with pytest.raises(JobInterrupted):
            future.result(timeout=3)
    with pytest.raises(ProcessLookupError):
        os.kill(int(ready.read_text()), 0)


def test_api_parent_hard_exit_kills_guard_job_and_descendants(tmp_path):
    from backend.app.services.execution import _group_running

    ready, group = tmp_path / "ready", tmp_path / "group"
    descendant = (
        "import pathlib,signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"pathlib.Path({str(ready)!r}).write_text('started'); time.sleep(60)"
    )
    job = (
        "import signal,time,subprocess,sys; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"subprocess.Popen([sys.executable, '-c', {descendant!r}]); time.sleep(60)"
    )
    guard = (
        "import sys; from backend.app.services.job_guard import guard; "
        f"sys.exit(guard([sys.executable, '-c', {job!r}], int(sys.argv[1])))"
    )
    parent = (
        "import pathlib,subprocess,os,sys,time; "
        f"child=subprocess.Popen([sys.executable, '-c', {guard!r}, str(os.getpid())], start_new_session=True); "
        f"pathlib.Path({str(group)!r}).write_text(str(child.pid)); time.sleep(60)"
    )
    process = subprocess.Popen([sys.executable, "-c", parent])
    try:
        deadline = monotonic() + 5
        while not ready.exists() and monotonic() < deadline:
            sleep(0.01)
        assert ready.exists()
        process.kill()  # No supervisor finally/shutdown callback gets to run.
        process.wait(timeout=2)
        group_id = int(group.read_text())
        deadline = monotonic() + 3
        while _group_running(group_id) and monotonic() < deadline:
            sleep(0.01)
        assert not _group_running(group_id)
    finally:
        process.kill()
        process.wait(timeout=2)
        if group.exists():
            import signal
            try:
                os.killpg(int(group.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_admission_counts_reservations_queue_active_and_releases_failures():
    from backend.app.services.admission import Admission, QueueFull

    admission = Admission(workers=2, waiting=1)
    started, release = Event(), Event()

    def work():
        started.set()
        assert release.wait(5)

    with ThreadPoolExecutor(max_workers=1) as executor:
        try:
            with admission.reserve() as first:
                active = first.submit(executor, work)
            assert started.wait(2)
            with admission.reserve() as second:
                queued = second.submit(executor, lambda: None)
            with admission.reserve():
                with pytest.raises(QueueFull):
                    with admission.reserve():
                        pytest.fail("admitted beyond capacity")
                state = admission.snapshot()
                assert (state["active"], state["queued"], state["reserved"]) == (1, 1, 1)
                assert state["available"] == 0
                assert state["oldest_queued_seconds"] >= 0
            assert queued.cancel()
            assert admission.snapshot()["available"] == 2
        finally:
            release.set()
        active.result(timeout=2)
    assert admission.snapshot()["available"] == 3
    with pytest.raises(RuntimeError):
        with admission.reserve() as slot:
            slot.submit(executor, lambda: None)  # Already shut down.
    assert admission.snapshot()["available"] == 3


def test_full_queue_rejects_ingest_without_persistence_and_duplicates_still_work(api_client):  # noqa: F811
    from contextlib import ExitStack
    from backend.app.main import app

    first = api_client.post("/api/v1/ingest", json={"url": "https://youtu.be/dQw4w9WgXcQ"})
    assert first.status_code == 202
    admission = app.state.admission
    with ExitStack() as stack:
        while admission.snapshot()["available"]:
            stack.enter_context(admission.reserve())
        response = api_client.post("/api/v1/ingest", json={"url": "https://youtu.be/abcdefghijk"})
        assert response.status_code == 429
        assert response.headers["retry-after"] == "5"
        assert response.json()["error"]["code"] == "PROCESSING_BUSY"
        assert len(api_client.get("/api/v1/projects").json()) == 1
        duplicate = api_client.post("/api/v1/ingest", json={"url": "https://youtu.be/dQw4w9WgXcQ"})
        assert duplicate.json()["project_id"] == first.json()["project_id"]
        state = api_client.get("/api/v1/processing").json()
        assert state["available"] == 0 and state["capacity"] == 6


@pytest.mark.parametrize("duration", [None, float("nan"), float("inf"), 0, -1, 7201])
def test_source_policy_rejects_unknown_or_excessive_duration(duration):
    from backend.app.config import Settings
    from backend.app.services.youtube import YoutubeService

    service = YoutubeService(Settings(_env_file=None))
    reject = service._options(skip_download=False)["match_filter"]
    assert reject({"duration": duration}, incomplete=False)
    assert reject({"duration": 7200}, incomplete=False) is None
    assert reject({}, incomplete=True) is None


def test_clip_limit_and_full_queue_leave_existing_project_untouched(api_client):  # noqa: F811
    from contextlib import ExitStack
    from backend.app.config import get_settings
    from backend.app.db import session_factory
    from backend.app.main import app
    from backend.app.models import Clip, Moment, Project, Video

    with session_factory()() as session:
        project = Project(source_url="https://youtu.be/dQw4w9WgXcQ", status="READY")
        session.add(project)
        session.flush()
        source = get_settings().project_storage(project.id) / "source" / "video.mp4"
        source.write_bytes(b"saved source")
        project.video = Video(duration=1000, source_path=str(source))
        moment = Moment(title="Saved", description="Saved", reason="Saved", start=0, end=20, score=8)
        project.moments.append(moment)
        session.commit()
        session.refresh(project)
        project_id, moment_id, updated = project.id, moment.id, project.updated_at
    path = f"/api/v1/projects/{project_id}/clips"
    response = api_client.post(path, json={"moment_id": moment_id, "start": 0, "end": 181})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "CLIP_TOO_LONG"
    with ExitStack() as stack:
        while app.state.admission.snapshot()["available"]:
            stack.enter_context(app.state.admission.reserve())
        response = api_client.post(path, json={"moment_id": moment_id, "start": 0, "end": 20})
        assert response.status_code == 429
    with session_factory()() as session:
        assert session.get(Project, project_id).updated_at == updated
        assert session.get(Project, project_id).status == "READY"
        assert session.query(Clip).count() == 0


def test_supervised_real_child_reuses_saved_stages(runtime):  # noqa: F811
    from backend.app.models import Project
    from backend.app.services.execution import JobSupervisor

    project_id, moment_id, _, root = saved_project(runtime, "QUEUED")
    supervisor = JobSupervisor(runtime.settings)
    supervisor.run(project_id)
    assert not supervisor.stop.is_set()
    with runtime.sessions() as session:
        project = session.get(Project, project_id)
        assert project.status == "READY"
        assert [moment.id for moment in project.moments] == [moment_id]
        assert len(project.transcript_segments) == 1
        assert (root / "source" / "video.mp4").read_bytes() == b"saved artifact"


def test_real_hung_transcriber_is_killed_before_failure_persistence(runtime, monkeypatch, tmp_path):  # noqa: F811
    from backend.app.models import Project
    from backend.app.services import execution

    project_id, _, _, root = saved_project(runtime, "QUEUED")
    with runtime.sessions() as session:
        project = session.get(Project, project_id)
        project.transcript_segments.clear()
        session.commit()
    temporary_marker = tmp_path / "job-temporary"
    script = (
        "import json,sys,time,os,pathlib; "
        "from backend.app.config import Settings; "
        "from backend.app.services.pipeline import Pipeline; "
        "from backend.app.db import session_factory; "
        "settings=Settings(_env_file=None, **json.load(sys.stdin)); "
        f"pathlib.Path({str(temporary_marker)!r}).write_text(os.environ['TMPDIR']); "
        "stalled=type('Stalled', (), {'transcribe': lambda self, path: time.sleep(60)})(); "
        "Pipeline(settings, transcriber=stalled, make_session=session_factory(settings.database_url)).run(sys.argv[1])"
    )
    real_run = execution.run_process

    def stalled_process(command, **kwargs):
        real_run([sys.executable, "-c", script, project_id], **kwargs)

    monkeypatch.setattr(execution, "run_process", stalled_process)
    settings = runtime.settings.model_copy(update={"ingestion_timeout_seconds": 2})
    execution.JobSupervisor(settings).run(project_id)
    assert temporary_marker.exists()
    assert not Path(temporary_marker.read_text()).exists()
    with runtime.sessions() as session:
        project = session.get(Project, project_id)
        assert project.status == "FAILED"
        assert project.error_message.startswith("Transcription exceeded its time limit")
        assert project.transcript_segments == []
        assert project.moments  # Completed independent stages are preserved.
    assert (root / "source" / "video.mp4").exists()
    assert (root / "audio" / "audio.wav").exists()


@pytest.mark.parametrize("cause", ["timeout", "interrupted", "crash"])
def test_stopped_clip_failure_is_isolated_and_cleans_only_temporary_files(runtime, monkeypatch, cause):  # noqa: F811
    from backend.app.models import Clip, Project
    from backend.app.services import execution

    project_id, _, clip_id, root = saved_project(runtime, "RENDERING", "RENDERING")
    for suffix in (".ass", ".partial.mp4"):
        (root / "clips" / f"{clip_id}{suffix}").write_bytes(b"unfinished")
    errors = {"timeout": execution.JobTimeout, "interrupted": execution.JobInterrupted, "crash": RuntimeError}

    def fail(*args, **kwargs):
        raise errors[cause]("synthetic job failure")

    monkeypatch.setattr(execution, "run_process", fail)
    execution.JobSupervisor(runtime.settings).run(project_id, clip_id)
    with runtime.sessions() as session:
        assert session.get(Project, project_id).status == "READY"
        clip = session.get(Clip, clip_id)
        assert clip.status == "FAILED" and clip.output_path is None
        assert "synthetic" not in clip.error_message
        assert session.get(Project, project_id).transcript_segments
    assert (root / "clips" / f"{clip_id}.mp4").exists()
    assert not (root / "clips" / f"{clip_id}.ass").exists()
    assert not (root / "clips" / f"{clip_id}.partial.mp4").exists()


def test_unconfirmed_termination_disables_admission_without_publishing_retry(runtime, monkeypatch):  # noqa: F811
    from backend.app.models import Project
    from backend.app.services import execution

    project_id, _, _, _ = saved_project(runtime, "TRANSCRIBING")

    def unkillable(*args, **kwargs):
        raise execution.TerminationFailure("synthetic termination failure")

    monkeypatch.setattr(execution, "run_process", unkillable)
    supervisor = execution.JobSupervisor(runtime.settings)
    from backend.app.services.admission import Admission, QueueFull

    admission = Admission(workers=2, waiting=4)
    with ThreadPoolExecutor(max_workers=2) as executor:
        with admission.reserve() as slot:
            future = slot.submit(executor, supervisor.run, project_id)
        with pytest.raises(execution.TerminationFailure):
            future.result(timeout=2)
    assert supervisor.stop.is_set()
    assert admission.snapshot()["quarantined"] == 1
    assert admission.snapshot()["available"] == 0
    with pytest.raises(QueueFull):
        with admission.reserve():
            pytest.fail("Unconfirmed termination must prevent further admission")
    with runtime.sessions() as session:
        assert session.get(Project, project_id).status == "TRANSCRIBING"


def test_source_policy_prevents_download_and_persists_safe_failure(runtime):  # noqa: F811
    from backend.app.models import Project
    from backend.app.services.pipeline import Pipeline
    from backend.app.services.youtube import VideoMetadata

    class TooLong:
        def get_metadata(self, url):
            return VideoMetadata("dQw4w9WgXcQ", "Long", 7201, None)

        def download_video(self, *args):
            pytest.fail("Download must not start after rejected metadata")

    with runtime.sessions() as session:
        project = Project(source_url="https://youtu.be/dQw4w9WgXcQ")
        session.add(project)
        session.commit()
        project_id = project.id
    Pipeline(runtime.settings, youtube=TooLong(), make_session=runtime.sessions).run(project_id)
    with runtime.sessions() as session:
        project = session.get(Project, project_id)
        assert project.status == "FAILED"
        assert "source duration" in project.error_message.lower()
        assert project.video is None


def test_submission_lock_wait_is_bounded_without_persistence(api_client, monkeypatch):  # noqa: F811
    from backend.app.api.routes import _submission_lock
    from backend.app.config import get_settings

    monkeypatch.setattr(get_settings(), "submission_lock_timeout_seconds", 0.05)
    with _submission_lock:
        before = monotonic()
        response = api_client.post("/api/v1/ingest", json={"url": "https://youtu.be/dQw4w9WgXcQ"})
        assert monotonic() - before < 1
    assert response.status_code == 429
    assert api_client.get("/api/v1/projects").json() == []


def test_storage_budget_rejects_before_persisting_or_dispatching(api_client, monkeypatch):  # noqa: F811
    from backend.app.api import routes
    from backend.app.errors import CLIENT_MESSAGES

    def full(*args, **kwargs):
        from backend.app.services.storage import StorageBudgetError
        raise StorageBudgetError("synthetic full disk")

    monkeypatch.setattr(routes, "check_capacity", full)
    response = api_client.post("/api/v1/ingest", json={"url": "https://youtu.be/dQw4w9WgXcQ"})
    assert response.status_code == 507
    assert response.json()["error"]["code"] == "STORAGE_BUDGET"
    assert response.json()["error"]["message"] == CLIENT_MESSAGES["STORAGE_BUDGET"]
    assert api_client.get("/api/v1/projects").json() == []


def test_terminal_child_status_cannot_be_retried_before_supervisor_releases_ownership(api_client, monkeypatch):  # noqa: F811
    from concurrent.futures import Future
    from backend.app.db import session_factory
    from backend.app.main import app
    from backend.app.models import Project

    accepted = []

    def hold(*args):
        future = Future()
        accepted.append(future)
        return future

    monkeypatch.setattr(app.state.pipeline_executor, "submit", hold)
    payload = {"url": "https://youtu.be/dQw4w9WgXcQ"}
    project_id = api_client.post("/api/v1/ingest", json=payload).json()["project_id"]
    with session_factory()() as session:
        project = session.get(Project, project_id)
        project.status = "FAILED"  # Child's terminal commit, before its exit/cleanup.
        session.commit()
    assert api_client.post("/api/v1/ingest", json=payload).status_code == 409
    assert len(accepted) == 1
    with session_factory()() as session:
        assert session.get(Project, project_id).status == "FAILED"
    accepted[0].set_result(None)  # Represents confirmed exit and completed reconciliation.
    assert api_client.post("/api/v1/ingest", json=payload).status_code == 202
    assert len(accepted) == 2


def test_postgres_engine_sets_connect_statement_and_lock_deadlines(monkeypatch):
    from backend.app import db
    from backend.app.config import Settings

    settings = Settings(_env_file=None, database_url="postgresql+psycopg://app@database/test")
    captured = {}

    def create_engine(url, **kwargs):
        captured.update(kwargs)
        return object()

    db.get_engine.cache_clear()
    monkeypatch.setattr(db, "get_settings", lambda: settings)
    monkeypatch.setattr(db, "create_engine", create_engine)
    try:
        db.get_engine()
        options = captured["connect_args"]
        assert options["connect_timeout"] == 5
        assert options["options"] == "-c statement_timeout=10000 -c lock_timeout=3000"
        assert options["tcp_user_timeout"] == 10000
        assert captured["pool_timeout"] == 5
    finally:
        db.get_engine.cache_clear()
