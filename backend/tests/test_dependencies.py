"""Keep image dependency resolution explicit and CPU-oriented."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def constraints():
    values = {}
    for line in (ROOT / "constraints.txt").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            name, version = line.split("==", 1)
            values[name.lower()] = version
    return values


def test_constraints_pin_runtime_and_transcription_resolution():
    pinned = constraints()
    for name in ("fastapi", "pydantic", "sqlalchemy", "psycopg", "openai", "yt-dlp", "yt-dlp-ejs", "whisper-timestamped"):
        assert name in pinned
    assert pinned["torch"] == "2.8.0+cpu"
    assert pinned["torchaudio"] == "2.8.0"
    assert pinned["whisper-timestamped"] == "1.15.9"


def test_docker_build_uses_constraints_and_cpu_index_on_all_linux_architectures():
    dockerfile = (ROOT / "backend/Dockerfile").read_text()
    assert "COPY pyproject.toml constraints.txt ./" in dockerfile
    assert '"https://download.pytorch.org/whl/cpu"' in dockerfile
    assert '"--constraint", "constraints.txt"' in dockerfile
    assert "platform.machine().lower() not in" not in dockerfile
