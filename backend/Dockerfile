# Node 22+ is required by current yt-dlp EJS support. Keep it in the
# container so ingestion never depends on a host Node.js installation.
FROM node:22.23.3-bookworm-slim AS node-runtime

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# yt-dlp's JavaScript challenge solver runs through Node.js. yt-dlp itself
# is installed from pyproject.toml below, never from the OS package manager.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates ffmpeg build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node

WORKDIR /app
COPY pyproject.toml ./
COPY backend ./backend
RUN pip install ".[transcription]" \
    && python -m pip show yt-dlp yt-dlp-ejs \
    && command -v yt-dlp \
    && node --version \
    && ffmpeg -version >/dev/null

EXPOSE 8000
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
