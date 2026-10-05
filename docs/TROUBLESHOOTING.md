# Troubleshooting

Run Compose commands from the repository root. Start with:

```bash
docker compose ps
docker compose logs --tail=100 api frontend
```

Public errors are stage-specific and sanitized. Technical diagnostics belong in redacted backend logs; there are no demo transcripts, moments, or clips substituted for failures.

## Startup and configuration

- **Invalid LLM configuration:** supply a non-placeholder `LLM_BASE_URL`, `LLM_API_KEY`, and `LLM_MODEL`. The URL must have an HTTP(S) scheme and host. Startup validates settings before database/recovery/admission work without contacting the provider; passing validation does not establish provider reachability.
- **Invalid transcript windows:** `TRANSCRIPT_OVERLAP_SECONDS` must be smaller than `TRANSCRIPT_CHUNK_SECONDS`.
- **Database/schema failure:** follow [Database maintenance](DATABASE.md). Changing `.secrets/` alone does not rotate an existing role's password, and startup does not migrate an existing schema automatically.
- **Frontend/API connection failure:** Compose uses `http://<FRONTEND_BIND_ADDRESS>:5173`, including `/docs`, `/health`, and `/ready`; port 8000 is only directly available in non-Compose development. Leave `VITE_API_BASE_URL` empty for Compose and rebuild after changing it. Check the Tailscale binding and [network policy](DEPLOYMENT.md#private-network-access).

## YouTube access and cookies

yt-dlp uses Node by default (`YTDLP_JS_RUNTIME=node`) for metadata and downloads. Cookies are only passed when `YTDLP_COOKIE_FILE` points to an existing regular file.

Place an exported Netscape-format `cookies.txt` in the repository root before startup when authentication is needed. Compose mounts `./cookies.txt:/app/cookies.txt:ro`; keep `YTDLP_COOKIE_FILE=/app/cookies.txt`. For local development, use a local path such as `./cookies.txt`.

If no file exists, Docker's bind mount may create an empty `cookies.txt` directory. The application ignores it. To replace that empty directory with a real cookie file:

```bash
docker compose down
rmdir cookies.txt
```

Then place the cookie file and restart. `rmdir` removes only an empty directory. Never commit cookies, put their contents in `.env`, copy them into images, log them, or share them; both `cookies.txt` and `*.cookies.txt` are Git-ignored.

### Direct yt-dlp verification

These checks contact YouTube. Replace the example URL with the video you intend to test. With a cookie file mounted, use a temporary writable copy because the mounted file is read-only (the application does this automatically):

```bash
docker compose exec api sh -eu -c '
  cookie_copy=$(mktemp)
  trap '\''rm -f "$cookie_copy"'\'' EXIT
  cp /app/cookies.txt "$cookie_copy"
  yt-dlp --ignore-config --js-runtimes node --cookies "$cookie_copy" --simulate \
    "https://www.youtube.com/watch?v=7EIk0crvc0k"
'
```

Without cookies:

```bash
docker compose exec api yt-dlp --ignore-config --js-runtimes node --simulate \
  "https://www.youtube.com/watch?v=7EIk0crvc0k"
```

When YouTube requires the solver, successful output may include `[jsc:node] Solving JS challenges using node`. Bot checks or unavailable media remain real failures.

## Processing failures and interrupted work

- Resubmitting a failed video's URL retries missing stages while preserving completed source/audio/transcript/analysis. Explicitly resubmitting a completed project with a missing source restores it without repeating completed analysis.
- **Retry Clip** retries a failed/missing clip without rerunning ingestion or replacing other successful clips. GET requests and project reloads never start work.
- HTTP 429 / `PROCESSING_BUSY` means admission is full or submission is busy; wait and explicitly retry. Inspect `/api/v1/processing`. HTTP 507 / `STORAGE_BUDGET` requires restoring storage headroom before retrying.
- `PROCESSING_TIMEOUT` means a running job exceeded its configured budget. Queue wait does not count against that budget. See [runtime policies](DEPLOYMENT.md#runtime-policies).
- `PROCESSING_INTERRUPTED` means startup recovered abandoned work. Interrupted ingestion/clips become FAILED; rendering projects return to READY, with saved analysis and completed files retained. Resubmit ingestion or use **Retry Clip**.
- A non-terminal project that has remained in the same status for about ten minutes shows safe guidance that the job may be stuck. Use **Reload status** to refresh the status view; this does not retry or duplicate processing.

Recovery never automatically queues work or treats uncommitted output as successful. If recovery cannot commit, startup fails. Stop the old API and its job tree before starting a replacement; concurrent API processes are unsupported.

For shutdown/timeouts, whole-group termination is confirmed before ownership is released or retryable failure is published. Signals escalate to SIGKILL and the child is reaped; confirmation excludes zombie group members. The parent-death guard also terminates work if the API exits abruptly. The child gets a private settings pipe and fresh database sessions, rather than inherited ORM connections or credentials in command arguments. See [Architecture](ARCHITECTURE.md#execution-and-startup) for the supervision design.

After confirmed termination, cleanup removes unfinished ASS/partial clip output, unpersisted default ingestion artifacts, and job-owned temporary cookie copies. Hard crashes may leave temporary artifacts for startup storage maintenance. If termination or failure persistence cannot be confirmed, admission closes until a healthy restart; quarantined capacity is not released prematurely. Lost in-memory queued work still requires startup reconciliation.

## Analysis, captions, and playback

- Empty/invalid word-timestamped Whisper output fails transcription rather than creating fake transcript data. Spoken segments without usable alignment cannot be saved as a successful new transcription.
- LLM reviews must identify every candidate exactly once, including rejected candidates. Missing, duplicate, or unknown IDs receive one correction attempt and then fail analysis safely. Valid detection may return no candidates.
- `CAPTION_ALIGNMENT_MISSING` means stored word timing is missing/misaligned. `CAPTION_NO_SPEECH` means the requested range has no captionable speech. Both fail instead of publishing an empty-caption MP4. Clip requests do not repair/retranscribe legacy transcripts; those require deliberate maintenance that preserves analysis and successful clips.
- Long aligned segments are split by their timed-word sequence, preserving speaker/timing independently of whitespace or punctuation counts. Caption styling is burned into output: existing MP4s need rerendering after style changes, while unchanged valid READY clips are reused. See [caption implementation](ARCHITECTURE.md#caption-generation-and-highlighting).
- Source previews pause once at the selected end, then allow normal playback/seeking until another selection. API-script loading and player readiness each have a 15-second deadline. For a failed/embedding-disabled player, use **Retry player** or **Open on YouTube**; player retry does not submit ingestion or clips.

## Container runtime verification

After a fresh build/start:

```bash
docker compose exec api yt-dlp --version
docker compose exec api node --version
docker compose exec api python -m pip show yt-dlp
docker compose exec api python -m pip show yt-dlp-ejs
docker compose exec api python -m pip check
docker compose exec api ffmpeg -hide_banner -h filter=subtitles
```

Versions should match the image's checked-in pins, not host installations. A present FFmpeg executable alone does not establish native libass subtitle support. Backend tests use mocked providers; the synthetic MP4 check verifies vertical dimensions, codecs, duration, temporary cleanup, retry/isolation, scoped media, byte ranges, and downloads across the relevant tests.

## Real workflow verification

This creates/retries a project and contacts YouTube, Whisper, and the configured LLM; provider usage may be billed. With Compose running, set the actual frontend address and replace `VIDEO_ID` with a real video ID:

```bash
BASE_URL=http://<FRONTEND_BIND_ADDRESS>:5173
curl -X POST "$BASE_URL/api/v1/ingest" \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.youtube.com/watch?v=VIDEO_ID"}'
curl "$BASE_URL/api/v1/projects/PROJECT_ID/status"
```

Replace `PROJECT_ID` with the returned ID and poll until READY or FAILED. For non-Compose development use `BASE_URL=http://localhost:8000`.

A successful ingestion exposes saved video metadata and transcript segments, plus valid AI analysis (which may contain zero moments). If moments exist, create one clip and verify preview/download and captions. YouTube bot checks, unavailable media, missing speech/models, or provider failure should produce FAILED with a safe stage message. See [API usage](API.md) for inspecting results.
