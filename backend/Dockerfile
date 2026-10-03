# syntax=docker/dockerfile:1
# Keep Node and Python on the same Debian release for runtime compatibility.
FROM node:22.23.3-bookworm-slim AS node-runtime

FROM python:3.12-slim-bookworm AS dependencies

ENV VIRTUAL_ENV=/opt/venv
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && python -m venv "$VIRTUAL_ENV"

WORKDIR /build
COPY pyproject.toml ./
# Read dependency declarations from their existing source of truth. Source-code
# edits must not invalidate the expensive transcription/PyTorch installation layer.
RUN --mount=type=cache,target=/root/.cache/pip python - <<'PY'
import platform
import subprocess
import sys
import tomllib

with open("pyproject.toml", "rb") as manifest:
    project = tomllib.load(manifest)["project"]
requirements = project["dependencies"] + project["optional-dependencies"]["transcription"]
install = [sys.executable, "-m", "pip", "install"]
if platform.machine().lower() not in {"x86_64", "amd64"}:
    # Match WhisperX's non-x86 Linux CPU index and compatible Torch 2.8 pair.
    # ARM64 Torchaudio 2.8's CPU wheel has no +cpu version suffix.
    cpu_requirements = ["torch==2.8.0+cpu", "torchaudio==2.8.0"]
    subprocess.check_call(install + ["--index-url", "https://download.pytorch.org/whl/cpu"] + cpu_requirements)
    # Keep the CPU build pinned when resolving the application's dependencies.
    requirements += cpu_requirements
subprocess.check_call(install + requirements)
PY

FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

# Compilers stay in the build stage. FFmpeg and OpenMP are runtime dependencies.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates ffmpeg libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=dependencies /opt/venv /opt/venv
COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node

WORKDIR /app
COPY backend/__init__.py ./backend/__init__.py
COPY backend/app ./backend/app
# Fail the image build early if required runtime components are unavailable.
RUN python -m pip check \
    && python -c "import backend.app.main; import yt_dlp; import whisper_timestamped" \
    && node --version \
    && ffmpeg -version >/dev/null \
    && ffprobe -version >/dev/null

EXPOSE 8000
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
