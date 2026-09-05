# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current state

All 5 original build phases (see "Build phases" below) are implemented and validated end-to-end with real dubbing (Amazon Polly), including the video-trim feature (§ Frontend) and friendly error handling / logging (§ Quality bar). The original architecture spec (`docs/youclone-arquitetura.md`, in Portuguese) is kept as historical reference; this file is the up-to-date source of truth. See `docs/ARQUITETURA.md` (design/why) and `docs/FLUXO.md` (request trace/how) for the current, detailed documentation. Google Cloud TTS was implemented earlier and then **removed** by explicit operator decision (2026-09) in favor of Polly alone — do not re-add it unless asked.

**YouTube URL is the only input.** File upload was removed entirely (CLI, web service, and web frontend) in favor of pasting a YouTube URL — the operator never handles the source file at all. Only `youtube.com`/`youtu.be`/`m.youtube.com`/`music.youtube.com` over http/https are accepted. Trimming (start + duration) is optional and uncapped — no fixed 60s window; the frontend's trim bar is a free two-handle range over the full duration, defaulting to the whole video. When a clip duration is given, the download step uses yt-dlp's `download_ranges` to fetch only that segment instead of the whole video.

**Three independent services, deliberately with zero shared source code (2026-09).** The original single FastAPI process (which called the engine in-process via a Python thread) was split into `webapp/` (the site) and `worker/` (the engine), talking to each other **only through Redis** — never through a shared import, a shared volume of code, or a direct function call. This was an explicit operator requirement: each of `webapp/` and `worker/` must be a self-contained folder that could be copied out and deployed on its own, with no reference to anything outside itself. Concretely:

- `worker/` owns the entire motor (`worker/engine/`, moved there wholesale) plus `worker/cli.py` (a way to run the engine standalone, no queue) and `worker/worker.py` (the queue consumer). It has its own `requirements.txt` (heavy: faster-whisper, google-genai, boto3, yt-dlp) and needs system ffmpeg.
- `webapp/` owns `main.py` (FastAPI), `schemas.py`, `web/index.html`, and two small standalone helpers: `youtube_probe.py` (a from-scratch yt-dlp metadata lookup, **not** a call into `worker/engine/`) and `queue_client.py` (the producer half of the Redis protocol). Its `requirements.txt` is light: fastapi, uvicorn, redis, yt-dlp, pydantic — no ffmpeg, no faster-whisper, no cloud SDKs.
- Each service has its **own** `queue_client.py` and its **own** `logging_setup.py` (identical utility, duplicated on purpose) rather than importing a shared module. `queue_client.py` is genuinely two halves of one Redis protocol (`webapp/queue_client.py`: `create_job`/`get_job`/`subscribe`; `worker/queue_client.py`: `get_job`/`update_job`/`publish_event`/`dequeue`) — **if the Redis key/field naming changes in one copy, it must change in the other, there is no compiler to catch drift.** This is a known, accepted trade-off in exchange for true deployability independence.
- Each service has its own `.env`/`.env.example`, `.gitignore`, `.dockerignore`, and `Dockerfile`, and its Docker build `context` is its own folder (not the repo root) — so an image literally cannot `COPY` anything from outside its own service directory.
- `docker-compose.yml` at the repo root is the **only** place that references both services together (that's orchestration, not code sharing — normal and expected).
- The `redis` service uses the stock `redis:7-alpine` image; there's no code for it in this repo.

**Deployment target shifted from laptop to a cloud VM.** Still single-user in the sense that there's one operator and no accounts/auth, but the queue now genuinely supports multiple concurrent jobs processed by multiple worker replicas (`docker compose up -d --scale worker=N`) — this was an explicit ask, not speculative scaling. Job state lives in Redis (hash `youcreate:job:<id>`, list `youcreate:queue`, pub/sub channel `youcreate:job:<id>:events`), with a 24h TTL on job metadata (`JOB_TTL_SECONDS`) so Redis doesn't grow unbounded — final results themselves live on disk (`storage/outputs/`, shared between `webapp` and `worker` containers via a Docker named volume), not in Redis. `worker/worker.py`'s `main()` sweeps any leftover subdirectory of `WORK_DIR` on startup (defensive: a worker container restart normally wipes its own ephemeral disk anyway, but this guards against a persistent-volume misconfiguration).

**A Redis client gotcha worth remembering:** `redis-py`'s blocking commands (`BLPOP` in `worker/queue_client.py::dequeue`) need the connection's `socket_timeout` set comfortably higher than the blocking command's own timeout — otherwise the client raises `redis.exceptions.TimeoutError` on the socket before Redis gets to reply with `nil` at the end of the block. Both `queue_client.py` copies set `socket_timeout=30` on the shared `redis.Redis` client for this reason (dequeue's timeout is 5s; `webapp`'s SSE loop also polls `pubsub.get_message(timeout=5.0, ...)`, same class of risk).

## Project goal

An app that takes a YouTube video (English) and returns it localized to Brazilian Portuguese: `.srt` subtitles + PT-BR dubbing, no watermark. Single operator, no accounts/billing; the architecture is already split into independently deployable/scalable services (see "Current state") without over-building auth/multi-tenancy that hasn't been asked for (see "Future seams" below).

## Locked decisions (do not revisit)

| Decision | Value |
|---|---|
| Output | Subtitles **and** dubbing, both |
| Input | YouTube URL only — no file upload anywhere (CLI/web service/frontend) |
| Scope | Single operator, no login/accounts; queue supports multiple concurrent jobs via worker replicas |
| Environment | Docker Compose on a cloud VM (`webapp` + `worker` + `redis`), or run each service locally without Docker |
| Language | Python (worker/engine and webapp) |
| Service boundary | `webapp` and `worker` share **zero source files** — communicate only via Redis (see "Current state") |
| Web framework | FastAPI (`webapp/main.py`) |
| Live progress | SSE (Server-Sent Events), backed by Redis pub/sub |
| Frontend | Plain HTML/JS (`webapp/web/index.html`), must be swappable for React without touching the backend |
| Transcription | faster-whisper (local, free) |
| Translation | Gemini via `google-genai` (free tier OK for dev); provider must be pluggable |
| TTS (dubbing) | **Amazon Polly** (only provider implemented; Google Cloud TTS was removed by request); provider architecture stays pluggable for future options (Azure / ElevenLabs) |
| Audio/video | ffmpeg (system dependency, only in `worker/`) |

## Compliance / product rules (implement as default behavior)

- No watermark on any output.
- Do **not** implement AI frame recreation/regeneration — it degrades quality and is unnecessary when the operator holds a license. May exist later as an off-by-default flag; not part of v1.
- Do **not** implement lip sync — target content is narrated (face isn't the focus), and lip sync needs a heavy video model.
- The source video is always a YouTube URL, downloaded by `worker/` via `yt-dlp`; there is no file-upload path anywhere in the project.
- The final README must remind the operator to mark content as "altered or synthetic" when uploading to YouTube (platform requirement for AI voice/audio) and to confirm commercial usage rights on the source content.

## Architecture principles

1. **Three independently deployable services, zero shared source.** `webapp/`, `worker/`, and `redis` (stock image) talk only over the network (HTTP from the browser to `webapp`; Redis protocol between `webapp` and `worker`). Neither service imports, mounts, or otherwise reads a file that lives in the other's folder. See "Current state" for the specific duplicated pieces (`queue_client.py`, `logging_setup.py`) and why.
2. **Engine isolated from the web, and now fully owned by the worker.** All processing lives in `worker/engine/` as pure Python with zero FastAPI dependency (no `import fastapi` anywhere under `engine/`). `worker/worker.py` and `worker/cli.py` are the only two callers.
3. **Pluggable steps.** Each pipeline step is a module behind a stable interface. Swapping providers (translation, TTS) or reordering steps must not require touching the orchestrator.
4. **Progress via callback inside the engine; via Redis between services.** `pipeline.run`'s `on_progress(step_id, label, pct, message)` callback has no knowledge of Redis or HTTP — `worker/worker.py` is what turns those callback calls into `queue_client.update_job` + `queue_client.publish_event` calls. `webapp` never calls the engine directly; it only reads what the worker wrote to Redis.
5. **Local-first, seamed for further scale.** Job queue and progress already run on Redis (not a future seam anymore). Final-output storage is a local/VM disk shared via a Docker volume; only reach for S3/MinIO if this becomes genuinely multi-host (see "Future seams").

## Stack

Python 3.11+, two independent dependency sets:

- `webapp/requirements.txt`: `fastapi`, `uvicorn[standard]`, `redis`, `yt-dlp`, `pydantic`, `python-dotenv`.
- `worker/requirements.txt`: `redis`, `faster-whisper` (local transcription), `google-genai` (Gemini translation), `boto3` (Polly TTS), `yt-dlp` (download), `pydantic`, `python-dotenv`.

System dependency (not pip, only needed inside `worker/`'s image/environment): **ffmpeg**.

Secrets via environment variables only, never hardcoded: `GEMINI_API_KEY`, plus AWS credentials for Polly (`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_DEFAULT_REGION`) — both live only in `worker/.env` (the webapp never needs them). See "Pluggable providers" below for why these are read explicitly instead of via boto3's default chain.

## Directory structure

```
docker-compose.yml          # the ONLY file that references both services together
webapp/                      # SERVICE 1: the site -- fully self-contained
│   ├── Dockerfile           # build context = this folder
│   ├── requirements.txt     # light: fastapi, redis, yt-dlp, pydantic
│   ├── .env / .env.example  # REDIS_URL, STORAGE_DIR, OUTPUTS_DIR only
│   ├── main.py              # FastAPI app: /, /api/probe, /api/jobs, /api/jobs/{id}, /api/jobs/{id}/events, /api/download/{filename}
│   ├── schemas.py           # request/response Pydantic models
│   ├── queue_client.py      # Redis protocol, PRODUCER half (create_job/get_job/subscribe)
│   ├── youtube_probe.py     # standalone yt-dlp metadata lookup (NOT a call into worker/engine/)
│   ├── logging_setup.py     # own copy of the logging utility
│   └── web/
│       └── index.html       # frontend: YouTube URL input, optional uncapped trim bar, progress, downloads
└── worker/                    # SERVICE 2: the motor -- fully self-contained
    ├── Dockerfile            # build context = this folder; installs system ffmpeg
    ├── requirements.txt      # heavy: faster-whisper, google-genai, boto3, yt-dlp
    ├── .env / .env.example   # everything engine-related + REDIS_URL + storage paths
    ├── worker.py             # queue consumer: BLPOP -> pipeline.run -> publish progress -> cleanup
    ├── cli.py                # run the engine standalone from the terminal, no queue
    ├── queue_client.py       # Redis protocol, CONSUMER half (get_job/update_job/publish_event/dequeue)
    ├── logging_setup.py      # own copy of the logging utility
    └── engine/               # THE MOTOR -- pure Python, no web, no Redis
        ├── config.py         # env-based config (loads .env via python-dotenv)
        ├── models.py         # dataclasses (Segment, PipelineResult)
        ├── pipeline.py       # orchestrates steps + emits progress; PipelineError
        ├── ffmpeg_utils.py   # shared run_ffmpeg/probe_duration/is_ffmpeg_available
        ├── providers/        # pluggable implementations
        │   ├── translate_gemini.py
        │   ├── translate_base.py
        │   ├── tts_polly.py  # Amazon Polly -- only TTS provider implemented
        │   └── tts_base.py   # abstract TTS interface
        └── steps/
            ├── download.py   # (1) yt-dlp -- YouTube-only, probe() + download_video() with optional range clip
            ├── audio.py      # (2) extract audio (ffmpeg)
            ├── transcribe.py # (3) faster-whisper (with timestamps)
            ├── translate.py  # (4) calls the active translation provider
            ├── subtitle.py   # (5) generate .srt
            ├── dub.py        # (6) TTS + temporal fitting
            └── render.py     # (7) remux with ffmpeg (subs + dubbed audio)
```

`storage/outputs/` (final `.mp4` + `.srt`) is a Docker named volume mounted into both `webapp` and `worker` containers at `/app/storage/outputs` — the only thing genuinely shared between the two services, and it's data, not code (the worker produces files, the web service serves them for download; this is a normal producer/consumer handoff, not a violation of "no shared code"). `storage/work/` (per-job temp files: downloaded video, extracted audio, dub clips) is **not** shared — it's local/ephemeral to whichever worker container is processing that job, and is deleted in `worker.py`'s `finally` block when the job finishes.

## Engine spec (`worker/engine/`)

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

- `download.probe(url: str) -> dict` — `{duration, title}` via yt-dlp, without downloading (YouTube-only URL validation). Note: `webapp/youtube_probe.py` is a **separate, standalone reimplementation** of this same idea for the web service — see "Current state" on why they're not shared.
- `download.download_video(url: str, out_dir: Path, start: float = 0.0, duration: float | None = None) -> Path` — downloads via yt-dlp; when `duration` is given, uses `download_ranges` to fetch only that segment.
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

**Security-critical: Polly credentials are read explicitly, never via boto3's default chain.** `PollyTTS.__init__` requires `config.AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_REGION`, read only from `worker/.env` (see `engine/config.py`). It deliberately does **not** call bare `boto3.client("polly")` and does **not** support AWS CLI named profiles, because either path falls back to (or can be confused with) `~/.aws/credentials`'s `default` profile, shell env vars, or an IAM instance role — any of which could belong to an unrelated AWS account. This happened once during development (a test silently ran against the operator's employer AWS account because it was the only profile configured on the machine) and was tried again with an explicit `AWS_PROFILE` env var (which also broke: an empty `AWS_PROFILE=` in `.env` still sets the process env var, and boto3 then tries to resolve a profile literally named `""` and raises `ProfileNotFound` even when explicit keys are also passed) — both approaches were rejected by the operator in favor of literal keys only. If a new cloud provider/credential integration is ever added to this project (AWS, GCP, Azure, etc.), follow this same pattern — explicit, project-scoped credentials only, never the SDK's ambient-credential discovery, and prefer literal keys in `.env` over named-profile indirection.

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

All ffmpeg calls go through `subprocess` with `check=True`.

### Orchestrator (`pipeline.py`)

```python
def run(video_path: Path | None = None,
        on_progress: Callable[[str,str,int,str], None] = noop,
        make_subs: bool = True,
        make_dub: bool = True,
        work_dir: Path | None = None,
        source_url: str | None = None,
        url_clip_start: float = 0.0,
        url_clip_duration: float | None = None) -> PipelineResult:
    # order: [download if source_url] → audio → transcribe → translate → subtitle → dub → render(final)
    # emit on_progress at each step with increasing pct and a clear PT-BR message
```

`video_path` and `source_url` are mutually exclusive-ish (`source_url` wins by populating `video_path` internally after download) but at least one is required. In practice every current caller (`worker.py`, `cli.py`) passes only `source_url` — `video_path` is kept as an engine-level affordance (e.g. ad hoc scripts/tests against a local sample file).

The orchestrator knows nothing about HTTP, Redis, or jobs — only the callback.

`run` wraps the whole pipeline in a try/except and, on any failure, raises `PipelineError(message, partial_result)` — `partial_result` is the `PipelineResult` built so far (e.g. `srt_path` already set even if dubbing later fails). Callers (`cli.py`, `worker.py`) catch `PipelineError` specifically to recover whatever artifacts were already produced instead of losing them when a downstream step fails.

## Web service (`webapp/`)

Endpoints (`main.py`):
- `GET /api/probe?url=`: reads `{ duration, title }` for a YouTube URL via `youtube_probe.probe` (standalone, not a call into `worker/engine/`), without downloading it — used by the frontend to size the trim bar before submitting.
- `POST /api/jobs` (JSON body, `JobCreateRequest`: `{url, start?, clip_duration?}`): calls `queue_client.create_job` — writes the job hash to Redis and pushes its id onto `youcreate:queue`. Returns immediately with `{ "id": <job_id> }`; the download and processing happen entirely inside whichever `worker` picks it up.
- `GET /api/jobs/{id}`: reads current state `{ status, pct, step, message, video, srt }` from the Redis hash via `queue_client.get_job`.
- `GET /api/jobs/{id}/events`: **SSE** stream. Subscribes to the job's Redis pub/sub channel (`queue_client.subscribe`), checks current status first (handles the job having already finished before the subscribe call), then loops on `pubsub.get_message(timeout=5.0, ...)`, re-checking status on every timeout as a safety net against a missed final message. Emits `data: {step,pct,message}\n\n` per event, plus a final `data: {done:true,status}\n\n`.
- `GET /api/download/{filename}`: serves files from `OUTPUTS_DIR` (the shared Docker volume).
- `GET /`: serves `web/index.html`.

`queue_client.py` (producer half — see "Current state" for why this isn't imported from `worker/`):
```python
@dataclass
class JobRecord:
    id: str; source_url: str
    url_clip_start: float = 0.0; url_clip_duration: float | None = None
    status: str = "queued"   # queued|running|done|error
    pct: int = 0; step: str = ""; message: str = ""
    result_video: str = ""; result_srt: str = ""

def create_job(source_url, url_clip_start=0.0, url_clip_duration=None) -> str  # job id
def get_job(job_id) -> JobRecord | None
def subscribe(job_id) -> redis.client.PubSub
```

## Worker service (`worker/`)

`worker.py`'s `main()` loop: `queue_client.dequeue(timeout=5)` (BLPOP — atomic across replicas, each job consumed by exactly one worker) → `_process(job_id)`, which reads the job from Redis, calls `pipeline.run(...)` with an `on_progress` that does `queue_client.update_job(...)` + `queue_client.publish_event(...)`, and in a `finally` block `shutil.rmtree`s the job's `work_dir` regardless of success/failure — the downloaded video and every intermediate artifact are temporary; only `OUTPUTS_DIR` (shared volume) needs to survive.

`queue_client.py` (consumer half):
```python
def get_job(job_id) -> JobRecord | None
def update_job(job_id, **fields) -> None
def publish_event(job_id, event: dict) -> None
def dequeue(timeout=5) -> str | None   # BLPOP
```

Run multiple replicas for concurrent processing: `docker compose up -d --scale worker=N`.

## Frontend (`webapp/web/index.html`)

Single page, dark theme, mobile-friendly: a single YouTube URL input + "Carregar" button, "Traduzir e legendar" submit, progress bar fed by `EventSource` against the SSE endpoint, and download links for the `.mp4` and `.srt` on completion. No file picker, no upload mode — URL is the only input. No framework — must be trivial to later replace with React without backend changes.

**Trim bar — optional, uncapped.** After "Carregar" resolves via `GET /api/probe`, the trim bar always appears (any duration) with the full range pre-selected — leaving it untouched means the whole video is processed. It's a two-handle range (`#trim-window` with `.trim-grip.left`/`.trim-grip.right`), each independently draggable to narrow the clip, plus a body-drag to move the whole window; `setClipRange(start, end)` enforces `MIN_CLIP_SECONDS = 1` and recomputes `needsTrim` (`true` only when the range is narrower than the full video). On submit, `start`/`clip_duration` are included in the `POST /api/jobs` JSON body only when `needsTrim` is true. There's no local video preview (the source is a remote URL, not a local file) — just the track + time labels.

## Configuration (`.env` per service)

`webapp/.env` (see `webapp/.env.example`): `REDIS_URL`, `STORAGE_DIR`, `OUTPUTS_DIR`. Nothing else — the web service has no secrets.

`worker/.env` (see `worker/.env.example`): `REDIS_URL`, storage paths (`STORAGE_DIR`/`OUTPUTS_DIR`/`WORK_DIR`), `GEMINI_API_KEY`, AWS credentials, `SOURCE_LANG`/`TARGET_LANG`, `WHISPER_MODEL`/`WHISPER_DEVICE`/`WHISPER_COMPUTE_TYPE`, `TRANSLATE_PROVIDER`/`GEMINI_MODEL`/`TRANSLATE_STYLE`, `TTS_PROVIDER`/`DUB_VOICE`/`POLLY_ENGINE`/`DUB_MAX_SPEEDUP`/`DUB_KEEP_MUSIC`, `BURN_SUBS`.

In `docker-compose.yml`, both services get `REDIS_URL=redis://redis:6379/0` injected via `environment:` (overriding whatever's in the `.env` file, which defaults to `redis://localhost:6379/0` for non-Docker local runs).

## Future seams (leave ready, do NOT implement now)

- **Job persistence beyond Redis's TTL:** today Redis hashes with a 24h TTL; leave a seam for **SQLite/Postgres** if job history needs to outlive that window.
- **Shared-output storage across hosts:** today a Docker named volume on one VM (`storage/outputs/`), which only works because `webapp` and `worker` run on the same host. If this ever goes multi-host, swap it for **S3/MinIO** — don't build this now.
- **Auth/accounts:** no login; keep `webapp/main.py`'s endpoints organized so auth middleware and job→user association could be inserted later.
- **Multi-user/billing:** out of scope — don't build it, just don't block it.

## Build phases (historical — already complete)

**Phase 0 — Scaffold.** **Phase 1 — Engine: subtitles.** **Phase 2 — Engine: dubbing + final render.** **Phase 3 — Backend + progress.** **Phase 4 — Frontend.** **Phase 5 — Polish.** All done; see "Current state" for what's changed since (YouTube-only input, then the webapp/worker split). If revisiting from scratch, the same order still applies within `worker/engine/` before wiring up `webapp/`.

## Out of scope

AI frame recreation; lip sync; login/multi-user; billing; multi-host deploy (S3/MinIO for outputs). (All are anticipated future evolution — see "Future seams".)

## Logging

Each service has its own `logging_setup.py` (`setup_logging(log_file)`; console handler plus an optional file handler, timestamped, `%(levelname)s`/`%(name)s`). `worker/cli.py` logs to `youcreate.log` inside `worker/`; `webapp/main.py` logs to `storage/youcreate-web.log`; `worker/worker.py` logs to `storage/youcreate-worker.log` and also does a startup check (`is_ffmpeg_available()`) that logs a warning if ffmpeg/ffprobe aren't on PATH. `engine/pipeline.py` logs the full traceback via `logger.exception(...)` before re-raising as `PipelineError`, so the log file has full detail even when the user-facing message is a short PT-BR summary.

## Quality bar

- `webapp/` and `worker/` must not import anything from outside their own folder (see "Current state" — this is the whole point of the split).
- Engine (`worker/engine/`) must be 100% independent of the web layer (no `import fastapi`, no `import redis` anywhere under `engine/`).
- Every ffmpeg call uses `check=True` with clear error capture.
- No hardcoded secrets — everything via env, split by service (see "Configuration").
- Progress and error messages are in **Portuguese**, clear to the operator.
- Comment code only at non-obvious points (dubbing's temporal fitting, audio mixing, SRT format, the Redis `socket_timeout` gotcha).

## Commands

**Local, without Docker** (each service in its own venv, or one venv with both `-r webapp/requirements.txt -r worker/requirements.txt` installed):
- Site: `cd webapp && uvicorn main:app --reload` → http://localhost:8000 (needs a local Redis running, and `webapp/.env` with `REDIS_URL` pointing at it)
- Worker: `cd worker && python worker.py`
- Engine standalone, no queue: `cd worker && python cli.py <youtube-url> [--no-dub] [--no-subs] [--start N --duration N]`

**Docker Compose** (recommended — this is the actual deploy target):
- Start everything: `docker compose up -d --build`
- Scale worker replicas: `docker compose up -d --scale worker=3`
- Logs: `docker compose logs -f web` / `docker compose logs -f worker`
- Full setup/config walkthrough (obtaining `GEMINI_API_KEY` and AWS/Polly credentials): see `README.md`
