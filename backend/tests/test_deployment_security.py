"""F02/F03: configuration contracts, without contacting a live provider."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
from sqlalchemy.engine import make_url

from backend.app.config import Settings
from backend.tests.test_api import api_client as api_client


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(not shutil.which("docker"), reason="Docker Compose CLI required for config validation")
def test_compose_publishes_only_the_private_frontend():
    result = subprocess.run(
        ["docker", "compose", "--env-file", "/dev/null", "config", "--format", "json"],
        cwd=ROOT, capture_output=True, text=True, check=True,
        env={"PATH": os.environ["PATH"], "FRONTEND_BIND_ADDRESS": "100.64.0.10"},
    )
    services = json.loads(result.stdout)["services"]
    assert not services["postgres"].get("ports")
    assert not services["api"].get("ports")
    ports = services["frontend"]["ports"]
    assert len(ports) == 1
    assert ports[0]["host_ip"] == "100.64.0.10"
    assert str(ports[0]["published"]) == "5173"
    assert make_url(services["api"]["environment"]["DATABASE_URL"]).username == "lazyclipper"
    assert "POSTGRES_PASSWORD" not in services["postgres"]["environment"]
    assert services["postgres"]["environment"]["POSTGRES_PASSWORD_FILE"].startswith("/run/secrets/")
    assert services["api"]["environment"]["DATABASE_PASSWORD_FILE"].startswith("/run/secrets/")
    assert services["api"]["environment"]["CORS_ALLOWED_ORIGINS"] == "[]"
    assert all(services[name].get("restart") == "unless-stopped" for name in ("postgres", "api", "frontend"))


def test_database_secret_file_is_applied_without_exposing_it_in_settings(tmp_path):
    password = tmp_path / "password"
    password.write_text("synthetic-db-password/@:\n", encoding="utf-8")
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://lazyclipper@localhost/lazy_clipper",
                        database_password_file=password, llm_api_key="unit-test-key")
    assert make_url(settings.database_url).password == "synthetic-db-password/@:"
    assert "synthetic-db-password" not in repr(settings)


def test_database_secret_file_must_not_be_empty(tmp_path):
    password = tmp_path / "password"
    password.write_text("\n", encoding="utf-8")
    with pytest.raises(ValueError, match="database password file"):
        Settings(_env_file=None, database_password_file=password, llm_api_key="unit-test-key")


def test_secret_initialization_is_private_and_does_not_rotate(tmp_path, capsys):
    from hashlib import sha256
    from scripts.setup_secrets import create_secrets

    directory = tmp_path / "secrets"
    create_secrets(directory)
    before = {p.name: sha256(p.read_bytes()).digest() for p in directory.iterdir()}
    create_secrets(directory)
    assert {p.name: sha256(p.read_bytes()).digest() for p in directory.iterdir()} == before
    assert directory.stat().st_mode & 0o777 == 0o700
    assert all(p.stat().st_mode & 0o777 == 0o444 for p in directory.iterdir())
    assert capsys.readouterr().out == ""


def test_arbitrary_origins_cannot_read_api_responses(api_client):
    response = api_client.get("/api/v1/projects", headers={"Origin": "https://untrusted.example"})
    assert "access-control-allow-origin" not in response.headers
    preflight = api_client.options("/api/v1/ingest", headers={
        "Origin": "https://untrusted.example", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "Content-Type",
    })
    assert preflight.status_code == 400


def test_local_development_origin_is_explicitly_supported(api_client):
    response = api_client.get("/api/v1/projects", headers={"Origin": "http://localhost:5173"})
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_health_is_liveness_and_ready_checks_database_and_processing(api_client):
    assert api_client.get("/health").json() == {"status": "ok"}
    ready = api_client.get("/ready")
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready", "processing": "accepting"}


def test_ready_fails_closed_when_admission_is_closed(api_client):
    from backend.app.main import app

    app.state.admission.close()
    try:
        response = api_client.get("/ready")
        assert response.status_code == 503
        assert response.json() == {"status": "not_ready", "reason": "processing_unavailable"}
    finally:
        app.state.admission._closed = False


def test_processing_window_and_runtime_llm_configuration_fail_early():
    from backend.app.config import Settings, validate_runtime_settings

    with pytest.raises(ValueError, match="OVERLAP"):
        Settings(_env_file=None, transcript_chunk_seconds=30, transcript_overlap_seconds=30)
    with pytest.raises(ValueError, match="LLM_API_KEY"):
        validate_runtime_settings(Settings(_env_file=None, llm_base_url="http://gateway.test/v1",
                                           llm_api_key=None, llm_model=None))
    with pytest.raises(ValueError, match=r"http\(s\)"):
        validate_runtime_settings(Settings(_env_file=None, llm_base_url="gateway.test", llm_api_key="key", llm_model="model"))


def test_runtime_configuration_accepts_non_placeholder_provider_settings():
    from backend.app.config import Settings, validate_runtime_settings

    validate_runtime_settings(Settings(_env_file=None, llm_base_url="https://gateway.test/v1",
                                       llm_api_key="unit-test-key", llm_model="unit-test-model"))
