"""Small process-local admission ledger; reservations precede database commits."""
from concurrent.futures import Executor, Future
from contextlib import contextmanager
from threading import Lock
from time import monotonic

from .execution import TerminationFailure


class QueueFull(RuntimeError):
    pass


class Admission:
    def __init__(self, *, workers: int, waiting: int):
        self.workers = workers
        self.capacity = workers + waiting
        self._lock = Lock()
        self._slots: dict[Reservation, tuple[str, float]] = {}
        self._closed = False

    @contextmanager
    def reserve(self, project_id: str | None = None):
        slot = Reservation(self)
        slot.project_id = project_id
        with self._lock:
            if self._closed or len(self._slots) >= self.capacity:
                raise QueueFull("Processing admission is full or closed")
            self._slots[slot] = ("reserved", monotonic())
        try:
            yield slot
        finally:
            if not slot.submitted:
                self._release(slot)

    def _release(self, slot):
        with self._lock:
            if not slot.quarantined:
                self._slots.pop(slot, None)

    def close(self):
        with self._lock:
            self._closed = True

    def owns(self, project_id: str) -> bool:
        with self._lock:
            return any(slot.project_id == project_id for slot in self._slots)

    def snapshot(self) -> dict:
        with self._lock:
            counts = {name: sum(state == name for state, _ in self._slots.values())
                      for name in ("active", "queued", "reserved", "quarantined")}
            ages = [monotonic() - since for state, since in self._slots.values() if state == "queued"]
            return {
                "workers": self.workers, "capacity": self.capacity, **counts,
                "available": 0 if self._closed else self.capacity - len(self._slots),
                "accepting": not self._closed,
                "oldest_queued_seconds": round(max(ages, default=0), 3),
            }


class Reservation:
    def __init__(self, admission: Admission):
        self.admission = admission
        self.submitted = False
        self.quarantined = False
        self.project_id: str | None = None

    def submit(self, executor: Executor, runner, *args, project_id: str | None = None) -> Future:
        if self.submitted:
            raise RuntimeError("An admission reservation can be submitted only once")
        with self.admission._lock:
            self.project_id = project_id or self.project_id
            self.admission._slots[self] = ("queued", monotonic())
        future = executor.submit(self._run, runner, args)
        self.submitted = True
        # Also releases jobs canceled before they start. pop is idempotent.
        future.add_done_callback(lambda _: self.admission._release(self))
        return future

    def _run(self, runner, args):
        with self.admission._lock:
            self.admission._slots[self] = ("active", monotonic())
        try:
            return runner(*args)
        except TerminationFailure:
            # Keep ownership counted until restart; a failed kill is not a free
            # slot, even though the supervising future has finished.
            with self.admission._lock:
                self.quarantined = True
                self.admission._closed = True
                self.admission._slots[self] = ("quarantined", monotonic())
            raise
        finally:
            self.admission._release(self)
