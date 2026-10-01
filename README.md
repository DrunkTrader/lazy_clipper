# LazyClipper MVP

LazyClipper is a small, real YouTube-to-moments pipeline. It accepts a YouTube URL, downloads the source with `yt-dlp`, extracts audio with FFmpeg, transcribes with WhisperX, normalizes and chunks the transcript, asks an OpenAI-compatible LLM for structured clip candidates, deterministically ranks/deduplicates them, persists the results in PostgreSQL, and displays the project, transcript, and moments in a minimal React UI.

There is no demo/mock fallback. Missing media tools, WhisperX, PostgreSQL, or LLM configuration puts the project in `FAILED` with the real error.

## One-command startup

1. Copy the environment template and set the LLM values:

   ```bash
   cp .env.example .env
   # edit LLM_BASE_URL, LLM_API_KEY, and LLM_MODEL
   ```

   FreeLLMAPI only requires changing those three variables. When FreeLLMAPI runs on the host while Compose runs the API, use `LLM_BASE_URL=http://host.docker.internal:3001/v1`; the Compose file maps that hostname to the host on Linux too. A remote/Tailscale URL works directly.

2. Start PostgreSQL, FastAPI, and the Vite frontend:

   ```bash
   docker compose up --build
   ```

   Open <http://localhost:5173>. The first backend image build installs WhisperX and can take a while; the first transcription also downloads the configured Whisper model.

3. Paste a YouTube URL and click **Create project**. The workspace polls the API until `READY` or `FAILED` and then shows real video metadata, transcript segments, and ranked AI moments. Clicking a timestamp seeks the source video when the browser can play it.

The API is available at <http://localhost:8000/docs>.

## API smoke test

```bash
curl -X POST http://localhost:8000/api/v1/ingest \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.youtube.com/watch?v=VIDEO_ID"}'

curl http://localhost:8000/api/v1/projects/PROJECT_ID/status
curl http://localhost:8000/api/v1/projects/PROJECT_ID
curl http://localhost:8000/api/v1/projects/PROJECT_ID/transcript
curl http://localhost:8000/api/v1/projects/PROJECT_ID/moments
```

`POST /api/v1/ingest` returns immediately with a project id. Processing runs in a small in-process thread pool for this MVP; PostgreSQL is the source of truth for status and results.

## Local development without Compose

The runtime dependencies are declared in `pyproject.toml`. A PostgreSQL server and system FFmpeg must be available. WhisperX is intentionally an optional install because it is large:

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

Set `VITE_API_BASE_URL` if the API is not at `http://localhost:8000`.

## Verification

```bash
python3 -m compileall backend
pytest -q
cd frontend && npm run build
```

The unit tests mock external processing services; they do not manufacture transcript or moment data in the production pipeline.
