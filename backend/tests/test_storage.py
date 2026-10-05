"""Storage lifecycle, atomic artifacts and budget boundary regressions."""
import pytest
from pathlib import Path
from uuid import uuid4

from backend.app.config import Settings
from backend.app.services.storage import (
    StorageBudgetError,
    atomic_write_text,
    check_capacity,
    reconcile_startup,
    usage_bytes,
    quarantine_project,
    project_root,
)


def test_atomic_text_write_leaves_no_partial_and_replaces_complete_content(tmp_path):
    target = tmp_path / "transcript" / "transcript.json"
    atomic_write_text(target, '{"version": 1}')
    assert target.read_text() == '{"version": 1}'
    atomic_write_text(target, '{"version": 2}')
    assert target.read_text() == '{"version": 2}'
    assert not list(target.parent.glob("*.tmp"))


def test_startup_reconciles_only_known_temporary_and_quarantine_artifacts(tmp_path):
    storage = tmp_path / "storage"
    project = storage / "projects" / str(uuid4()) / "clips"
    project.mkdir(parents=True)
    clip_id = str(uuid4())
    (project / f"{clip_id}.ass").write_text("temporary")
    (project / f"{clip_id}.partial.mp4").write_bytes(b"partial")
    (project / "clip.mp4").write_bytes(b"valid")
    (project / "unrelated.txt").write_text("preserve")
    trash = storage / "projects" / ".trash" / f"{uuid4()}.{uuid4().hex}" / "source"
    trash.mkdir(parents=True)
    (trash / "video.mp4").write_bytes(b"deleted")
    report = reconcile_startup(storage, {project.parent.name})
    assert report.files_removed == 3
    assert report.bytes_removed == len("temporary") + len(b"partial") + len(b"deleted")
    assert (project / "clip.mp4").read_bytes() == b"valid"
    assert (project / "unrelated.txt").read_text() == "preserve"
    assert not trash.parent.exists()


def test_storage_budget_accounts_for_jobs_and_disk_reserve(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    storage.mkdir(parents=True)
    projects = storage / "projects"
    projects.mkdir()
    (projects / "existing.bin").write_bytes(b"12345")
    settings = Settings(_env_file=None, storage_dir=storage, storage_max_bytes=10, storage_job_reserve_bytes=5, storage_min_free_bytes=0)
    with pytest.raises(StorageBudgetError):
        check_capacity(settings, reservations=2)
    settings = settings.model_copy(update={"storage_max_bytes": 20})
    check_capacity(settings, reservations=2)
    assert usage_bytes(storage) == 5


def test_interrupted_delete_restores_media_if_database_record_survived(tmp_path):
    project_id = str(uuid4())
    root = project_root(tmp_path, project_id)
    root.mkdir(parents=True)
    (root / "kept.mp4").write_bytes(b"complete")
    _, quarantined = quarantine_project(tmp_path, project_id)
    assert quarantined.exists() and not root.exists()
    reconcile_startup(tmp_path, {project_id})
    assert (root / "kept.mp4").read_bytes() == b"complete"
    assert not quarantined.exists()
    _, quarantined = quarantine_project(tmp_path, project_id)
    reconcile_startup(tmp_path, set())
    assert not root.exists() and not quarantined.exists()


def test_cleanup_preserves_unknown_files_and_never_follows_project_symlinks(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / f"{uuid4()}.ass").write_text("not ours")
    projects = tmp_path / "storage" / "projects"
    projects.mkdir(parents=True)
    project_id = str(uuid4())
    (projects / project_id).symlink_to(outside, target_is_directory=True)
    kept = projects / str(uuid4()) / "clips"
    kept.mkdir(parents=True)
    (kept / "notes.tmp").write_text("not an application artifact")
    with pytest.raises(ValueError):
        project_root(tmp_path / "storage", project_id)
    for invalid in (".", "..", ".trash", "", "nested/id"):
        with pytest.raises(ValueError):
            project_root(tmp_path / "storage", invalid)
    reconcile_startup(tmp_path / "storage", {project_id, kept.parent.name})
    assert len(list(outside.iterdir())) == 1
    assert (kept / "notes.tmp").exists()


def test_atomic_write_failure_retains_previous_artifact(tmp_path, monkeypatch):
    target = tmp_path / "transcript.json"
    target.write_text("previous complete data")

    def fail(*args):
        raise OSError("synthetic replace failure")

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError):
        atomic_write_text(target, "replacement")
    assert target.read_text() == "previous complete data"
    assert list(tmp_path.iterdir()) == [target]


def test_low_free_space_rejects_even_with_tree_budget_available(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from backend.app.services import storage

    settings = Settings(_env_file=None, storage_dir=tmp_path, storage_max_bytes=1000,
                        storage_min_free_bytes=10, storage_job_reserve_bytes=5)
    monkeypatch.setattr(storage.shutil, "disk_usage", lambda _: SimpleNamespace(free=14))
    with pytest.raises(StorageBudgetError):
        check_capacity(settings)
