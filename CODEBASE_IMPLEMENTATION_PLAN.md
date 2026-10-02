# LazyClipper — Current Implementation Task

Read `PROJECT_DESCRIPTION.md` first to understand the intended MVP.

The goal of this task is NOT to redesign or over-engineer LazyClipper.

The goal is simple:

> Review what already exists, fix the current problems, implement the missing basic clip-generation functionality, and leave the project in a working runnable state.

Before doing anything, use `find-skills` to check whether any available skill is useful for this task. Use it only where it actually helps; do not introduce unnecessary complexity because of it.

---

## 1. Review the Existing Code First

Inspect the current repository and understand what is already implemented.

Check:

- backend
- frontend
- pipeline
- database models
- API routes
- YouTube ingestion
- FFmpeg integration
- WhisperX transcription
- LLM analysis
- project storage
- Docker/dependencies

Do NOT rewrite working code just because you would design it differently.

The current repository is the source of truth for what already exists.

---

## 2. Current Working Pipeline

The current intended flow is approximately:

YouTube URL
↓
YouTube ingestion
↓
metadata
↓
video download
↓
audio extraction
↓
WhisperX transcription
↓
transcript normalization/chunking
↓
LLM analysis
↓
candidate moments
↓
scoring/deduplication
↓
project workspace

Preserve this functionality.

---

## 3. Main Missing Feature

The system currently identifies candidate moments, but it does not yet properly turn those moments into actual short video files.

Implement the simplest working version of:

candidate moment
↓
FFmpeg
↓
9:16 MP4 short
↓
stored in project clips directory
↓
API can serve it
↓
frontend can preview/download it

Do not build a professional video editor.

Do not add:

- face tracking
- dynamic reframing
- subtitles
- animated captions
- B-roll
- transitions
- social publishing
- advanced editing
- new worker infrastructure

For now, just make clip generation work.

---

## 4. Clip Rendering

Use the existing FFmpeg setup.

Create or extend a small media/clipping service in the existing project structure.

It should:

- take the source video
- take start/end timestamps
- trim the requested section
- convert it to a vertical 9:16 format
- produce an MP4
- save it under the project's existing `clips/` directory

For the MVP:

- 9:16
- 1080x1920
- H.264
- AAC
- simple deterministic crop is enough

Do not spend time making the crop intelligent.

---

## 5. Persist Enough Information for Clips

Inspect the current database implementation.

Add only the minimum persistence needed to know:

- which project the clip belongs to
- which moment it came from
- start
- end
- clip status
- generated file path

Do not build a large new job system.

Do not introduce Redis/Celery/Dramatiq unless the existing code absolutely requires it.

Use the current architecture.

---

## 6. Pipeline Integration

After the existing candidate moments are generated:

1. Save the moment.
2. Generate the corresponding clip.
3. Save the clip information.
4. Continue processing.

A single failed clip should not destroy all successfully generated clips.

Keep error handling simple and clear.

The project should finish in a usable state even if one clip fails.

---

## 7. API

Add the minimum API needed to expose generated clips.

At minimum, support:

GET /api/v1/projects/{project_id}/clips

GET /api/v1/projects/{project_id}/clips/{clip_id}/media

Follow the existing API style.

Do not expose arbitrary filesystem paths.

Serve only clips belonging to the requested project.

---

## 8. Frontend

Keep the existing frontend.

Do not redesign the application.

Add only what is necessary to:

- show generated clips
- show their status
- preview a READY clip
- download a READY clip
- show a useful error for a failed clip

Keep the existing:

- project workspace
- source video
- transcript
- suggested moments
- moment seeking

The user should be able to go from:

Suggested Moment
→ generated short
→ Preview
→ Download

---

## 9. Browser Refresh Problem

Investigate the current refresh behavior.

A browser refresh on:

`?project_id=<id>`

must reload the existing project.

It must NOT:

- create a new project
- call POST /ingest again
- restart processing
- download the YouTube video again
- regenerate the transcript
- regenerate the moments

Find the real cause in the existing code and fix it.

Do not add a complicated state-management system just for this.

---

## 10. Reuse Existing Files

If a project already has a valid downloaded source video, do not download it again unnecessarily.

Likewise, do not recreate existing generated clips just because the frontend refreshed.

Prefer simple existence/state checks.

---

## 11. Dependencies / Docker

Only change dependencies or Docker configuration when required for the implementation.

Make sure FFmpeg is actually available in the runtime where the backend runs.

Do not add unrelated dependencies.

Do not introduce a new infrastructure layer.

---

## 12. Verification

Before finishing, actually run the project and verify the important path.

At minimum verify:

1. Backend starts.
2. Frontend starts.
3. A project can be created.
4. Processing reaches the existing transcript/moment stage.
5. A candidate clip can be rendered.
6. The generated MP4 exists.
7. The API can serve the clip.
8. The frontend can preview it.
9. The frontend can download it.
10. Refreshing the project page does not restart ingestion.

Do not spend time building an extensive test suite in this task.

Basic verification and existing tests are enough.

---

## 13. Keep Scope Tight

This is the most important instruction.

Do NOT over-engineer this task.

Do NOT:

- redesign the architecture
- migrate to Redis workers
- rewrite the frontend
- introduce complex state management
- build a professional clip editor
- add extensive observability
- build a comprehensive test framework
- refactor unrelated code
- implement future product features

The priority is:

> Make the current LazyClipper MVP actually run end-to-end.

---

## 14. Final Report

When finished, briefly report:

### What you found
Important problems in the current code.

### What you changed
Only the changes actually made.

### How clip generation works now
Briefly describe the new flow.

### Refresh issue
State what caused it and what was fixed.

### Verification
State what you actually ran and verified.

### Remaining limitations
Only mention things that genuinely remain.

Do not claim something was tested or implemented unless you actually verified it.