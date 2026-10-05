"""Tiny Linux group leader: kill the job tree even if its API parent dies.

Keep provider/model code in the child. This process only waits, so its signal
handler cannot be delayed by a model holding the GIL or a blocked native call.
"""
import ctypes
import os
import signal
import subprocess
import sys


def guard(command: list[str], parent_pid: int) -> int:
    if os.getpgrp() != os.getpid():
        raise RuntimeError("Job guard must own a dedicated process group")

    def stop_group(*_):
        os.killpg(os.getpgrp(), signal.SIGKILL)

    signal.signal(signal.SIGTERM, stop_group)
    # Linux parent-death notification. Recheck ppid to cover death before prctl.
    if ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGTERM, 0, 0, 0) != 0:
        raise RuntimeError("Could not install job parent-death notification")
    if os.getppid() != parent_pid:
        stop_group()
    # Inherit the private settings pipe and process group, not an API DB session.
    with subprocess.Popen(command, stdin=sys.stdin) as child:
        return child.wait()


if __name__ == "__main__":
    command = [sys.executable, "-m", "backend.app.services.job_process", *sys.argv[1:]]
    sys.exit(guard(command, int(os.environ["LAZYCLIPPER_PARENT_PID"])))
