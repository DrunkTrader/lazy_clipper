# LazyClipper agent notes

## Repository shape

- `backend/app/main.py` is the FastAPI entrypoint; routes live in `backend/app/api/routes.py`, and the persisted ingest/clip pipeline is in `backend/app/services/pipeline.py`.
- Background processing is an in-process `ThreadPoolExecutor` with two workers, not a separate queue or worker service. Pipeline tasks use their own database sessions and persist stage status.
- PostgreSQL stores project metadata; media stays under `storage/projects/<project_id>/{source,audio,transcript,clips}`. The downloaded source is backend-only; the UI plays the original through YouTube and serves generated clips through scoped API routes.
- There is no migration tool/configuration. Startup calls SQLAlchemy `create_all`; treat schema changes as requiring deliberate database migration handling rather than assuming existing tables are altered.
- `frontend/src` is a strict TypeScript React app built by Vite. The Docker frontend is a static Nginx image that proxies `/api/` to the `api` Compose service.

## Setup and runtime

- Recommended local stack: copy `.env.example` to `.env` without overwriting an existing `.env`, configure `LLM_BASE_URL`, `LLM_API_KEY`, and `LLM_MODEL`, then run `docker compose up -d --build --wait` from the repository root.
- Inspect the stack with `docker compose ps` and `docker compose logs --tail=100 api frontend`; stop it with `docker compose down` (omit `--volumes` to preserve PostgreSQL/model caches and `storage/`).
- Compose builds the pinned Python `yt-dlp`/`yt-dlp-ejs` dependencies and Node 22 into the API image; do not rely on a host-installed `yt-dlp` for container behavior. FFmpeg with libass subtitle support and `ffprobe` are also image dependencies.
- `VITE_API_BASE_URL` is embedded during the frontend image build. Leave it empty for the same-origin Nginx `/api` proxy; set it only for a separate API origin and rebuild.
- `cookies.txt` is optional authentication material, mounted read-only in Compose and ignored when absent. Never commit it, put it in `.env`, copy it into an image, or log its contents.
- For non-Compose development, Python 3.12+, PostgreSQL, and system FFmpeg with libass subtitle support are required; `whisper-timestamped` is optional unless running the real transcription pipeline:
  ```bash
  python3 -m venv .venv
  . .venv/bin/activate
  pip install -e '.[test]'                 # add [transcription] to run word-timestamped transcription locally
  cp .env.example .env                    # configure local DATABASE_URL/LLM settings
  uvicorn backend.app.main:app --reload
  ```
  In a second shell: `cd frontend && npm install && npm run dev`.

## Caption implementation

- `backend/app/services/transcription.py` uses `whisper-timestamped` for full-source word timing. Preserve the persisted segment `words` data through normalization and reuse it when creating clips.
- `backend/app/services/captions.py` uses standard-library Python to generate ASS subtitles. `select_words()` selects/clamps overlapping words; `build_ass()` converts their timestamps to clip-relative times and emits one dialogue event per word with a sliding window of up to four words.
- Current-word highlighting is an ASS color override: `{\c&H0000BFFF&}` for amber (`#FFBF00`) and `{\c&H00FFFFFF&}` for surrounding white words. ASS uses blue-green-red color ordering. Change colors/window behavior in `build_ass()` and font, size, background, and placement in `ASS_HEADER`.
- `Pipeline._render_clip_media()` writes `clips/<clip_id>.ass`, passes it to `MediaService.render_clip()`, and removes it in `finally`. FFmpeg's `subtitles` filter uses native libass to burn captions into the requested MP4. Preserve temporary-file cleanup on both success and failure.
- Caption styling belongs to the rendered video. Existing MP4s require rerendering to pick up style changes; the React player simply plays the generated clip. Keep caption generation tied to explicit clip requests, using stored timings rather than retranscribing the clip.

## Verification

- From the repository root, run backend checks in this order:
  ```bash
  . .venv/bin/activate
  python -m compileall -q backend
  ruff check backend
  pytest -q
  ```
- Run a focused backend file with `pytest -q backend/tests/test_api.py`, or a single test with `pytest -q backend/tests/test_api.py::test_ingest_persists_project_and_exposes_status`.
- Backend tests use temporary SQLite databases and mocked external services; the real vertical MP4 test is skipped unless both `ffmpeg` and `ffprobe` are available.
- For caption changes, run `pytest -q backend/tests/test_captions.py backend/tests/test_pipeline.py backend/tests/test_clipping.py`. Verify native subtitle support with `ffmpeg -hide_banner -h filter=subtitles` (or prefix with `docker compose exec api` for the container); a present FFmpeg binary alone does not guarantee libass support.
- Check the frontend separately with `cd frontend && npm test && npm run build`; tests use Node 22+ and the existing Vite toolchain, and the build runs `tsc -b` followed by `vite build`. There is no frontend lint script.

## Product invariants

- Do not add demo/static fallbacks: real ingestion, transcription, and LLM failures must remain persisted as `FAILED` with a safe stage-specific message. Actual exception diagnostics belong only in redacted backend logs; the frontend must never display raw provider/exception text.
- Ingestion is resumable from persisted stages and does not render clips. A clip is rendered only after the user POSTs one moment’s timestamps; clip failures are isolated and retryable.
- GET/reload paths are read-only. Explicitly resubmitting ingestion may retry a failed project or restore a missing source while retaining valid saved stages.
- Keep media paths scoped to the project’s storage directory and expose clip URLs, never filesystem paths; source video is never a frontend media endpoint. See `README.md` for the API and state details.
