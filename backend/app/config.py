"""Application configuration.

All settings can be supplied through environment variables (or a local .env).
"""
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore", hide_input_in_errors=True)

    app_name: str = "LazyClipper API"
    database_url: str = Field(default="postgresql+psycopg://lazyclipper@localhost:5432/lazy_clipper", repr=False)
    database_password_file: Path | None = None
    cors_allowed_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"])
    storage_dir: Path = Path("storage")
    storage_max_bytes: int = Field(default=50 * 1024**3, gt=0)
    storage_min_free_bytes: int = Field(default=1024**3, ge=0)
    storage_job_reserve_bytes: int = Field(default=1024**3, gt=0)
    ffmpeg_binary: str = "ffmpeg"
    yt_dlp_binary: str = "yt-dlp"
    ytdlp_js_runtime: str = Field(default="node", min_length=1)
    ytdlp_cookie_file: Path | None = None
    whisper_model: str = "small"
    whisper_device: str = "cpu"
    transcript_chunk_seconds: float = Field(default=300.0, gt=0)
    transcript_overlap_seconds: float = Field(default=45.0, ge=0)
    max_moments: int = Field(default=10, ge=1, le=100)

    max_queued_jobs: int = Field(default=4, ge=0, le=100)
    max_source_seconds: float = Field(default=7200, gt=0, allow_inf_nan=False)
    max_clip_seconds: float = Field(default=180, gt=0, le=180, allow_inf_nan=False)
    ingestion_timeout_seconds: float = Field(default=7200, gt=0, allow_inf_nan=False)
    clip_timeout_seconds: float = Field(default=960, gt=0, allow_inf_nan=False)
    media_timeout_margin_seconds: float = Field(default=30, gt=0, allow_inf_nan=False)
    submission_lock_timeout_seconds: float = Field(default=1, gt=0, le=5, allow_inf_nan=False)
    database_connect_timeout_seconds: int = Field(default=5, ge=1, le=30)
    database_statement_timeout_seconds: int = Field(default=10, ge=1, le=60)
    database_lock_timeout_seconds: int = Field(default=3, ge=1, le=30)

    # OpenAI-compatible provider configuration.
    llm_base_url: str | None = None
    llm_api_key: str | None = Field(default=None, repr=False)
    llm_model: str | None = None
    llm_temperature: float = Field(default=0.2, ge=0, le=2)
    llm_max_tokens: int = Field(default=4000, gt=0)
    llm_timeout: float = Field(default=120.0, gt=0)
    llm_max_retries: int = Field(default=2, ge=0, le=10)

    @model_validator(mode="after")
    def load_database_password(self) -> "Settings":
        if self.database_password_file is not None:
            from sqlalchemy.engine import make_url

            try:
                password = self.database_password_file.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise ValueError("The database password file is unavailable") from exc
            if not password:
                raise ValueError("The database password file is empty")
            url = make_url(self.database_url)
            if url.get_backend_name() != "postgresql":
                raise ValueError("A database password file requires a PostgreSQL URL")
            self.database_url = url.set(password=password).render_as_string(hide_password=False)
        return self

    @model_validator(mode="after")
    def validate_transcript_window(self) -> "Settings":
        if self.transcript_overlap_seconds >= self.transcript_chunk_seconds:
            raise ValueError("TRANSCRIPT_OVERLAP_SECONDS must be less than TRANSCRIPT_CHUNK_SECONDS")
        return self

    @property
    def database_is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    def project_storage(self, project_id: str) -> Path:
        from .services.storage import project_root
        path = project_root(self.storage_dir, project_id)
        for child in ("source", "audio", "transcript", "clips"):
            if (path / child).is_symlink():
                raise ValueError("Project media directories must not be symlinks")
            (path / child).mkdir(parents=True, exist_ok=True)
        return path


_process_settings: Settings | None = None


def set_process_settings(settings: Settings) -> None:
    """Install the parent's snapshot once inside a fresh job child process."""
    global _process_settings
    _process_settings = settings
    get_settings.cache_clear()


@lru_cache
def get_settings() -> Settings:
    return _process_settings or Settings()


def reset_settings_cache() -> None:
    global _process_settings
    _process_settings = None
    get_settings.cache_clear()


def validate_runtime_settings(settings: Settings) -> None:
    """Validate deterministic processing prerequisites before accepting work."""
    missing = [name for name, value in (
        ("LLM_BASE_URL", settings.llm_base_url),
        ("LLM_API_KEY", settings.llm_api_key),
        ("LLM_MODEL", settings.llm_model),
    ) if not value or not value.strip()]
    placeholders = {"your-api-key", "your-model-id"}
    if settings.llm_api_key in placeholders or settings.llm_model in placeholders:
        missing.append("non-placeholder LLM credentials")
    parsed = urlparse(settings.llm_base_url or "")
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        missing.append("LLM_BASE_URL with an http(s) host")
    if missing:
        raise ValueError("Invalid processing configuration: " + ", ".join(dict.fromkeys(missing)))
