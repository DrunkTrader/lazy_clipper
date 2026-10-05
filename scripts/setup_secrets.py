"""Create installation-specific database secrets without printing or rotating them."""
import os
from pathlib import Path
import secrets
import stat


def create_secrets(directory: Path) -> None:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink() or directory.stat().st_mode & 0o077:
        raise ValueError("Secret directory must be private (mode 0700) and not a symlink")
    for name in ("postgres_admin_password", "app_database_password"):
        path = directory / name
        if path.exists() or path.is_symlink():
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o777 != 0o444 or not path.read_text(encoding="utf-8").strip():
                raise ValueError("Existing secrets must be nonempty regular read-only files in the private directory")
            continue
        # O_EXCL prevents accidental replacement/rotation, including concurrent runs.
        # Compose mounts file secrets without remapping ownership. Read-only
        # 0444 permits the container's postgres UID to read its mount; the host
        # directory remains 0700, preventing access by other host users.
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444), "w", encoding="utf-8") as stream:
            stream.write(secrets.token_urlsafe(32) + "\n")


if __name__ == "__main__":
    create_secrets(Path(__file__).resolve().parents[1] / ".secrets")
    print("Database secret files are ready; existing values were preserved.")
