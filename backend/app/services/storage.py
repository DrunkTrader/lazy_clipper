"""Scoped storage budgets, atomic reusable artifacts, and startup cleanup."""
import os
from pathlib import Path
import shutil
import tempfile
import re
from uuid import UUID, uuid4
from dataclasses import dataclass


class StorageBudgetError(RuntimeError):
    pass


@dataclass(frozen=True)
class StorageReport:
    bytes_used: int
    files_removed: int
    bytes_removed: int


def storage_root(storage_dir: Path) -> Path:
    root = storage_dir.resolve() / "projects"
    if root.is_symlink():
        raise ValueError("Project storage root must not be a symlink")
    return root


def _project_id(value: str) -> bool:
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def project_root(storage_dir: Path, project_id: str) -> Path:
    root = storage_root(storage_dir)
    if not _project_id(project_id):
        raise ValueError("Invalid project storage identifier")
    path = root / project_id
    if path.is_symlink():
        raise ValueError("Project storage must not be a symlink")
    return path


def _files(root: Path):
    if root.is_symlink() or not root.is_dir():
        return
    for directory, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [name for name in dirs if not (Path(directory) / name).is_symlink()]
        for name in names:
            path = Path(directory) / name
            if not path.is_symlink() and path.is_file():
                yield path


def usage_bytes(storage_dir: Path) -> int:
    return sum(path.stat().st_size for path in _files(storage_root(storage_dir)) or ())


def check_capacity(settings, reservations: int = 1) -> None:
    """Reserve headroom for an admitted job before its DB row is committed."""
    reservations = max(1, reservations)
    root = storage_root(settings.storage_dir)
    root.mkdir(parents=True, exist_ok=True)
    used = usage_bytes(settings.storage_dir)
    reserved = settings.storage_job_reserve_bytes * reservations
    disk = shutil.disk_usage(root)
    if used + reserved > settings.storage_max_bytes or disk.free < settings.storage_min_free_bytes + reserved:
        raise StorageBudgetError("Storage capacity is reserved for existing work")


def atomic_write_text(path: Path, content: str) -> Path:
    """Publish a complete text artifact through same-directory replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except Exception:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise
    return path


def _remove(path: Path) -> tuple[int, int]:
    if not path.exists() and not path.is_symlink():
        return 0, 0
    if path.is_dir() and not path.is_symlink():
        size = sum(item.stat().st_size for item in _files(path) or ())
        shutil.rmtree(path)
        return 1, size
    size = path.stat().st_size if path.is_file() else 0
    path.unlink(missing_ok=True)
    return 1, size


def reconcile_startup(storage_dir: Path, project_ids: set[str]) -> StorageReport:
    """Remove only known temporary/quarantine artifacts after old work stopped."""
    root = storage_root(storage_dir)
    root.mkdir(parents=True, exist_ok=True)
    removed = bytes_removed = 0
    trash = root / ".trash"
    if trash.is_symlink():
        raise ValueError("Deletion quarantine must not be a symlink")
    for entry in list(trash.iterdir()) if trash.is_dir() else ():
        identity, separator, token = entry.name.partition(".")
        if not separator or not _project_id(identity) or not re.fullmatch(r"[0-9a-f]{32}", token):
            continue  # Unknown files are never interpreted as deletion intent.
        if entry.is_symlink() or not entry.is_dir():
            raise ValueError("Invalid deletion quarantine")
        if identity in project_ids:
            destination = project_root(storage_dir, identity)
            if destination.exists():
                raise ValueError("Both project and quarantine exist; reconcile explicitly before startup")
            restore_quarantined(destination, entry)
            continue
        count, size = _remove(entry)
        removed += count
        bytes_removed += size
    for project in list(root.iterdir()):
        if project.name not in project_ids or not _project_id(project.name) or project.is_symlink() or not project.is_dir():
            continue
        for path in list(_files(project) or ()):
            relative = path.relative_to(project)
            if len(relative.parts) != 2:
                continue
            directory, name = relative.parts
            known = (
                directory == "clips" and (
                    name.endswith(".ass") and _project_id(name.removesuffix(".ass"))
                    or name.endswith(".partial.mp4") and _project_id(name.removesuffix(".partial.mp4"))
                )
                or directory == "source" and bool(re.fullmatch(r"video(?:\.[A-Za-z0-9_-]+)+\.part(?:-Frag\d+)?", name))
                or directory == "audio" and name == "audio.partial.wav"
                or directory == "transcript" and bool(re.fullmatch(r"\.transcript\.json\.[A-Za-z0-9_-]+\.tmp", name))
            )
            if known:
                count, size = _remove(path)
                removed += count
                bytes_removed += size
    return StorageReport(usage_bytes(storage_dir), removed, bytes_removed)


def quarantine_project(storage_dir: Path, project_id: str) -> tuple[Path, Path | None]:
    root = project_root(storage_dir, project_id)
    if not root.exists():
        return root, None
    trash = storage_root(storage_dir) / ".trash"
    if trash.is_symlink():
        raise ValueError("Deletion quarantine must not be a symlink")
    trash.mkdir(parents=True, exist_ok=True)
    target = trash / f"{project_id}.{uuid4().hex}"
    root.replace(target)
    return root, target


def restore_quarantined(root: Path, target: Path | None) -> None:
    if target is not None and target.exists():
        if root.exists() or root.is_symlink():
            raise ValueError("Cannot overwrite a project while restoring quarantine")
        root.parent.mkdir(parents=True, exist_ok=True)
        target.replace(root)


def remove_quarantined(target: Path | None) -> None:
    if target is not None:
        _remove(target)
