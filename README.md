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
  → FastAPI API
  → minimal React workspace
```

The current MVP ends at inspecting and selecting generated moments. Advanced editing, publishing, captions, vertical reframing, and social integrations are intentionally out of scope.

There are no demo or static-data fallbacks. If ingestion, media processing, transcription, PostgreSQL, or LLM analysis fails, the project is persisted as `FAILED` with the actual error.

## Requirements

- Docker and Docker Compose for the recommended setup
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

Start PostgreSQL, the FastAPI backend, and the Vite frontend:

```bash
docker compose build --no-cache
docker compose up -d
```

Compose mounts an optional host file using:

```text
./cookies.txt:/app/cookies.txt:ro
```

If `cookies.txt` is absent, the application ignores the mounted path and continues without cookies. To use cookies, place an exported Netscape-format cookie file at `./cookies.txt` before starting the stack. You can override the container path with `YTDLP_COOKIE_FILE`, but it must match the mounted target.

Open:

- Frontend: <http://localhost:5173>
- API documentation: <http://localhost:8000/docs>
- Health check: <http://localhost:8000/health>

Paste a YouTube URL and click **Create project**. The frontend polls the project until it reaches `READY` or `FAILED`, then displays real metadata, transcript segments, and ranked moments.

The first backend image build installs WhisperX and may take several minutes. It also installs the pinned yt-dlp packages, Node.js, and FFmpeg inside the image. The first transcription downloads the configured Whisper model and VAD assets. Media and transcript files are stored under:

```text
storage/projects/<project_id>/
├── source/video.mp4
├── audio/audio.wav
├── transcript/transcript.json
└── clips/
```

Stop the local stack with:

```bash
docker compose down
```

## API

### Create a project

```bash
curl -X POST http://localhost:8000/api/v1/ingest \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.youtube.com/watch?v=VIDEO_ID"}'
```

The endpoint validates the URL, creates a queued project, submits background processing, and returns immediately:

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
QUEUED → INGESTING → TRANSCRIBING → ANALYZING → READY
```

Any stage can end in:

```text
FAILED
```

The failure response includes a human-readable error. Empty or invalid WhisperX output is treated as a failure rather than as a successful project with fake transcript data.

Other available endpoints include:

```text
GET /api/v1/projects
GET /api/v1/projects/{project_id}/media
```

The media endpoint serves the downloaded source video when it is available. The frontend uses it for browser playback and timestamp seeking.

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

Set `VITE_API_BASE_URL` if the backend is not running at `http://localhost:8000`.

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

The test suite uses mocked external services for deterministic unit and integration-contract tests. Production code does not use those mocks or manufacture transcript/moment data.

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
