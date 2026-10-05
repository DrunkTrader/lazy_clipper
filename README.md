# LazyClipper MVP

LazyClipper turns a long-form YouTube video into timestamped clip candidates and captioned vertical clips. Paste a video URL, let the app find moments worth keeping, then render only the clips you choose.

For component boundaries, data flow, persistence, runtime topology, and detailed processing workflows, see [ARCHITECTURE.md](ARCHITECTURE.md).

![LazyClipper workspace showing a processed project, suggested moments, transcript, and generated clip](assets/img_lazy.png)

The backend keeps the downloaded source for word-timestamped Whisper/LLM processing, but the frontend plays the original through YouTube's IFrame Player API. A suggested moment only becomes a deterministic center-cropped 9:16 MP4 (H.264 video, AAC audio) with burned-in word captions after the user clicks **Create Clip**. Advanced editing, intelligent reframing, publishing, and social integrations remain out of scope.

Full-video transcription persists segment and word timestamps once. Clip creation selects the stored words overlapping the requested range, writes a temporary ASS subtitle file, and burns it into only that requested clip; it never transcribes the clip again or renders captions for the original video.

There are no demo or static-data fallbacks. If ingestion, audio extraction, transcription, or LLM analysis fails, the project is persisted as `FAILED` with a safe stage-specific message. Technical exception diagnostics stay in redacted backend logs. Individual clip-render failures are stored on the clip; other clips continue, and the project remains usable.

## What you can do

- Submit a YouTube URL and create a reusable project.
- Inspect the original video through YouTube, alongside its transcript and AI-ranked moments.
- Jump to a suggested moment before deciding whether to create a clip.
- Render a selected moment as a center-cropped 1080×1920 MP4 with burned-in word captions.
- Preview or download completed clips, and retry individual failed renders without rerunning analysis.

### Start a project

The workspace begins with a focused URL entry point and keeps saved projects available for later inspection.

![LazyClipper project creation and project header](assets/img_lazy_1.png)

### Review moments and transcript

When processing completes, suggested moments appear beside the timestamped transcript. Selecting a moment seeks the YouTube player; creating a clip is always an explicit action.

![LazyClipper suggested moments and transcript](assets/img_lazy_2.png)

## Requirements

- Docker with BuildKit and Docker Compose v2+ for the recommended setup
- A YouTube-accessible URL for the selected video
- An OpenAI-compatible LLM gateway, such as FreeLLMAPI
- An API key and model configured in that gateway

The application uses `yt-dlp`, `yt-dlp-ejs`, Node.js, FFmpeg with libass subtitle support, `whisper-timestamped`, PostgreSQL, SQLAlchemy, FastAPI, React, and Vite.

The backend image installs the tested Python resolution in [`constraints.txt`](constraints.txt), including `yt-dlp==2026.8.19`, `yt-dlp-ejs==0.8.0`, `whisper-timestamped==1.15.9`, and CPU-only `torch==2.8.0+cpu`/`torchaudio==2.8.0` from the PyTorch CPU index. It does not depend on `/usr/bin/yt-dlp` or a host-installed yt-dlp. Node.js 22.23.3 is included in the image and is used by `yt-dlp-ejs` for modern YouTube JavaScript challenge solving. The constraints are a tested update set, not a blind “latest” upgrade; change them deliberately and rerun image/runtime checks.

## Configuration

Create a local environment file:

```bash
cp -n .env.example .env
```

The required LLM settings are:

```env
LLM_BASE_URL=http://127.0.0.1:3001/v1
LLM_API_KEY=your-api-key
LLM_MODEL=your-model-id
```

At API startup, configuration is validated before database initialization or processing admission: the transcript overlap must be smaller than its chunk window, and the LLM URL/key/model must be present, non-placeholder, and use an HTTP(S) URL with a host. This is a deterministic configuration check; it does not contact the provider. Invalid configuration fails startup with redacted operator diagnostics instead of waiting until after download/transcription.

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

The media/transcription settings include `WHISPER_MODEL`, `WHISPER_DEVICE`, transcript chunk/overlap sizes, and `MAX_MOMENTS`.

### Private deployment and database credentials

The personal Compose deployment trusts your **Tailscale network permissions**. Set `FRONTEND_BIND_ADDRESS` in `.env` to this server's Tailscale IPv4 address (obtain it with `tailscale ip -4`). Only the frontend is published, on that address at port 5173; PostgreSQL and the API have no published host ports. Do not set the binding to `0.0.0.0` or an untrusted/public interface. This is a single-user/trusted-tailnet boundary, not per-user authorization; every permitted tailnet client can access the workspace. Restrict access through your tailnet policy as appropriate.

Generate installation-specific database credentials once:

```bash
python3 scripts/setup_secrets.py
```

The command does not display credentials or replace existing values. `.secrets/` is private (0700), Git-ignored, and excluded from image builds. Its files are read-only (0444) inside that private directory so Compose's file-secret mounts can be read by PostgreSQL's container UID; other host users cannot traverse the private directory. The API receives only the application password, not the administrator secret. Keep these files with your protected deployment backups, and never commit them.

Fresh PostgreSQL volumes run the initialization script automatically. The `lazyclipper` application role has no superuser, role-creation, database-creation, replication, or RLS-bypass privileges. It has runtime DML on existing application tables and CREATE within the application database's public schema for fresh-schema initialization. Existing table ownership remains with its original owner; schema migrations use the administrative connection deliberately.

**Existing PostgreSQL volume: explicit upgrade required.** Changing secret files or Compose environment variables alone does not rotate an initialized role's password. Keep the API stopped while coordinating the role/password transition:

1. Generate the secret files above and configure the private frontend binding. Keep existing LLM/cookie configuration.
2. Stop the API/frontend. Start only PostgreSQL with `docker compose up -d --wait postgres`. No database host port is published.
3. Make a protected custom-format database backup (`pg_dump -U postgres -d lazy_clipper -Fc`), a globals backup (`pg_dumpall -U postgres --globals-only`), and a matching copy of `storage/`. Use a private directory and restrictive umask; globals backups contain authentication metadata. Restore-test the database backup in an isolated PostgreSQL instance before upgrading.
4. Run the idempotent, transactional role/password upgrade:
   ```bash
   docker compose exec -T postgres sh /opt/lazyclipper/setup-database-role.sh
   ```
   This creates/configures the application role, grants access to existing tables, and rotates the administrator password to its secret-file value. It requires local Unix-socket administrator access and does not modify authentication rules to bypass a custom policy. If access or existing role ownership differs from this dedicated-stack setup, stop and review it before proceeding.
5. Complete the schema upgrade below, then rebuild/start the API and frontend with `docker compose up -d --build --wait`. Verify project/clip counts and reads through the frontend. Do not restore superuser privileges to the API.

For local non-Compose development, configure your separately reachable `DATABASE_URL`; `DATABASE_PASSWORD_FILE` may provide its password without embedding it in the URL. Local Vite origins are explicitly allowlisted by `CORS_ALLOWED_ORIGINS`. Compose sets that list to empty because browser requests use the same-origin Nginx proxy. CORS is not the access boundary; the private binding and network policy are.

### Explicit schema upgrades

`backend/app/migrations.py` tracks revisions in `schema_migrations`. Startup initializes a genuinely fresh database at the current revision, but **refuses an unversioned, outdated, partial or incompatible existing schema**. It never assumes `create_all()` alters existing tables.

For an existing installation, stop the API/frontend and take/restore-test protected database/globals/media backups as described above. Keep PostgreSQL running. Build the new API image, then run the upgrade as the existing table owner; in the dedicated Compose installation that is the administrator role:

```bash
docker compose stop api frontend
docker compose up -d --wait postgres
# Take and restore-test the coordinated backups before proceeding.
docker compose build api
docker compose run --rm --no-deps -T --entrypoint python \
  -e DATABASE_URL=postgresql+psycopg://postgres@postgres:5432/lazy_clipper \
  -e DATABASE_PASSWORD_FILE=/run/secrets/migration_admin_password \
  -v "$(pwd)/.secrets/postgres_admin_password:/run/secrets/migration_admin_password:ro" \
  api -m backend.app.migrations upgrade
docker compose run --rm --no-deps -T --entrypoint python \
  api -m backend.app.migrations status
docker compose up -d --build --wait
```

The administrator secret is mounted only into the one-off upgrade process. Normal API containers retain the restricted application connection. For non-Compose upgrades, configure the table owner's `DATABASE_URL`/`DATABASE_PASSWORD_FILE` for the maintenance command, then restore runtime settings before starting the API.

Revision 1 atomically canonicalizes saved source URLs, adds their unique lookup index plus a video-ID index, and backfills `analysis_completed`. Existing READY/RENDERING projects or projects with saved moments count as completed analysis, including valid READY projects with zero moments; other legacy failures remain incomplete. New analysis saves the completion marker and all results in the same transaction, including an empty result. Source-only repair therefore does not repeat completed analysis.

Project IDs, child rows, media references, source artifacts and activity timestamps are preserved. Invalid, duplicate or conflicting legacy identities abort the upgrade before any schema change; reconcile them explicitly from backups rather than deleting/merging automatically. PostgreSQL DDL/data changes are transactional; SQLite upgrades explicitly begin a transaction before DDL. Repeating a completed upgrade is harmless. Downgrades are refused; rollback uses the protected pre-upgrade metadata/media backup together with the matching application version.

Ingestion looks up the indexed canonical URL, handles a concurrent unique-key conflict by returning the winning project, and locks persisted project claims. Clip submissions also lock the parent project before changing state. Continue running one API process: database constraints complement admission/recovery ownership, rather than making overlapping workers supported.

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
# First setup: configure .env, the Tailscale binding, secrets, and your LLM gateway.
# Keep an existing .env; do not overwrite working credentials.
# Existing DB volumes: complete the role and schema upgrades above before API startup.
docker compose up -d --build --wait
```

No temporary Compose override, host Python/Node installation, or source bind mount is needed. Use the same command after code changes. API startup waits for PostgreSQL to be healthy; frontend startup waits for API readiness. Compose uses `restart: unless-stopped` for all three services. `/health` is a cheap process-liveness endpoint; `/ready` checks the database and local processing admission without contacting the LLM or YouTube. Check readiness and logs with:

```bash
docker compose ps
docker compose logs --tail=100 api frontend
```

Build behavior:

- **Backend:** dependencies are installed from `pyproject.toml` in a cached build stage before application source is copied. Editing Python code does not reinstall the transcription/PyTorch dependencies. BuildKit also caches pip downloads; build tools are excluded from the runtime image.
- **Frontend:** `npm ci` installs the checked-in lockfile during the image build, followed by the TypeScript/Vite build. Nginx serves the compiled assets; startup no longer runs npm or depends on host `node_modules`.
- **API access:** by default the frontend proxies `/api/` to the API container, including generated clip playback and downloads. This works with the hostname used to open the frontend, not a hard-coded browser-side `localhost:8000`. Set `VITE_API_BASE_URL` only when using a separate API origin, then rebuild the frontend; Vite embeds this value at build time.

The first build still downloads the large transcription dependencies. Subsequent builds reuse them unless `pyproject.toml` or the base image changes. Do not use `--no-cache` for normal updates; it deliberately discards these image-layer savings. Model downloads are reused separately through the existing `whisper_cache` volume.

Compose mounts an optional host file using:

```text
./cookies.txt:/app/cookies.txt:ro
```

If `cookies.txt` is absent, Docker's optional bind mount creates an empty directory at that path; the application ignores it and continues without cookies. To use cookies, place an exported Netscape-format cookie file at `./cookies.txt` before starting the stack. If Docker previously created an empty `cookies.txt` directory, stop the stack and remove that empty directory with `rmdir cookies.txt` before placing the real file. Keep `YTDLP_COOKIE_FILE=/app/cookies.txt` for Compose; use `./cookies.txt` for local non-Compose development.

Open:

- Frontend: `http://<FRONTEND_BIND_ADDRESS>:5173`
- API documentation: `http://<FRONTEND_BIND_ADDRESS>:5173/docs`
- Health check: `http://<FRONTEND_BIND_ADDRESS>:5173/health`
- Readiness check: `http://<FRONTEND_BIND_ADDRESS>:5173/ready`

Paste a YouTube URL and click **Create project**. The frontend polls the project through ingestion, transcription, and analysis. Suggested moments control the YouTube player with **Jump to moment**. **Create Clip** sends that moment's timestamps to the API; the backend renders only the requested vertical MP4 and the workspace then shows its processing state, **Preview**, and **Download**.

The `?project_id=...` URL and **Saved projects** links reopen database-backed projects using GET requests only. Submitting the same YouTube video reuses its existing project rather than creating a duplicate. Explicitly resubmitting a failed project retries missing pipeline stages while retaining its downloaded source, audio, transcript, and moments. If a completed project's source file was deleted, an explicit submission restores it while preserving saved analysis; refreshing never redownloads it.

Each moment has its own **Create Clip** action. **Retry Clip** rerenders a failed or missing output without downloading, transcribing, analyzing, or replacing successful clips. Refresh never automatically starts clip rendering.

### Interrupted work and restart recovery

Run only **one API process** against a project database/storage directory. Before accepting requests on startup, the API reconciles abandoned processing states in one database transaction: interrupted ingestion becomes `FAILED`, active clips become `FAILED`, and projects that were rendering return to `READY` so saved analysis remains usable. Failures carry the safe `PROCESSING_INTERRUPTED` code and retry guidance.

Recovery preserves saved source/audio/transcript/moments and completed clip files. It does not launch work or infer that an uncommitted output file is a successful clip. Resubmit the video's URL to retry interrupted ingestion, or use **Retry Clip** for an interrupted render. GET/reload paths remain read-only. If reconciliation cannot commit, API startup fails instead of serving partially recovered state. Stop the old API process before starting its replacement; concurrent API processes are not supported by this startup-only recovery policy.

The first backend image build installs `whisper-timestamped` and may take several minutes. It also installs the pinned yt-dlp packages, Node.js, and FFmpeg inside the image. The first transcription downloads the configured Whisper model. Media and transcript files are stored under:

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

The examples below use port 8000 for a **local non-Compose API**. For Compose, use `http://<FRONTEND_BIND_ADDRESS>:5173` instead; the API is accessible through the private frontend proxy, not a separate published port.

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
QUEUED → INGESTING → TRANSCRIBING → ANALYZING → READY
```

Ingestion and analysis can end in:

```text
FAILED
```

After analysis is `READY`, an individual clip request temporarily uses `RENDERING` while FFmpeg creates that selected clip, then returns the project to `READY`.

Failure responses include a safe stage and structured error; for example, an analysis failure has:

```json
{
  "status": "FAILED",
  "failed_stage": "analysis",
  "error": {
    "code": "PROCESSING_ERROR",
    "message": "Internal server error during AI analysis."
  }
}
```

HTTP error envelopes use lowercase `"failed"`. Safe validation errors use application-owned codes such as `INVALID_URL` and `INVALID_RANGE`. Successful response shapes are unchanged. Existing `error_message` fields contain only the public message for new failures; legacy raw errors are sanitized on read without rewriting saved projects or requiring a migration. Empty or invalid word-timestamped Whisper output is treated as a failure rather than as a successful project with fake transcript data.

The frontend loads project details, transcript, moments, and clips when opened or explicitly reloaded, polls only `/status` every four seconds while processing, and stops at `READY`/`FAILED`. Clip-only refreshes reuse successfully loaded transcript/moments while updating project/clip state; ingestion transitions and explicit ingestion resubmission invalidate that local content cache. Requests time out after 30 seconds and display a safe network message. Error display uses local message allowlists rather than arbitrary server/proxy text.

Successful JSON is also validated at every frontend endpoint before updating state: required identities, arrays/items, statuses, finite ordered ranges, and project/moment scope must match the contract. Invalid responses produce safe endpoint-specific errors while independently loaded content remains usable. Unexpected React rendering failures show a local reload fallback; reloading does not automatically resubmit processing. LLM candidate reviews must identify each proposal exactly once, including rejected proposals; missing, duplicate or unknown review IDs use the existing single correction attempt and then fail analysis safely. A genuinely empty candidate detection remains valid.

Backend logs include project/clip IDs, stage lifecycle events, and redacted exception tracebacks. Credentials, request payloads, validation inputs, and SQL parameters are excluded. API and PostgreSQL healthchecks run every three minutes after fast startup checks. Uvicorn and Nginx routine access logging is disabled; application and error logs remain available via `docker compose logs api frontend`.

Other available endpoints include:

```text
GET /api/v1/projects
GET  /api/v1/projects/{project_id}/clips
POST /api/v1/projects/{project_id}/clips
GET  /api/v1/projects/{project_id}/clips/{clip_id}/media
GET  /api/v1/projects/{project_id}/clips/{clip_id}/media?download=true
DELETE /api/v1/projects/{project_id}
GET /api/v1/processing
```

The downloaded source video is never served to the frontend. The project response exposes the YouTube video ID for IFrame playback. Generated clip media is served only from the requested project's clips directory, with byte-range support for playback and an attachment response for downloads. JSON responses expose origin-relative clip paths, not filesystem paths; the frontend resolves them against the configured API origin when `VITE_API_BASE_URL` is set. This keeps same-origin Nginx, non-default frontend ports, and HTTPS/TLS termination from depending on backend `request.base_url` scheme/host inference.

Original-video previews pause once at the selected range's end, then permit normal playback/seeking until another selection. API-script loading and player readiness each have a 15-second deadline. A failed, unavailable or embedding-disabled source player shows safe text outside the clipped video frame, with **Retry player** and **Open on YouTube** actions. Retrying recreates only the player, without resubmitting ingestion or clips.

The clip POST accepts `moment_id`, `start`, and `end`, persists one queued clip, and submits only that clip to the existing thread pool. It reuses a valid `READY` output and can retry a failed/missing output. All GET routes are read-only.

Saved projects are paginated: `GET /api/v1/projects?limit=25&offset=0` returns an array ordered by creation time and ID, newest first. The default page size is 25, maximum 100, with offset from 0 through 1,000,000. **Load more projects** appends the next page and retains loaded entries if a later request fails. The workspace requests `/transcript?include_words=false`, selecting segment display fields directly without loading word JSON. Omitting that flag or using `include_words=true` retains the complete timing response for clients that need it; stored timing and caption rendering are unchanged.

Clip statuses are `QUEUED`, `RENDERING`, `READY`, and `FAILED`. A failed clip includes the same `failed_stage`/`error` contract plus the safe, backward-compatible `error_message` field.

## Processing limits and queue visibility

The existing two-thread executor supervises local, short-lived job process groups. By default, **two jobs can run and four can wait**. Both ingestion and clips share that capacity. POST requests reserve a slot before changing persisted state; saturation returns HTTP `429`, `PROCESSING_BUSY`, and `Retry-After: 5`. A duplicate ingestion or already-completed matching clip can still be returned without consuming a slot. Submission-lock acquisition also has a short deadline. Retrying remains an explicit user action.

`GET /api/v1/processing` is a read-only snapshot containing `workers`, `capacity`, `active`, `queued`, `reserved`, `quarantined`, `available`, `accepting`, and `oldest_queued_seconds`. Reservations include requests admitted but not yet dispatched; an unconfirmed termination retains its quarantined slot and closes admission. This reports capacity and queue age, not an unreliable completion-time estimate.

Configurable policies in `.env.example` and Compose:

| Setting | Default | Policy |
|---|---:|---|
| `MAX_QUEUED_JOBS` | 4 | Waiting capacity in addition to two active jobs |
| `MAX_SOURCE_SECONDS` | 7200 | Known, finite, positive metadata duration required before downloading; checked again for saved-stage ingestion retries |
| `MAX_CLIP_SECONDS` | 180 | Maximum explicit clip range; may be lowered, up to 180 seconds |
| `INGESTION_TIMEOUT_SECONDS` | 7200 | Total running budget: metadata, download, model initialization, transcription, analysis and persistence |
| `CLIP_TIMEOUT_SECONDS` | 960 | Total running clip budget; FFmpeg also retains its own 900-second deadline |
| `SUBMISSION_LOCK_TIMEOUT_SECONDS` | 1 | Maximum wait to enter serialized submission |
| `DATABASE_CONNECT_TIMEOUT_SECONDS` | 5 | PostgreSQL connection and pool-checkout deadline |
| `DATABASE_STATEMENT_TIMEOUT_SECONDS` | 10 | PostgreSQL statement execution deadline |
| `DATABASE_LOCK_TIMEOUT_SECONDS` | 3 | PostgreSQL lock wait; SQLite test/development busy timeout |

Execution budgets start when a worker launches the job, excluding queue wait. They bound Whisper/model loading and total download time even when an individual library call stalls. On expiry or graceful shutdown, the supervisor signals the whole group, escalates to `SIGKILL`, reaps its child, and confirms no non-zombie group member remains before releasing ownership or publishing retryability. A minimal guard process also kills the job group if the API parent exits abruptly. Model/provider code runs in a separate child of that guard so it cannot block the guard's signal handler.

Timeout failures use safe stage-specific `PROCESSING_TIMEOUT` messages. Completed stages and independent clips are preserved; unfinished ASS/partial clip outputs, unpersisted default ingestion artifacts, and job-owned temporary cookie copies are cleaned after confirmed termination. If termination or failure persistence cannot be confirmed, processing stops accepting new work until a healthy restart. Hard API/container crashes can still leave temporary files for later storage maintenance. Restart reconciliation remains required for lost queued work.

Supervised processing requires **Linux**, including Linux containers on other host platforms. Run one API process; stop its complete job tree before replacing it. The child uses a private settings pipe and new database sessions, with no inherited ORM connection or credentials in command arguments. Source duration is a metadata admission policy, not a measured RAM/disk quota. Tune budgets for your host/model; storage budgets and model-reuse measurements are separate work.

## Captioned clips

Full-video transcription stores word-level timing once. When a clip is requested, the renderer selects the stored words in that range, creates temporary ASS subtitles, and burns them into the MP4 with FFmpeg's libass-backed `subtitles` filter. The active word is amber and surrounding words are white in a sliding four-word window.

Long aligned segments are split by their timed-word sequence, independently of whitespace/punctuation token counts, while preserving speaker and word timing. New transcription with spoken segments lacking usable alignment fails before it can be saved as a successful stage. Clip generation distinguishes missing/misaligned stored timing (`CAPTION_ALIGNMENT_MISSING`) from a range with no captionable speech (`CAPTION_NO_SPEECH`). Both fail safely instead of publishing a supposedly captioned MP4 with zero dialogue events. Clip requests never silently repair or retranscribe legacy data; an affected transcript requires deliberate maintenance, preserving existing analysis and successful clips.

Caption styling belongs to the rendered video, so existing MP4s need to be rerendered after a style change. Clip creation reuses persisted word timings and never retranscribes the selected range. See [ARCHITECTURE.md](ARCHITECTURE.md#caption-generation-and-highlighting) for the implementation details and native subtitle checks.

## Local development without Compose

Linux, a PostgreSQL server, and system FFmpeg with libass subtitle support must be available. Use Compose on other host platforms. `whisper-timestamped` is optional locally because it is a large dependency:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[transcription,test]'
cp -n .env.example .env
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

Check and build the frontend (Node 22+ for the built-in test runner):

```bash
cd frontend
npm test
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
