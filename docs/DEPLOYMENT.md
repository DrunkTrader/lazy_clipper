# Deployment

Use the [Docker quick start](../README.md#quick-start--docker) for installation. Run commands here from the repository root. Component topology and implementation details live in [Architecture](ARCHITECTURE.md#runtime-topology).

## Private-network access

The personal Compose deployment trusts Tailscale network permissions rather than per-user accounts. Set `FRONTEND_BIND_ADDRESS` to the server's Tailscale IPv4 address:

```bash
tailscale ip -4
```

Only `frontend` is published, on that address at port 5173. `api` and `postgres` have no published host ports. Every permitted tailnet client can access the workspace; restrict access through tailnet policy. Do not bind to `0.0.0.0` or an untrusted/public interface.

The frontend proxies `/api/`, `/docs`, `/openapi.json`, `/health`, and `/ready` to the internal API. Compose disables cross-origin access because requests are same-origin; local development uses `CORS_ALLOWED_ORIGINS`. CORS does not replace the private binding and network policy.

For database credentials, existing-volume upgrades, and backups, see [Database maintenance](DATABASE.md).

## LLM and frontend origins

Configure `LLM_BASE_URL`, `LLM_API_KEY`, and `LLM_MODEL` for an OpenAI-compatible gateway. FreeLLMAPI uses the same settings. A reachable remote or Tailscale gateway URL can be used directly.

For a gateway running on the Docker host at port 3001:

```env
LLM_BASE_URL=http://host.docker.internal:3001/v1
```

Compose maps `host.docker.internal` to the host gateway on Linux. The provider must be reachable from the API container. For non-Compose development on the same host, use `http://127.0.0.1:3001/v1`.

Keep `VITE_API_BASE_URL` empty for the Docker frontend's same-origin proxy. Set it only for a separate API origin and rebuild the frontend: Vite embeds it at build time. Local Vite defaults to `http://localhost:8000` when the variable is unset; Vite's environment is separate from the backend's root `.env`.

## Runtime policies

Supervised processing requires Linux, including Linux containers on other hosts. Run one API process per database/storage directory, and stop the old process and its job tree before replacing it. Source-duration limits are metadata admission rules, not measured RAM/disk quotas; tune budgets for the host and model.

The default capacity is two running jobs plus four waiting, shared by ingestion and clips. Full capacity returns HTTP 429 with `PROCESSING_BUSY` and `Retry-After: 5`; retries remain explicit user actions. Reusing an existing project or completed matching clip does not require a new slot. The read-only `/api/v1/processing` endpoint reports capacity and oldest queue age, not completion estimates.

Settings are defined in [`.env.example`](../.env.example) and passed into the API by Compose:

| Setting | Default | Meaning |
| --- | ---: | --- |
| `MAX_QUEUED_JOBS` | 4 | Waiting jobs in addition to two running jobs |
| `MAX_SOURCE_SECONDS` | 7200 | Known, finite, positive source duration required before download and on saved-stage retries |
| `MAX_CLIP_SECONDS` | 180 | Maximum requested clip range; may be lowered, not raised above 180 |
| `INGESTION_TIMEOUT_SECONDS` | 7200 | Running ingestion budget, including download, model loading, transcription, analysis, and persistence |
| `CLIP_TIMEOUT_SECONDS` | 960 | Running clip budget; FFmpeg derives an inner deadline using the margin below |
| `MEDIA_TIMEOUT_MARGIN_SECONDS` | 30 | Reserved inside each job budget so FFmpeg reports its own timeout before the supervisor deadline |
| `SUBMISSION_LOCK_TIMEOUT_SECONDS` | 1 | Maximum wait to enter serialized submission |
| `DATABASE_CONNECT_TIMEOUT_SECONDS` | 5 | PostgreSQL connection and pool-checkout deadline |
| `DATABASE_STATEMENT_TIMEOUT_SECONDS` | 10 | PostgreSQL statement deadline |
| `DATABASE_LOCK_TIMEOUT_SECONDS` | 3 | PostgreSQL lock wait; SQLite busy timeout |

Execution budgets exclude queue wait. FFmpeg derives its audio-extraction and clip-render subprocess deadlines from the corresponding job budget minus `MEDIA_TIMEOUT_MARGIN_SECONDS`; the outer process supervisor still owns and kills the complete job group at the configured total deadline. Transcription defaults to `small` / `cpu`; transcript windows default to 300 seconds with 45 seconds overlap. Overlap must remain smaller than the chunk window. LLM controls include temperature 0.2, 4000 maximum tokens, a 120-second timeout, and two transport retries. See `.env.example` for those settings and `MAX_MOMENTS`.

The supervisor passes only runtime plumbing such as `PATH`, cache/home, proxy, and certificate variables to the job guard. Database URLs/password files, LLM credentials, cookie paths, and other application settings remain in the private settings snapshot on stdin rather than the descendant environment.

Storage defaults to a 50 GiB project-tree budget, a 1 GiB free-space reserve, and 1 GiB per admitted job. These are admission checks, not hard quotas for out-of-band writes. See [Architecture](ARCHITECTURE.md#filesystem-artifacts) for scoped deletion and atomic artifacts.

## Startup and monitoring

```bash
docker compose up -d --build --wait
docker compose ps
docker compose logs --tail=100 api frontend
```

PostgreSQL health gates API startup, and API readiness gates frontend startup. `/health` checks API liveness; `/ready` checks the database and local processing availability without contacting YouTube or the LLM. PostgreSQL/API healthchecks run every three minutes after fast startup checks. Services use `restart: unless-stopped`.

Routine Uvicorn/Nginx access logs are disabled. Application logs retain project/clip IDs, stage events, and redacted diagnostics, excluding credentials, request payloads, validation inputs, and SQL parameters. See [Troubleshooting](TROUBLESHOOTING.md) for failure diagnosis.

## Image builds and dependencies

- The API image installs the tested resolution in [`constraints.txt`](../constraints.txt). yt-dlp/yt-dlp-ejs pins come from [`pyproject.toml`](../pyproject.toml); CPU Torch/Torchaudio wheels come from the PyTorch CPU index on both supported Linux architectures. Container behavior does not depend on host yt-dlp or `/usr/bin/yt-dlp`.
- Node.js 22 is included for EJS YouTube JavaScript challenge solving; FFmpeg/libass and ffprobe are included for media processing.
- Backend dependency installation is cached separately from source. BuildKit caches pip downloads, and build tools remain outside the runtime image. Source edits normally reuse the expensive transcription dependency layers.
- The frontend uses `npm ci`, TypeScript/Vite, and a static Nginx runtime; startup does not run npm or require host `node_modules`.
- The first build downloads large dependencies. The first transcription downloads its model into the reusable `whisper_cache` volume. Normal updates should reuse caches rather than use `--no-cache`.

Update dependency constraints deliberately. Rebuild and check `pip check`, imports, model loading, and synthetic/real media behavior; see [runtime verification](TROUBLESHOOTING.md#container-runtime-verification). Caption implementation and rendering details belong in [Architecture](ARCHITECTURE.md#caption-generation-and-highlighting).
