"""POSIX process-group deadlines, supervised by the existing two threads."""
import os
from pathlib import Path
import signal
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic, sleep


class JobTimeout(TimeoutError):
    pass


class JobInterrupted(RuntimeError):
    pass


class TerminationFailure(RuntimeError):
    """Fail closed: do not retry or persist completion while work may still run."""


def _signal_group(process: subprocess.Popen, sig: int) -> None:
    try:
        os.killpg(process.pid, sig)
    except ProcessLookupError:
        pass


def _group_running(group_id: int) -> bool:
    # killpg returning only confirms signal delivery, not descendant exit. Linux
    # /proc lets us wait for runnable members as well as reaping the direct child.
    # Orphan zombies are harmless and are reaped by Compose's init process.
    for path in Path("/proc").glob("[0-9]*/stat"):
        try:
            fields = path.read_text().rsplit(") ", 1)[1].split()
        except (FileNotFoundError, ProcessLookupError):
            continue
        if int(fields[2]) == group_id and fields[0] not in {"Z", "X"}:
            return True
    return False


def run_process(
    command: list[str], *, timeout: float, stop: Event, grace: float = 2,
    input_data: bytes | None = None, env: dict[str, str] | None = None,
) -> None:
    """Stop the whole group and reap its leader before releasing worker ownership.

    All supported job descendants (FFmpeg, yt-dlp JS runtimes) inherit this
    group. No preexec_fn/fork of Python's threaded runtime is used. Standard
    streams inherit the service logs; credentials travel only through stdin.
    """
    if sys.platform != "linux":
        raise RuntimeError("Supervised processing requires Linux (or the Compose Linux image)")
    if stop.is_set():
        raise JobInterrupted("Processing is stopping")
    deadline = monotonic() + timeout
    process = subprocess.Popen(command, stdin=subprocess.PIPE, start_new_session=True, env=env)
    try:
        while True:
            if stop.is_set():
                raise JobInterrupted("Processing is stopping")
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise JobTimeout("Job execution deadline exceeded")
            try:
                process.communicate(input=input_data, timeout=min(0.2, remaining))
                break
            except subprocess.TimeoutExpired:
                # communicate retains any unsent input across timeout calls.
                input_data = None
        if process.returncode:
            raise RuntimeError(f"Job process exited with code {process.returncode}")
    finally:
        # Signal even if the leader exited: descendants must not outlive a job.
        try:
            _signal_group(process, signal.SIGTERM)
            try:
                process.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                pass
            _signal_group(process, signal.SIGKILL)
            process.wait(timeout=5)
            stopped_by = monotonic() + 5
            while _group_running(process.pid):
                if monotonic() >= stopped_by:
                    raise subprocess.TimeoutExpired(command, 5)
                sleep(0.01)
        except Exception as exc:
            stop.set()
            raise TerminationFailure("Job termination could not be confirmed; processing disabled until restart") from exc
        finally:
            if process.stdin:
                process.stdin.close()


class JobSupervisor:
    def __init__(self, settings):
        self.settings = settings
        self.stop = Event()

    def ingest(self, project_id: str) -> None:
        self.run(project_id)

    def render(self, project_id: str, clip_id: str) -> None:
        self.run(project_id, clip_id)

    def run(self, project_id: str, clip_id: str | None = None) -> None:
        from ..logging import log_failure

        failure = None
        try:
            # Own the child's transient files (including writable cookie copies)
            # so hard cancellation can clean them without the child's finally.
            with TemporaryDirectory(prefix="lazyclipper-job-") as temporary:
                command = [sys.executable, "-m", "backend.app.services.job_guard", project_id]
                if clip_id:
                    command.append(clip_id)
                run_process(
                    command,
                    timeout=self.settings.clip_timeout_seconds if clip_id else self.settings.ingestion_timeout_seconds,
                    stop=self.stop,
                    input_data=self.settings.model_dump_json().encode(),
                    env={**os.environ, "TMPDIR": temporary, "LAZYCLIPPER_PARENT_PID": str(os.getpid())},
                )
        except TerminationFailure as exc:
            # Do not publish retryability if termination could not be confirmed.
            self.stop.set()
            log_failure(exc, project_id=project_id, clip_id=clip_id, stage="unknown", context="SUPERVISOR")
            raise
        except Exception as exc:
            failure = exc
            log_failure(exc, project_id=project_id, clip_id=clip_id,
                        stage="rendering" if clip_id else "ingestion", context="SUPERVISOR")
        self._finish(project_id, clip_id, failure)

    def _finish(self, project_id: str, clip_id: str | None, failure: Exception | None) -> None:
        """Only reconcile this stopped job, preserving stages and terminal success."""
        from ..db import session_factory
        from ..errors import INTERRUPTED_MESSAGES, TIMEOUT_MESSAGES, processing_error
        from ..logging import log_failure
        from ..models import Clip, Project

        try:
            with session_factory(self.settings.database_url)() as session:
                project = session.get(Project, project_id)
                if project is None:
                    return
                clip = session.get(Clip, clip_id) if clip_id else None
                if clip_id:
                    if clip is None or clip.project_id != project_id or clip.status not in {"QUEUED", "RENDERING"}:
                        return
                    stage = "rendering"
                else:
                    if project.status not in {"QUEUED", "INGESTING", "TRANSCRIBING", "ANALYZING"}:
                        return
                    stage = {"TRANSCRIBING": "transcription", "ANALYZING": "analysis"}.get(project.status, "ingestion")
                if isinstance(failure, JobTimeout):
                    message = TIMEOUT_MESSAGES[stage]
                elif isinstance(failure, JobInterrupted):
                    message = INTERRUPTED_MESSAGES[stage]
                else:
                    message = processing_error(stage).message
                # A child can exit without a terminal commit if persistence failed.
                # This check also covers process crashes and import/startup errors.
                self._cleanup_unfinished(project, clip)
                if clip:
                    clip.status = "FAILED"
                    clip.output_path = None
                    clip.error_message = message
                    if project.status == "RENDERING":
                        project.status = "READY"
                        project.status_message = message
                else:
                    project.status = "FAILED"
                    project.error_message = project.status_message = message
                session.commit()
        except Exception as exc:
            # A database outage must not turn into further accepted orphan work.
            # Startup recovery remains the fallback after service restoration.
            self.stop.set()
            log_failure(exc, project_id=project_id, clip_id=clip_id, stage="database", context="PERSIST_FAILURE")

    def _cleanup_unfinished(self, project, clip) -> None:
        root = (self.settings.storage_dir / "projects" / project.id).resolve()
        if clip:
            paths = [root / "clips" / f"{clip.id}{suffix}" for suffix in (".ass", ".partial.mp4")]
        else:
            video = project.video
            paths = list((root / "source").glob("video.*")) if not video or not video.source_path else []
            if not video or not video.audio_path:
                paths.append(root / "audio" / "audio.wav")
            if not project.transcript_segments:
                paths.append(root / "transcript" / "transcript.json")
        for path in paths:
            if path.resolve().is_relative_to(root) and path.is_file():
                path.unlink(missing_ok=True)
