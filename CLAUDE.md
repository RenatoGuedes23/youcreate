# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current state

All 5 build phases (see "Build phases" below) are implemented and validated end-to-end with real dubbing (Amazon Polly), including the video-trim feature (§ Frontend) and friendly error handling / logging (§ Quality bar). The original architecture spec (`youclone-arquitetura.md`, in Portuguese) is kept as historical reference; this file is the up-to-date source of truth. Google Cloud TTS was implemented earlier and then **removed** by explicit operator decision (2026-09) in favor of Polly alone — do not re-add it unless asked.

## Project goal

An app that takes an English `.mp4` and returns it localized to Brazilian Portuguese: `.srt` subtitles + PT-BR dubbing, no watermark. V1 is single-user and runs locally on the operator's machine; the architecture must stay clean enough to become a multi-user SaaS later without rewriting the core (see "Future seams" below) — but do not build those layers now.

## Locked decisions (do not revisit)

| Decision | Value |
|---|---|
| Output | Subtitles **and** dubbing, both |
| V1 scope | Single-user, local (multi-user is a future phase) |
| V1 environment | Operator's PC (Windows/Mac/Linux), no cloud |
| Language | Python (engine and backend) |
| Backend | FastAPI |
| Live progress | SSE (Server-Sent Events) |
| Frontend | Plain HTML/JS (must be swappable for React without touching the backend) |
| Transcription | faster-whisper (local, free) |
| Translation | Gemini via `google-genai` (free tier OK for dev); provider must be pluggable |
| TTS (dubbing) | **Amazon Polly** (only provider implemented; Google Cloud TTS was removed by request — see "Current state"); provider architecture stays pluggable for future options (Azure / ElevenLabs) |
| Audio/video | ffmpeg (system dependency) |

## Compliance / product rules (implement as default behavior)

- No watermark on any output.
- Do **not** implement AI frame recreation/regeneration — it degrades quality and is unnecessary when the operator holds a license. May exist later as an off-by-default flag; not part of v1.
- Do **not** implement lip sync — target content is narrated (face isn't the focus), and lip sync needs a heavy video model.
- Obtaining the source `.mp4` is the operator's responsibility. `yt-dlp` download is an optional step; the main flow assumes a local file already exists.
- The final README must remind the operator to mark content as "altered or synthetic" when uploading to YouTube (platform requirement for AI voice/audio) and to confirm commercial usage rights on the source content.

## Architecture principles

1. **Engine isolated from the web.** All processing lives in `engine/` as pure Python with zero FastAPI dependency (no `import fastapi` anywhere under `engine/`). The backend only wraps the engine — this lets the same core be driven by CLI, API, or a future queue worker.
2. **Pluggable steps.** Each pipeline step is a module behind a stable interface. Swapping providers (translation, TTS) or reordering steps must not require touching the orchestrator.
3. **Progress via callback.** The pipeline reports progress through `on_progress(step_id, label, pct, message)`. Consumers decide what to do with it (CLI prints, web streams over SSE) — the engine has no knowledge of HTTP.
4. **Local-first, seamed for scale.** Use simple local implementations (in-memory queue, disk storage) but behind interfaces that can later be swapped for Redis/worker and S3/MinIO (see "Future seams").

## Stack

Python 3.11+. `requirements.txt` should include: `fastapi`, `uvicorn[standard]`, `python-multipart` (backend); `faster-whisper` (local transcription); `google-genai` (Gemini translation); `boto3` (Polly TTS); `yt-dlp` (optional download); `pydantic` (models/validation).

System dependency (not pip): **ffmpeg** — document install per OS in the README.

Secrets via environment variables only, never hardcoded: `GEMINI_API_KEY`, plus AWS credentials for Polly (`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_DEFAULT_REGION`) — see "Pluggable providers" below for why these are read explicitly instead of via boto3's default chain.

## Directory structure (target)

```
engine/                     # ENGINE — pure Python, no web
│   ├── config.py           # env-based config (loads .env via python-dotenv)
│   ├── models.py           # dataclasses (Segment, PipelineResult)
│   ├── pipeline.py         # orchestrates steps + emits progress; PipelineError
│   ├── ffmpeg_utils.py     # shared run_ffmpeg/probe_duration/is_ffmpeg_available
│   ├── providers/          # pluggable implementations
│   │   ├── translate_gemini.py
│   │   ├── translate_base.py
│   │   ├── tts_polly.py    # Amazon Polly — only TTS provider implemented
│   │   └── tts_base.py     # abstract TTS interface
│   └── steps/
│       ├── download.py     # (1) yt-dlp — optional, not yet implemented
│       ├── trim.py         # (0b) optional clip cut (start+duration) before the pipeline
│       ├── audio.py        # (2) extract audio (ffmpeg)
│       ├── transcribe.py   # (3) faster-whisper (with timestamps)
│       ├── translate.py    # (4) calls the active translation provider
│       ├── subtitle.py     # (5) generate .srt
│       ├── dub.py          # (6) TTS + temporal fitting
│       └── render.py       # (7) remux with ffmpeg (subs + dubbed audio)
├── api/                    # BACKEND (FastAPI)
│   ├── main.py             # endpoints; sets up logging + ffmpeg startup check
│   ├── jobs.py             # job store (in-memory) — swappable interface
│   └── schemas.py          # request/response models
├── web/
│   └── index.html          # frontend: upload, trim bar (>1min videos), progress, downloads
├── cli.py                  # run the engine from the terminal, no web
├── logging_config.py       # shared logging setup for cli.py and api/main.py
├── storage/                # uploads / outputs / work (gitignored)
├── requirements.txt
├── .env.example
└── README.md
```

## Engine spec (`engine/`)

### Models (`models.py`)

```python
@dataclass
class Segment:
    start: float          # seconds
    end: float            # seconds
    text: str             # original text (EN)
    translation: str = "" # PT-BR (filled during translation step)

@dataclass
class PipelineResult:
    segments: list[Segment]
    srt_path: Path | None = None       # PT-BR subtitles
    dub_audio_path: Path | None = None # dubbed track
    video_out: Path | None = None      # final mp4 (subs + dub)
```

### Step interfaces (`steps/`)

- `download.download(url: str, out_dir: Path) -> Path` — optional, via yt-dlp.
- `audio.extract_audio(video_path: Path, work_dir: Path) -> Path` — `.wav` 16kHz mono via ffmpeg.
- `transcribe.transcribe(audio_path: Path) -> list[Segment]` — faster-whisper, `vad_filter=True`, source language from `config.SOURCE_LANG`.
- `translate.translate_segments(segments: list[Segment]) -> list[Segment]` — delegates to the active provider; translates **in one batch call** (all lines numbered, response as indexed JSON to preserve order). Fills `seg.translation`.
- `subtitle.build_srt(segments, out_path, translated=True) -> Path` — standard `.srt` (index, `HH:MM:SS,mmm --> ...`, text, blank line).
- `dub.synthesize_dub(segments, work_dir) -> Path` — see "Dubbing" below.
- `render.build_final(video, srt, dub_audio, out_path, opts) -> Path` — see "Final render" below.

### Pluggable providers (`providers/`)

```python
# translate_base.py
class Translator(Protocol):
    def translate_batch(self, texts: list[str]) -> list[str]: ...

# tts_base.py
class TTS(Protocol):
    def synthesize(self, text: str, voice: str) -> bytes: ...  # WAV/MP3 bytes
```

Provider selection happens via `config.TRANSLATE_PROVIDER` / `config.TTS_PROVIDER` through a simple factory (in `engine/steps/translate.py` and `engine/steps/dub.py`, respectively). Translation defaults to Gemini; `config.TTS_PROVIDER` only supports `"polly"` (`tts_polly.PollyTTS`) — Google Cloud TTS was implemented and then deliberately removed (see "Current state"). Polly returns headerless PCM from the AWS API, wrapped into a proper `.wav` via the stdlib `wave` module (PCM output only supports 8000/16000 Hz sample rates on Polly, unlike its mp3/ogg formats).

**Security-critical: Polly credentials are read explicitly, never via boto3's default chain.** `PollyTTS.__init__` requires `config.AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_REGION`, read only from this project's `.env` (see `engine/config.py`). It deliberately does **not** call bare `boto3.client("polly")` and does **not** support AWS CLI named profiles, because either path falls back to (or can be confused with) `~/.aws/credentials`'s `default` profile, shell env vars, or an IAM instance role — any of which could belong to an unrelated AWS account. This happened once during development (a test silently ran against the operator's employer AWS account because it was the only profile configured on the machine) and was tried again with an explicit `AWS_PROFILE` env var (which also broke: an empty `AWS_PROFILE=` in `.env` still sets the process env var, and boto3 then tries to resolve a profile literally named `""` and raises `ProfileNotFound` even when explicit keys are also passed) — both approaches were rejected by the operator in favor of literal keys only. If a new cloud provider/credential integration is ever added to this project (AWS, GCP, Azure, etc.), follow this same pattern — explicit, project-scoped credentials only, never the SDK's ambient-credential discovery, and prefer literal keys in `.env` over named-profile indirection.

### Dubbing — the trickiest part (`dub.py`)

Core problem: PT-BR speech tends to run longer than the English original, so the dubbed audio can overrun the segment's time slot. Approach:

1. For each `Segment`, generate TTS audio from `seg.translation`.
2. Compute the target duration `dur = seg.end - seg.start`.
3. **Fit to the time slot:**
   - If TTS clip > `dur`: speed up with ffmpeg `atempo` (cap ~1.3x to avoid a robotic sound; beyond that, allow slight bleed into the next gap).
   - If TTS clip < `dur`: pad with trailing silence.
4. **Assemble the full track:** build a silent track the length of the whole video and place each clip at its `seg.start` (via `adelay`/`amix` or concatenation with computed silence gaps). Result: a single `.wav` matching video length, each line in its correct spot.
5. Return the path to that dubbed track.

Relevant config: `DUB_VOICE` (PT-BR provider voice), `DUB_MAX_SPEEDUP` (e.g. 1.3), `DUB_KEEP_MUSIC` (bool — see render).

### Final render (`render.py`)

`build_final` must produce a single mp4 combining:
- the original video (image untouched);
- **dubbed audio** replacing the original voice:
  - if `DUB_KEEP_MUSIC=True`, **mix** the dubbed track with the original audio ducked down (e.g. -18 dB) to preserve background music/ambience;
  - if `False`, replace the whole audio track with the dub.
- **subtitles**, burned in (hardsub via the `subtitles=` filter) or attached as a soft track — expose as `BURN_SUBS` (default: burn, to guarantee display in the YouTube/Shorts feed).

Useful helpers to build now: `burn_subtitles(video, srt, out)` and an audio-mixing helper. All ffmpeg calls go through `subprocess` with `check=True`.

### Orchestrator (`pipeline.py`)

```python
def run(video_path: Path,
        on_progress: Callable[[str,str,int,str], None] = noop,
        make_subs: bool = True,
        make_dub: bool = True) -> PipelineResult:
    # order: audio → transcribe → translate → subtitle → dub → render(final)
    # emit on_progress at each step with increasing pct and a clear PT-BR message
```

The orchestrator knows nothing about HTTP or jobs — only the callback.

`run` wraps the whole pipeline in a try/except and, on any failure, raises
`PipelineError(message, partial_result)` — `partial_result` is the
`PipelineResult` built so far (e.g. `srt_path` already set even if dubbing
later fails). Callers (`cli.py`, `api/jobs.py`) catch `PipelineError`
specifically to recover whatever artifacts were already produced instead of
losing them when a downstream step fails.

## Backend (`api/`)

Endpoints:
- `POST /api/jobs` (multipart, `file` field): saves the upload to `storage/uploads/`, creates a Job, kicks off background processing, returns `{ "id": <job_id> }`.
- `GET /api/jobs/{id}`: current state `{ status, pct, step, message, video, srt }`.
- `GET /api/jobs/{id}/events`: **SSE** stream of progress events (`data: {step,pct,message}\n\n`) plus a final `{done:true,status}` event.
- `GET /api/download/{filename}`: serves files from `storage/outputs/`.
- `GET /`: serves `web/index.html`.

Job model (`jobs.py`), v1 in-memory, **behind a swappable interface**:
```python
@dataclass
class Job:
    id: str; video_path: Path
    status: str = "queued"   # queued|running|done|error
    pct: int = 0; step: str = ""; message: str = ""
    result_video: str = ""; result_srt: str = ""
    events: Queue = ...      # progress queue feeding the SSE stream
def create(video_path) -> Job   # creates + starts a thread/worker
def get(job_id) -> Job | None
```
The runner injects an `on_progress` that does `job.events.put({...})` and updates the job fields. On completion it puts `None` on the queue to close the SSE stream.

## Frontend (`web/index.html`)

Single page, dark theme, mobile-friendly: `.mp4` file picker, "Translate and subtitle" button, progress bar fed by `EventSource` against the SSE endpoint, and download links for the `.mp4` and `.srt` on completion. No framework in v1 — must be trivial to later replace with React without backend changes.

**Trim bar for videos over 1 minute.** On file selection, JS reads the video's duration locally (`<video>` + `loadedmetadata`, via `URL.createObjectURL` — no upload needed just to check). If duration > 60s, a draggable trim bar appears below the upload area: a fixed 60s window the user drags along a timeline, with a `<video>` preview that seeks to match (`previewVideo.currentTime = clipStart`) so the user sees exactly what they're selecting. On submit, `start` and `clip_duration` are sent as extra multipart form fields alongside `file`. The backend (`api/main.py`) trims the upload via `engine/steps/trim.py` (`ffmpeg -ss <start> -i <file> -t <duration> -c copy`, fast/keyframe-aligned, not frame-precise) **before** calling `jobs.create()` — only the trimmed clip goes through the heavy pipeline (transcription/translation/TTS), which is the point (cost/time control), though the full original file is still uploaded first since the cut happens server-side, not in-browser.

## Configuration (`config.py` + `.env.example`)

Everything via env with sane defaults: storage folders; `SOURCE_LANG=en`, `TARGET_LANG=pt`; `WHISPER_MODEL=small` and device/compute settings; `TRANSLATE_PROVIDER`, `GEMINI_MODEL` (default `gemini-3.6-flash`), `TRANSLATE_STYLE` (translation style prompt — natural PT-BR, keeps proper nouns, adapts slang); `TTS_PROVIDER`, `DUB_VOICE`, `DUB_MAX_SPEEDUP`, `DUB_KEEP_MUSIC`, `BURN_SUBS`. Generate `.env.example` covering all of these.

## Future seams (leave ready, do NOT implement now)

Implement each of these as a thin interface/abstraction with only the local version wired up, so the scalable version is a later plug-in:

- **Job queue:** today thread + in-memory queue; interface should allow swapping in **Redis + worker (Celery/RQ)** without touching the endpoints.
- **Storage:** today local filesystem via a small paths module; later swappable for **S3/MinIO**.
- **Job persistence:** v1 in-memory; leave a seam for **SQLite/Postgres**.
- **Auth/accounts:** no login in v1; keep endpoints organized so auth middleware and job→user association can be inserted later.
- **Multi-user/billing:** out of scope — don't build it, just don't block it.

## Build phases (execute in this order)

Build and validate **one phase at a time**.

**Phase 0 — Scaffold.** Create the directory structure, `requirements.txt`, `.env.example`, `.gitignore`, `config.py`, `models.py`. Acceptance: `python -c "import engine"` runs with no error.

**Phase 1 — Engine: subtitles.** Implement `audio` → `transcribe` → `translate` → `subtitle` and `pipeline.run` up through the `.srt`. Implement `cli.py`. Acceptance: `python cli.py sample.mp4` produces a synced PT-BR `.srt`.

**Phase 2 — Engine: dubbing + final render.** Implement `providers/tts_polly`, `dub.synthesize_dub` (temporal fitting per the dubbing section above), and `render.build_final` (audio mix/replace + hardsub). Acceptance: the CLI produces a final `.mp4` that is both subtitled and dubbed, with lines landing at the right time.

**Phase 3 — Backend + progress.** Implement `api/` (upload, status, SSE, download) and the in-memory job store. Acceptance: `uvicorn api.main:app` starts; `POST /api/jobs` processes a job and SSE streams the progress bar to 100%.

**Phase 4 — Frontend.** Implement `web/index.html` (upload + `EventSource` progress bar + downloads). Acceptance: the full flow works end-to-end from the browser at `localhost:8000`.

**Phase 5 — Polish.** Full README (ffmpeg install per OS, obtaining API keys, YouTube AI-disclosure reminder), friendly error handling, logging. Acceptance: someone new can install and run the project from the README alone.

## Out of scope (v1)

AI frame recreation; lip sync; login/multi-user; billing; cloud deploy; distributed queue. (All are anticipated future evolution — see "Future seams".)

## Logging

`logging_config.setup_logging(log_file)` configures a console handler plus an
optional file handler (timestamped, `%(levelname)s`/`%(name)s` included).
`cli.py` logs to `youcreate.log` at the repo root; `api/main.py` logs to
`storage/youcreate.log` and also does a startup check (`is_ffmpeg_available()`)
that logs a warning if ffmpeg/ffprobe aren't on PATH. `engine/pipeline.py` logs
the full traceback via `logger.exception(...)` before re-raising as
`PipelineError`, so the log file has full detail even when the user-facing
message is a short PT-BR summary.

## Quality bar

- Engine must be 100% independent of the web layer (no `import fastapi` anywhere under `engine/`).
- Every ffmpeg call uses `check=True` with clear error capture.
- No hardcoded secrets — everything via env.
- Progress and error messages are in **Portuguese**, clear to the operator.
- Comment code only at non-obvious points (dubbing's temporal fitting, audio mixing, SRT format).

## Commands

- Activate the venv first: `source .venv/bin/activate` (Windows: `.venv\Scripts\activate`)
- Install deps: `pip install -r requirements.txt` (plus system ffmpeg, installed separately per OS — see README)
- Run the engine standalone: `python cli.py caminho/do/video.mp4 [--no-dub] [--no-subs]`
- Run the backend: `uvicorn api.main:app --reload`, then open `http://localhost:8000`
- Full setup/config walkthrough (obtaining `GEMINI_API_KEY` and AWS/Polly credentials): see `README.md`
