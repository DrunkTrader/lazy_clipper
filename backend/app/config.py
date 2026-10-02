"""Application configuration.

Only the three LLM settings are used to configure the OpenAI-compatible client.
All settings can be supplied through environment variables (or a local .env).
"""
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "LazyClipper API"
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/lazy_clipper"
    storage_dir: Path = Path("storage")
    ffmpeg_binary: str = "ffmpeg"
    yt_dlp_binary: str = "yt-dlp"
    ytdlp_js_runtime: str = Field(default="node", min_length=1)
    ytdlp_cookie_file: Path | None = None
    whisper_model: str = "small"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    transcript_chunk_seconds: float = Field(default=300.0, gt=0)
    transcript_overlap_seconds: float = Field(default=45.0, ge=0)
    max_moments: int = Field(default=10, ge=1, le=100)

    # These are intentionally the only LLM configuration knobs.
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_temperature: float = Field(default=0.2, ge=0, le=2)
    llm_max_tokens: int = Field(default=4000, gt=0)
    llm_timeout: float = Field(default=120.0, gt=0)
    llm_max_retries: int = Field(default=2, ge=0, le=10)

    @property
    def database_is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    def project_storage(self, project_id: str) -> Path:
        path = self.storage_dir / "projects" / project_id
        for child in ("source", "audio", "transcript", "clips"):
            (path / child).mkdir(parents=True, exist_ok=True)
        return path


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
