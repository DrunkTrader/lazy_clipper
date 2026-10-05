# API Usage

FastAPI `/docs` is the full endpoint/schema reference. In Compose use the private frontend origin; for local non-Compose development use `http://localhost:8000`:

```bash
BASE_URL=http://<FRONTEND_BIND_ADDRESS>:5173
```

Replace address/ID placeholders before running commands. GET routes are read-only. POST requests explicitly create, retry, or repair work.

## Create or reopen a project

```bash
curl -X POST "$BASE_URL/api/v1/ingest" \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.youtube.com/watch?v=VIDEO_ID"}'
```

The URL is validated/canonicalized. A new project returns immediately with its ID and queued status:

```json
{"project_id": "...", "status": "queued"}
```

Submitting the same video returns its existing project. An explicit submission retries a failed project or repairs a completed project's missing source while retaining valid saved stages. Opening `?project_id=...` or a **Saved projects** link uses only GET requests.

## Inspect saved results

```bash
curl "$BASE_URL/api/v1/projects/PROJECT_ID"
curl "$BASE_URL/api/v1/projects/PROJECT_ID/status"
curl "$BASE_URL/api/v1/projects/PROJECT_ID/transcript"
curl "$BASE_URL/api/v1/projects/PROJECT_ID/moments"
curl "$BASE_URL/api/v1/projects/PROJECT_ID/clips"
```

Status follows `QUEUED → INGESTING → TRANSCRIBING → ANALYZING → READY`, or FAILED. A requested clip temporarily puts its project in RENDERING, then returns it to READY. Clip statuses are QUEUED, RENDERING, READY, and FAILED. Failure stages and safe error fields are described in [Architecture](../ARCHITECTURE.md#public-errors-and-internal-diagnostics).

## Pagination and transcript projection

`GET /api/v1/projects?limit=25&offset=0` returns an array ordered by creation time and ID, newest first. Default limit is 25; maximum is 100. Offset ranges from 0 through 1,000,000.

`GET /api/v1/projects/PROJECT_ID/transcript?include_words=false` returns display segments without loading/returning word JSON. Omitting the flag or using `include_words=true` retains full word timings. The workspace uses the display projection and appends saved projects with **Load more projects**, retaining loaded entries after a later page failure.

## Render and download clips

`POST /api/v1/projects/PROJECT_ID/clips` accepts `moment_id`, `start`, and `end` from a saved moment. It queues only that requested clip, reuses a matching available READY output, and retries failed/missing outputs explicitly. For example, after replacing the IDs and times with saved values:

```bash
curl -X POST "$BASE_URL/api/v1/projects/PROJECT_ID/clips" \
  -H 'Content-Type: application/json' \
  -d '{"moment_id":"MOMENT_ID","start":15,"end":45}'
```

Generated media is served at `/api/v1/projects/PROJECT_ID/clips/CLIP_ID/media` with byte-range support. Add `?download=true` for an attachment response. Clip JSON exposes origin-relative `media_url` and `download_url`, never filesystem paths; the frontend resolves them against `VITE_API_BASE_URL` when a separate API origin is configured. These paths work through same-origin Nginx, non-default ports, and TLS termination without backend host/scheme inference.

The downloaded source is never served through a media endpoint. Original playback uses the project's YouTube video ID.

## Capacity and readiness

```bash
curl "$BASE_URL/health"
curl "$BASE_URL/ready"
curl "$BASE_URL/api/v1/processing"
```

The processing snapshot includes `workers`, `capacity`, `active`, `queued`, `reserved`, `quarantined`, `available`, `accepting`, and `oldest_queued_seconds`. Reservations are admitted requests not yet dispatched; quarantined work retains capacity after unconfirmed termination and closes admission. This reports queue state, not completion-time estimates. See [runtime policies](DEPLOYMENT.md#runtime-policies).

## Delete a project

**Destructive: DELETE removes the terminal project's database records and scoped media.** Active/owned projects cannot be deleted. Replace the project ID deliberately:

```bash
curl -X DELETE "$BASE_URL/api/v1/projects/PROJECT_ID"
```

## Errors and workspace behavior

Persisted project/clip failures use uppercase FAILED; HTTP error envelopes use lowercase `failed`. Failures expose safe `failed_stage`, structured `error`, and backward-compatible `error_message` fields. New errors store public messages; legacy raw errors are sanitized on read without rewriting saved rows. See the [error contract](../ARCHITECTURE.md#public-errors-and-internal-diagnostics) for an example and diagnostic policy.

The frontend loads project/transcript/moments/clips on open or explicit reload, polls only status every four seconds while processing, and stops at READY/FAILED. Clip-only refreshes reuse loaded transcript/moments; ingestion transitions/resubmission invalidate that cache. Requests time out after 30 seconds with a locally owned network message rather than arbitrary server/proxy text.

Successful JSON is checked for identities, required arrays/items, statuses, finite ordered ranges, and project/moment scope before state updates. Malformed responses yield safe endpoint-specific errors while independently loaded content remains usable. Unexpected React failures show a reload fallback; reload never automatically submits processing.
