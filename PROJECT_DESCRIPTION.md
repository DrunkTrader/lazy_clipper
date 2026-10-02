# LazyClipper — MVP Project Description

## 1. Product Definition

LazyClipper is an AI-assisted video clipping system focused on turning long-form YouTube videos into useful, timestamped short-form clip candidates.

The MVP should not attempt to be a complete video editor or social publishing platform.

The core product promise is:

> **YouTube URL → understand the video → identify strong moments → let the user inspect/select them → prepare a clip.**

The MVP should prioritize a reliable, modular processing pipeline over advanced editing features.

---

## 2. MVP Scope

### Core Pipeline

```text
YouTube URL
    ↓
Video Ingestion
    ↓
Metadata Extraction
    ↓
Transcript Generation
    ↓
Transcript Normalization / Segmentation
    ↓
AI Content Analysis
    ↓
Candidate Moment Detection
    ↓
Candidate Validation / Ranking
    ↓
Deduplication
    ↓
Project Workspace
    ↓
User Selects a Moment
    ↓
Clip Preparation
    ↓
 MVP Render
```

The first MVP milestone should stop around **clip selection and basic preparation**, with simple FFmpeg rendering added if practical.

---

## 3. Core User Experience

The user flow should be:

```text
1. Paste YouTube URL
2. Click "Create Project"
3. LazyClipper creates a project
4. Background pipeline processes the video
5. Project status updates through:
      Queued
      Ingesting
      Transcribing
      Analyzing
      Ready
6. Project workspace opens
7. User sees video metadata + transcript + AI moments
8. User clicks a moment
9. Video seeks to that timestamp
10. User opens the moment in the clip editor
11. User can adjust start/end
12. render creates the clip
```

Errors should produce an explicit:

```text
FAILED
```

state with a human-readable reason.

The frontend must never silently fall back to fake/demo project data when a real API request fails.

---

# 4. Recommended Architecture

```text
                    ┌─────────────────────┐
                    │      Frontend       │
                    │ React / TypeScript  │
                    │ Tailwind + shadcn   │
                    └──────────┬──────────┘
                               │
                               │ REST API
                               ↓
                    ┌─────────────────────┐
                    │      FastAPI        │
                    │   Application API   │
                    └──────────┬──────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
              ↓                ↓                ↓
       PostgreSQL          Redis/Queue      File Storage
       project state       jobs/state       media/audio
              │                │
              │                ↓
              │       ┌────────────────┐
              │       │ Worker Process │
              │       └───────┬────────┘
              │               │
              │      ┌────────┼──────────┐
              │      ↓        ↓          ↓
              │    yt-dlp   WhisperX   FFmpeg
              │
              └──────────────┐
                             ↓
                     AI / LLM Gateway
                             │
                     OpenAI-compatible API
                             │
                    ┌────────┴─────────┐
                    │                  │
               FreeLLMAPI            OpenAI
```

### Architectural Principle

LazyClipper should not depend directly on a specific LLM provider.

The AI layer should communicate through an OpenAI-compatible interface so the same application can use:

- FreeLLMAPI
- OpenAI
- Other OpenAI-compatible APIs
- vLLM
- LM Studio
- local model gateways
- future providers

The media pipeline should remain independent of the LLM provider.

---

# 5. Recommended Technology Stack

## Backend

- Python 3.12+
- FastAPI
- Pydantic v2
- SQLAlchemy 2
- Alembic
- httpx

FastAPI should expose the application API for:

- project creation
- project retrieval
- processing status
- transcripts
- suggested moments
- clips

---

## Database

### PostgreSQL

PostgreSQL stores application state and metadata.

Do not store large media files directly inside PostgreSQL.

Recommended logical entities:

```text
projects
videos
processing_jobs
transcript_segments
moments
clips
```

### Example Project

```text
projects
--------
id
title
source_url
status
created_at
updated_at
```

### Example Video

```text
videos
------
id
project_id
youtube_id
title
duration
thumbnail_url
source_path
audio_path
```

### Transcript

```text
transcript_segments
-------------------
id
project_id
start
end
text
speaker
segment_index
```

### Suggested Moment

```text
moments
-------
id
project_id
title
description
start
end
score
reason
source_segments
```

### Clip

```text
clips
-----
id
project_id
moment_id
start
end
status
output_path
```

---

# 6. Media Storage

For MVP, local filesystem storage is sufficient.

Suggested structure:

```text
storage/
└── projects/
    └── <project_id>/
        ├── source/
        │   └── video.mp4
        ├── audio/
        │   └── audio.wav
        ├── transcript/
        │   └── transcript.json
        └── clips/
            └── clip-001.mp4
```

Later this can be moved to:

- S3
- Cloudflare R2
- MinIO
- OCI Object Storage

The processing architecture should not depend on local storage forever.

---

# 7. Background Processing

Large video processing should not happen directly inside an HTTP request.

The API should create a project and enqueue a background job.

```text
POST /api/v1/ingest
        ↓
Create project
        ↓
Create processing job
        ↓
Return project_id
        ↓
Worker processes pipeline
```

### Recommended MVP Options

Simpler:

```text
FastAPI + dedicated worker process
```

More scalable:

```text
Redis + Dramatiq
```

or:

```text
Redis + Celery
```

For LazyClipper MVP, a Redis-backed worker architecture is a good direction.

---

# 8. Processing State Machine

Projects should have explicit processing states.

```text
CREATED
   ↓
INGESTING
   ↓
TRANSCRIBING
   ↓
ANALYZING
   ↓
READY
```

Failure from any processing state:

```text
ANY STATE
   ↓
FAILED
```

Persist the status in PostgreSQL.

Example:

```json
{
  "status": "TRANSCRIBING",
  "message": "Generating transcript"
}
```

Only show processing stages that are actually known.

Do not invent fake progress percentages.

---

# 9. YouTube Ingestion

## yt-dlp

Use `yt-dlp` as the primary YouTube integration.

Responsibilities:

- validate the YouTube URL
- extract video ID
- obtain metadata
- obtain thumbnail
- determine duration
- download media
- support audio extraction

Create an isolated service such as:

```text
backend/services/youtube/
```

Conceptually:

```text
YoutubeService
├── validate_url()
├── get_metadata()
├── download_video()
└── extract_audio()
```

The rest of the application should not call yt-dlp directly.

---

# 10. FFmpeg

FFmpeg is the base media-processing tool.

Responsibilities:

- extract audio
- normalize audio
- convert formats
- trim video
- render clips
- generate thumbnails
- prepare media for future captioning/cropping

FFmpeg should be treated as a system-level dependency and wrapped behind a small media service.

---

# 11. Transcription

## WhisperX

Use WhisperX for timestamped transcription.

Pipeline:

```text
YouTube video
      ↓
FFmpeg
      ↓
audio.wav
      ↓
WhisperX
      ↓
timestamped transcript
```

Prefer preserving:

- segment timestamps
- word timestamps
- alignment data

Word timestamps are valuable later for:

- precise clipping
- subtitles
- caption highlighting
- transcript seeking
- fine-grained editing

---

# 12. Transcript Representation

Do not store the transcript only as one large string.

Use structured segments.

Example:

```json
{
  "start": 125.42,
  "end": 133.91,
  "text": "The first thing I changed was my workflow."
}
```

Where available, preserve word timestamps:

```json
{
  "start": 125.42,
  "end": 133.91,
  "text": "The first thing I changed was my workflow.",
  "words": [
    {
      "word": "The",
      "start": 125.42,
      "end": 125.63
    }
  ]
}
```

---

# 13. Transcript Normalization

Raw transcription output should pass through a normalization stage before AI analysis.

```text
Raw WhisperX
    ↓
Remove invalid/empty segments
    ↓
Normalize whitespace
    ↓
Merge tiny segments
    ↓
Split excessively long segments
    ↓
Preserve timestamps
    ↓
Normalized transcript
```

The goal is to create semantically useful chunks while preserving accurate time ranges.

---

# 14. AI Analysis Pipeline

Do not use a single massive LLM prompt such as:

> "Watch this transcript and tell me the best clips."

Use a staged pipeline.

```text
Transcript
    ↓
Chunking
    ↓
Content Understanding
    ↓
Candidate Extraction
    ↓
Candidate Validation
    ↓
Candidate Scoring
    ↓
Timestamp Deduplication
    ↓
Final Moments
```

This gives the application more control and makes AI output easier to debug.

---

# 15. Transcript Chunking

Long videos can exceed model context limits.

Split the transcript into overlapping windows.

Example:

```text
Window: 5 minutes
Overlap: 30–60 seconds
```

Conceptually:

```text
0:00 ───── 5:00
        4:30 ───── 9:30
                 9:00 ───── 14:00
```

The overlap prevents important statements near chunk boundaries from being lost.

Make chunk size configurable.

---

# 16. Candidate Moment Detection

The first LLM stage finds potential short-form moments.

The model should look for:

- strong hooks
- surprising statements
- contrarian ideas
- clear insights
- stories
- arguments
- emotional moments
- useful explanations
- memorable statements
- strong conclusions
- questions with satisfying answers

The objective is not simply "viral content".

A strong candidate should contain a coherent idea that can stand on its own.

---

# 17. Structured Candidate Output

AI responses should always be structured.

Example:

```json
{
  "candidates": [
    {
      "start": 618.2,
      "end": 684.4,
      "title": "The question that changes everything",
      "summary": "The speaker explains how changing one question changes their entire decision process.",
      "reason": "Strong standalone idea with a clear setup and payoff."
    }
  ]
}
```

Validate this output with Pydantic models.

Do not rely on parsing arbitrary prose.

---

# 18. Candidate Validation

A second AI stage should validate whether each candidate actually works as a standalone clip.

Evaluate:

- Does it make sense without previous context?
- Does it have a clear beginning?
- Is the core point understandable?
- Is there a payoff?
- Does the clip end naturally?
- Is it too dependent on preceding conversation?
- Is it too long or too short?

Weak candidates should be removed or adjusted.

---

# 19. Candidate Scoring

Use multiple dimensions rather than one opaque model-generated score.

Example:

```json
{
  "hook": 8,
  "clarity": 9,
  "standalone": 8,
  "novelty": 7,
  "emotional_interest": 6,
  "payoff": 9
}
```

The backend can then calculate a deterministic composite score.

Example:

```text
score =
0.25 * hook
+ 0.20 * clarity
+ 0.20 * standalone
+ 0.15 * novelty
+ 0.10 * emotional_interest
+ 0.10 * payoff
```

The exact weights can be tuned later.

This makes ranking more explainable and easier to change.

---

# 20. Candidate Deduplication

Different transcript chunks may identify the same moment.

Example:

```text
Candidate A: 10:02–10:48
Candidate B: 10:15–11:01
Candidate C: 10:19–10:53
```

These should become one final candidate.

Use deterministic time-interval overlap logic first.

Do not waste LLM calls on simple timestamp deduplication.

---

# 21. Final AI Moments

The final output should contain a small number of high-quality candidates.

For MVP:

```text
5–10 moments
```

Each moment should contain:

```text
id
title
description
start
end
duration
score
reason
```

Optional structured details:

```text
hook
payoff
standalone
clarity
```

---

# 22. OpenAI-Compatible LLM Layer

The LLM integration should be isolated behind a service.

Suggested structure:

```text
backend/services/llm/
├── client.py
├── models.py
├── analyzer.py
└── prompts/
    ├── candidate_detection.txt
    ├── candidate_validation.txt
    ├── candidate_scoring.txt
    └── title_generation.txt
```

The rest of the application should depend on an internal abstraction such as:

```text
LLMClient
```

rather than directly depending on OpenAI or FreeLLMAPI.

---

# 23. FreeLLMAPI Integration

FreeLLMAPI should be treated as an OpenAI-compatible AI gateway.

Conceptually:

```text
LazyClipper
      │
      │ OpenAI-compatible HTTP
      ↓
FreeLLMAPI
      │
      ├── Model A
      ├── Model B
      └── Model C
```

The application should only need:

```text
BASE_URL
API_KEY
MODEL
```

Example configuration:

```env
LLM_PROVIDER=openai_compatible

LLM_BASE_URL=http://127.0.0.1:3001/v1
LLM_API_KEY=your-key
LLM_MODEL=your-model-id

LLM_TEMPERATURE=0.2
LLM_MAX_TOKENS=4000
LLM_TIMEOUT=120
LLM_MAX_RETRIES=2
```

If FreeLLMAPI is remote over a private/Tailscale network:

```env
LLM_BASE_URL=http://100.x.x.x:3001/v1
```

The application should not need any other changes.

---

# 24. OpenAI Python SDK

Use the official OpenAI Python client as the compatibility layer where appropriate.

Conceptually:

```python
from openai import OpenAI

client = OpenAI(
    api_key=settings.llm_api_key,
    base_url=settings.llm_base_url,
)
```

The same LazyClipper LLM layer can then point to:

```text
FreeLLMAPI
OpenAI
other OpenAI-compatible providers
```

without changing pipeline code.

---

# 25. Model Roles

Define logical model roles instead of hard-coding one model everywhere.

Example:

```env
LLM_ANALYSIS_MODEL=...
LLM_SUMMARY_MODEL=...
LLM_METADATA_MODEL=...
```

The MVP can use the same model for all roles.

Later, different workloads can use different models:

```text
fast model
    ↓
chunk classification

strong model
    ↓
candidate validation/ranking

small model
    ↓
title generation
```

---

# 26. LLM Reliability

The AI layer should handle:

- timeouts
- temporary network failures
- rate limiting
- malformed output
- missing fields
- oversized context
- unavailable models

Suggested behavior:

```text
429 / rate limit → bounded retry
timeout → bounded retry
malformed structured output → one correction/retry
invalid request → fail cleanly
```

Do not retry indefinitely.

---

# 27. Prompt Management

Do not place complex prompts throughout Python code.

Store prompts in:

```text
backend/services/llm/prompts/
```

This allows prompt iteration without modifying core processing logic.

Prompts should define the reasoning task while the backend injects transcript/project data.

---

# 28. API Design

Keep the MVP API small and predictable.

## Ingest

```http
POST /api/v1/ingest
```

Request:

```json
{
  "url": "https://youtube.com/watch?v=..."
}
```

Response:

```json
{
  "project_id": "...",
  "status": "queued"
}
```

## Project

```http
GET /api/v1/projects/{project_id}
```

## Projects

```http
GET /api/v1/projects
```

## Transcript

```http
GET /api/v1/projects/{project_id}/transcript
```

## Moments

```http
GET /api/v1/projects/{project_id}/moments
```

## Status

```http
GET /api/v1/projects/{project_id}/status
```

## Clip

```http
POST /api/v1/clips
```

Example:

```json
{
  "project_id": "...",
  "start": 618.2,
  "end": 684.4
}
```

The exact endpoint set should follow the actual implementation, but these represent the intended MVP API boundary.

---

# 29. Frontend Stack

Recommended:

- React
- TypeScript
- Vite or existing Next.js setup
- Tailwind CSS
- shadcn/ui
- Radix UI
- TanStack Query

If the current project already uses Next.js, do not migrate just for preference.

---

# 30. Frontend Data Flow

Use TanStack Query for server state:

```text
projects
transcript
moments
project status
```

Use local React state for UI state:

```text
selected moment
sidebar state
search query
modal state
player position
```

Avoid storing all server responses in a giant global state store.

---

# 31. UI Component Library

Use shadcn/ui + Radix for core primitives:

- Button
- Input
- Dialog
- Drawer
- Tabs
- Tooltip
- Badge
- Dropdown
- Command/search
- Alert
- Skeleton
- Progress

The frontend should have reusable design primitives instead of one-off styling.

---

# 32. New Project UI

The New Project page should be simple and responsive.

Desktop:

```text
Sidebar
   |
   └── Main
        ├── URL Input
        ├── Create Project
        └── Processing State
```

Mobile:

```text
Top Navigation
      ↓
Project Creation Card
      ↓
YouTube URL
      ↓
Create
```

The frontend should work across:

```text
320px
375px
768px
1024px
1280px+
```

Do not just shrink the desktop layout.

---

# 33. Project Workspace

The workspace should focus on four areas:

```text
┌─────────────────────────────────────────────┐
│ Project Header                              │
├──────────────────────────┬──────────────────┤
│                          │                  │
│      Video Player        │ Suggested        │
│                          │ Moments          │
│                          │                  │
├──────────────────────────┴──────────────────┤
│ Timeline                                    │
├─────────────────────────────────────────────┤
│ Transcript                                  │
└─────────────────────────────────────────────┘
```

On mobile:

```text
Project Header
      ↓
Video
      ↓
Controls
      ↓
Suggested Moments
      ↓
Transcript
```

---

# 34. Transcript UX

Transcript must be real API data.

Required interactions:

- timestamp
- transcript text
- search
- active segment
- selected segment
- click timestamp → seek video
- scrolling
- responsive layout

Do not hardcode transcript data in production.

---

# 35. Suggested Moments UX

Each moment card should expose:

```text
title
description
start time
end time
duration
score/relevance
reason
open in editor
```

Only show scores that actually exist in the backend.

If no moments are ready:

```text
No moments generated yet
```

Do not show sample moments as if they were real.

---

# 36. Media Player

The MVP player should support:

- play/pause
- seeking
- current timestamp
- duration
- transcript → seek
- moment → seek
- selected moment state

Do not build fake controls.

If the backend does not yet serve playable media, clearly separate supported and unsupported behavior.

---

# 37. Clip Editor — MVP

Do not build a professional multi-track editor.

The MVP editor only needs:

```text
source video
start timestamp
end timestamp
trim selection
preview selected region
```

Conceptually:

```text
[------------- Full Timeline -------------]
       [------ Selected Clip ------]
       start                   end
```

The selected range becomes the clip definition.

---

# 38. Basic Rendering

Use FFmpeg to render the selected range.

Pipeline:

```text
Selected clip range
       ↓
FFmpeg
       ↓
clip.mp4
```

Advanced operations can come later:

- vertical 9:16 crop
- subtitles
- captions
- branding
- B-roll
- animations
- automatic reframing

These are not core MVP requirements.

---

# 39. What Is Explicitly Out of Scope for MVP

Do not prioritize:

- TikTok publishing
- Instagram publishing
- YouTube publishing
- billing
- subscriptions
- multi-user teams
- collaboration
- advanced subtitle styling
- AI avatars
- AI voiceovers
- automatic B-roll
- face tracking
- multi-track editing
- advanced transitions
- social analytics
- recommendation feeds

These can be future product layers.

---

# 40. Recommended Repository Structure

```text
LazyClipper/
│
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── api/
│   │   │   └── v1/
│   │   ├── models/
│   │   ├── schemas/
│   │   ├── services/
│   │   │   ├── youtube/
│   │   │   ├── transcription/
│   │   │   ├── media/
│   │   │   ├── llm/
│   │   │   └── clipping/
│   │   ├── workers/
│   │   └── db/
│   │
│   └── migrations/
│
├── frontend/
│   └── src/
│       ├── components/
│       ├── pages/
│       ├── features/
│       │   ├── projects/
│       │   ├── transcript/
│       │   ├── moments/
│       │   └── editor/
│       ├── hooks/
│       └── lib/
│           └── api/
│
├── storage/
├── scripts/
├── docker-compose.yml
├── README.md
├── PROJECT_DESCRIPTION.md
└── .env.example
```

---

# 41. Recommended Libraries Summary

## Backend

```text
FastAPI
Pydantic
SQLAlchemy
Alembic
PostgreSQL
Redis
httpx
```

## Media

```text
yt-dlp
FFmpeg
```

## Transcription

```text
WhisperX
faster-whisper
PyTorch
```

## AI

```text
openai
httpx
Pydantic
```

## Frontend

```text
React
TypeScript
Tailwind CSS
shadcn/ui
Radix UI
TanStack Query
```

## Optional Later

```text
Dramatiq / Celery
S3 / Cloudflare R2 / MinIO
Sentry
Prometheus
```

---

# 42. AI Gateway Model

FreeLLMAPI should sit outside the core application as a replaceable provider.

```text
                     LazyClipper
                          │
                          │ OpenAI-compatible API
                          ▼
                     FreeLLMAPI
                          │
               ┌──────────┼──────────┐
               ▼          ▼          ▼
             Model A    Model B    Model C
```

The application only needs:

```text
LLM_BASE_URL
LLM_API_KEY
LLM_MODEL
```

This makes switching providers a configuration change instead of an architectural change.

---

# 43. Important Engineering Principles

### 1. Deterministic media pipeline

The following should not depend on the LLM:

- downloading
- metadata extraction
- audio extraction
- timestamps
- transcript storage
- interval calculations
- deduplication
- clipping
- FFmpeg rendering

### 2. Replaceable AI layer

Only the AI service should care about:

- model name
- provider
- API base URL
- API key
- generation parameters

### 3. Structured AI outputs

Use Pydantic/JSON Schema and validate every response.

### 4. Explicit state

Every long-running stage needs:

```text
status
started_at
completed_at
error
```

### 5. No hidden mock fallback

Production frontend behavior should always be:

```text
API success → real data
API loading → loading UI
API error → error UI
API empty → empty UI
```

Never:

```text
API error → fake demo project
```

---

# 44. End-to-End MVP Pipeline

The complete product should implement:

```text
                    USER
                      │
                      │ YouTube URL
                      ▼
               ┌──────────────┐
               │ LazyClipper  │
               └──────┬───────┘
                      │
                      ▼
                 yt-dlp
                      │
              ┌───────┴────────┐
              │                │
          Metadata           Video
              │                │
              │             FFmpeg
              │                │
              │              Audio
              │                │
              │            WhisperX
              │                │
              │          Transcript
              │                │
              └───────┬────────┘
                      ▼
                Normalization
                      │
                      ▼
                   Chunking
                      │
                      ▼
             OpenAI-Compatible LLM
                      │
             ┌────────┼─────────┐
             ▼        ▼         ▼
          Detect    Validate    Score
             │        │         │
             └────────┼─────────┘
                      ▼
                 Deduplicate
                      │
                      ▼
                Final Moments
                      │
                      ▼
              Project Workspace
                 │          │
                 │          └── Transcript
                 │
                 └── Suggested Moments
                            │
                            ▼
                       Clip Selection
                            │
                            ▼
                          FFmpeg
                            │
                            ▼
                         clip.mp4
```

---

# 45. MVP Success Criteria

LazyClipper MVP is successful when the following end-to-end workflow is reliable:

```text
YouTube URL
    ↓
Project Created
    ↓
Metadata Retrieved
    ↓
Video/Audio Available
    ↓
Transcript Generated
    ↓
Transcript Normalized
    ↓
AI Finds Candidate Moments
    ↓
Candidates Validated + Ranked
    ↓
Duplicate Moments Removed
    ↓
5–10 Strong Moments Available
    ↓
User Opens Project
    ↓
User Reads Transcript
    ↓
User Clicks a Moment
    ↓
Video Seeks to Timestamp
    ↓
User Adjusts Start/End
    ↓
Clip Can Be Rendered
```

The core product question the MVP should answer is:

> **Can LazyClipper reliably turn a long-form video into useful, timestamped, understandable clip candidates with minimal user effort?**

Everything else should remain secondary until this pipeline is reliable.

---

# 46. Final Recommended Stack

For the first serious LazyClipper MVP:

```text
Backend:
    Python
    FastAPI
    Pydantic
    SQLAlchemy
    Alembic
    PostgreSQL
    Redis

Video:
    yt-dlp
    FFmpeg

Transcription:
    WhisperX
    faster-whisper
    PyTorch

AI:
    OpenAI Python SDK
    FreeLLMAPI
    OpenAI-compatible API
    Pydantic structured outputs

Frontend:
    React
    TypeScript
    Tailwind CSS
    shadcn/ui
    Radix UI
    TanStack Query

Storage:
    Local filesystem for MVP
    S3/R2/MinIO/OCI later

Workers:
    Redis + Dramatiq/Celery or a dedicated worker process
```

The architecture should remain small enough to run locally on a single machine while being structured so that media storage, workers, and AI providers can be scaled independently later.
