# LazyClipper MVP

LazyClipper turns a long-form YouTube video into timestamped clip candidates:

```text
YouTube URL
  → project
  → yt-dlp metadata and video
  → FFmpeg audio
  → WhisperX transcript
  → transcript normalization and chunking
  → OpenAI-compatible LLM analysis
  → validation, scoring, and deduplication
  → persisted moments
  → FFmpeg 1080×1920 clips
  → minimal React workspace with preview/download
```

Each suggested moment produces a deterministic center-cropped 9:16 MP4 (H.264 video, AAC audio). Advanced editing, intelligent reframing, publishing, captions, and social integrations remain out of scope.

There are no demo or static-data fallbacks. If ingestion, audio extraction, transcription, or LLM analysis fails, the project is persisted as `FAILED` with the actual error. Individual clip-render failures are stored on the clip; other clips continue, and the project remains usable.

## Requirements

- Docker with BuildKit and Docker Compose v2+ for the recommended setup
- A YouTube-accessible URL for the selected video
- An OpenAI-compatible LLM gateway, such as FreeLLMAPI
- An API key and model configured in that gateway

The application uses `yt-dlp`, `yt-dlp-ejs`, Node.js, FFmpeg, WhisperX, PostgreSQL, SQLAlchemy, FastAPI, React, and Vite. Processing currently runs in a small in-process thread pool; PostgreSQL stores the project state and generated results.

The backend image installs the pinned Python packages `yt-dlp==2026.8.19` and `yt-dlp-ejs==0.8.0` from `pyproject.toml`; it does not depend on `/usr/bin/yt-dlp` or a host-installed yt-dlp. Node.js 22.23.3 is included in the image and is used by `yt-dlp-ejs` for modern YouTube JavaScript challenge solving.

## Configuration

Create a local environment file:

```bash
cp .env.example .env
```

The required LLM settings are:

```env
LLM_BASE_URL=http://127.0.0.1:3001/v1
LLM_API_KEY=your-api-key
LLM_MODEL=your-model-id
```

FreeLLMAPI only requires changing those three values. If FreeLLMAPI runs on the host while the API runs in Compose, use:

```env
LLM_BASE_URL=http://host.docker.internal:3001/v1
```

The Compose configuration maps `host.docker.internal` on Linux. A reachable remote or Tailscale URL can also be used directly.

Optional LLM controls are available in `.env.example`:

```env
LLM_TEMPERATURE=0.2
LLM_MAX_TOKENS=4000
LLM_TIMEOUT=120
LLM_MAX_RETRIES=2
```

The YouTube runtime settings are:

```env
YTDLP_JS_RUNTIME=node
YTDLP_COOKIE_FILE=/app/cookies.txt
```

`YTDLP_COOKIE_FILE` is optional. The Python integration only passes a cookie file to yt-dlp when the configured path exists and is a regular file. In local non-Compose development, use a local path such as `YTDLP_COOKIE_FILE=./cookies.txt`.

The media/transcription settings include `WHISPER_MODEL`, `WHISPER_DEVICE`, `WHISPER_COMPUTE_TYPE`, transcript chunk/overlap sizes, and `MAX_MOMENTS`.

## YouTube ingestion and optional cookies

The ingestion service uses yt-dlp's Python API. For every metadata lookup and download it configures:

- `js_runtimes: {"node": {}}` by default, using `YTDLP_JS_RUNTIME`.
- `cookiefile` only when `YTDLP_COOKIE_FILE` points to an existing file.
- The existing canonical YouTube URL validation, MP4 format selection, size limit, retry settings, and storage paths.

Cookies are useful for videos that require an authenticated YouTube session or are blocked without browser session data. They are not required for every URL.

**Security:** `cookies.txt` contains authentication material. Never commit it, put it in `.env`, copy it into the Docker image, paste it into logs, or share it. `.gitignore` excludes both `cookies.txt` and `*.cookies.txt`. The Compose mount is read-only and only exposes the file to the API container.

## Run with Docker Compose

From the repository root, build and start PostgreSQL, the FastAPI backend, and the compiled React frontend:

```bash
# First setup only: copy .env.example to .env and configure your LLM gateway.
# Keep an existing .env; do not overwrite working credentials.
docker compose up -d --build --wait
```

No temporary Compose override, host Python/Node installation, or source bind mount is needed. Use the same command after code changes. API startup waits for PostgreSQL to be healthy; frontend startup waits for the API. Check readiness and logs with:

```bash
docker compose ps
docker compose logs --tail=100 api frontend
```

Build behavior:

- **Backend:** dependencies are installed from `pyproject.toml` in a cached build stage before application source is copied. Editing Python code does not reinstall WhisperX/PyTorch. BuildKit also caches pip downloads; build tools are excluded from the runtime image.
- **Frontend:** `npm ci` installs the checked-in lockfile during the image build, followed by the TypeScript/Vite build. Nginx serves the compiled assets; startup no longer runs npm or depends on host `node_modules`.
- **API access:** by default the frontend proxies `/api/` to the API container, including video playback and downloads. This works with the hostname used to open the frontend, not a hard-coded browser-side `localhost:8000`. Set `VITE_API_BASE_URL` only when using a separate API origin, then rebuild the frontend; Vite embeds this value at build time.

The first build still downloads the large transcription dependencies. Subsequent builds reuse them unless `pyproject.toml` or the base image changes. Do not use `--no-cache` for normal updates; it deliberately discards these image-layer savings. Model downloads are reused separately through the existing `whisper_cache` volume.

Compose mounts an optional host file using:

```text
./cookies.txt:/app/cookies.txt:ro
```

If `cookies.txt` is absent, Docker's optional bind mount creates an empty directory at that path; the application ignores it and continues without cookies. To use cookies, place an exported Netscape-format cookie file at `./cookies.txt` before starting the stack. If Docker previously created an empty `cookies.txt` directory, stop the stack and remove that empty directory with `rmdir cookies.txt` before placing the real file. Keep `YTDLP_COOKIE_FILE=/app/cookies.txt` for Compose; use `./cookies.txt` for local non-Compose development.

Open:

- Frontend: <http://localhost:5173>
- API documentation: <http://localhost:8000/docs>
- Health check: <http://localhost:8000/health>

Paste a YouTube URL and click **Create project**. The frontend polls the project through ingestion, transcription, analysis, and clip rendering. Suggested moments show clip status, **Preview**, and **Download**. Preview plays the generated vertical MP4; **Seek video** still seeks in the original source.

The `?project_id=...` URL and **Saved projects** links reopen database-backed projects using GET requests only. Submitting the same YouTube video reuses its existing project rather than creating a duplicate. Explicitly resubmitting a failed project retries missing pipeline stages while retaining its downloaded source, audio, transcript, and moments. If a completed project's source file was deleted, an explicit submission restores it while preserving saved analysis; refreshing never redownloads it.

For projects created before clip rendering was added, open the saved project and click **Generate clips**. **Retry failed clips** rerenders missing/failed outputs without downloading, transcribing, analyzing, or replacing successful clips. Refresh never automatically starts either operation.

The first backend image build installs WhisperX and may take several minutes. It also installs the pinned yt-dlp packages, Node.js, and FFmpeg inside the image. The first transcription downloads the configured Whisper model and VAD assets. Media and transcript files are stored under:

```text
storage/projects/<project_id>/
├── source/video.mp4
├── audio/audio.wav
├── transcript/transcript.json
└── clips/<clip_id>.mp4
```

The database stores clip ownership, moment ID, timestamps, status, errors, and file paths—not media bytes. Startup creates the additive `clips` table in existing databases. Incomplete renders use temporary files and are only published after FFmpeg succeeds.

Stop the local stack with:

```bash
docker compose down
```

This preserves database/model volumes and the `storage/` bind mount. Do not add `--volumes` unless you intend to delete the database and model cache. For live source editing rather than image rebuilds, use the local development commands below.

## API

### Create a project

```bash
curl -X POST http://localhost:8000/api/v1/ingest \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.youtube.com/watch?v=VIDEO_ID"}'
```

The endpoint validates and canonicalizes the URL. For a new video it persists a queued project and submits background processing. For an existing video it returns the saved project; failed projects or completed projects with a missing source file are explicitly retried. The response returns immediately:

```json
{
  "project_id": "...",
  "status": "queued"
}
```

### Inspect the project


```bash
curl http://localhost:8000/api/v1/projects/PROJECT_ID
curl http://localhost:8000/api/v1/projects/PROJECT_ID/status
curl http://localhost:8000/api/v1/projects/PROJECT_ID/transcript
curl http://localhost:8000/api/v1/projects/PROJECT_ID/moments
```

The project status moves through:

```text
QUEUED → INGESTING → TRANSCRIBING → ANALYZING → RENDERING → READY
```

Any stage can end in:

```text
FAILED
```

The failure response includes a human-readable error. Empty or invalid WhisperX output is treated as a failure rather than as a successful project with fake transcript data.

Other available endpoints include:

```text
GET /api/v1/projects
GET  /api/v1/projects/{project_id}/media
GET  /api/v1/projects/{project_id}/clips
POST /api/v1/projects/{project_id}/clips
GET  /api/v1/projects/{project_id}/clips/{clip_id}/media
GET  /api/v1/projects/{project_id}/clips/{clip_id}/media?download=true
```

The source media endpoint serves the downloaded video referenced by the database. The frontend uses it for browser playback and timestamp seeking. Clip media is served only from the requested project's clips directory, with byte-range support for playback and an attachment response for downloads. JSON responses expose URLs, not filesystem paths.

The bodyless clip POST explicitly queues rendering of saved moments through the existing thread pool. It skips valid `READY` outputs, retries failed/missing ones, and rejects submissions while a project is already processing. All GET routes are read-only.

Clip statuses are `QUEUED`, `RENDERING`, `READY`, and `FAILED`. A failed clip includes a human-readable `error_message`.

## Local development without Compose

A PostgreSQL server and system FFmpeg must be available. WhisperX is optional locally because it is a large dependency:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[transcription,test]'
cp .env.example .env
uvicorn backend.app.main:app --reload
```

In another shell:

```bash
cd frontend
npm install
npm run dev
```

For local `npm run dev`, set `VITE_API_BASE_URL` if the backend is not running at `http://localhost:8000`. The same-origin Nginx proxy is specific to the Docker frontend.

## Verification

Run the backend checks from the repository root:

```bash
. .venv/bin/activate
python -m compileall -q backend
ruff check backend
pytest -q
```

Build the frontend:

```bash
cd frontend
npm run build
```

The test suite uses mocked external services for deterministic unit and integration-contract tests, plus a real FFmpeg/ffprobe clip smoke test when those binaries are installed. Checks cover vertical dimensions/codecs/duration, clip failure isolation and retry, database reuse, GET-only reloads, duplicate submissions, scoped media access, byte ranges, and download headers. Production code does not use mocks or manufacture transcript/moment data.

### Verify the container runtime

After a fresh image build and startup, verify that the dependencies come from the image:

```bash
docker compose exec api yt-dlp --version
docker compose exec api node --version
docker compose exec api python -m pip show yt-dlp
docker compose exec api python -m pip show yt-dlp-ejs
```

The yt-dlp version should be the modern pip-installed version, and Node.js should be available for EJS solving. To verify YouTube metadata extraction with the same setup used by the application:

Because the Compose cookie mount is read-only, copy it to a temporary writable file for a direct CLI check (the application does this automatically):

```bash
docker compose exec api sh -eu -c '
  cookie_copy=$(mktemp)
  cp /app/cookies.txt "$cookie_copy"
  yt-dlp --ignore-config --js-runtimes node --cookies "$cookie_copy" --simulate \
    "https://www.youtube.com/watch?v=7EIk0crvc0k"
  rm -f "$cookie_copy"
'
```

If no cookie file is mounted, omit the copy and `--cookies` parts; the application itself does this automatically when the configured file is absent. Successful output should include a line similar to `[jsc:node] Solving JS challenges using node` when YouTube requires the challenge solver.

For a real application smoke test, start the Compose stack, submit a real YouTube URL, and poll its status until `READY` or `FAILED`:

```bash
curl -X POST http://localhost:8000/api/v1/ingest \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.youtube.com/watch?v=VIDEO_ID"}'

curl http://localhost:8000/api/v1/projects/PROJECT_ID/status
```

A successful run must expose non-empty video metadata, transcript segments, and AI-generated moments through the project, transcript, and moments endpoints. YouTube bot checks, unavailable media, missing speech, unavailable Whisper models, or an unavailable LLM provider will correctly produce `FAILED` rather than demo output.
