# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current state

**Product pivot (2026-09): from "localize a video" to "a YouTube Shorts machine."** The original goal was to take any YouTube video and hand back a PT-BR localized copy of it. The operator redefined the product: it now exists to take videos that **already succeeded internationally** and reissue them as **Brazilian-market Shorts** — vertical, ~60s, dubbed and captioned. Everything downstream of that decision:

- **The "hybrid" approach was chosen deliberately, over recreating from scratch.** Three options were weighed: reuse the original clip as-is, rebuild a new video from scratch using the original only as a script/format reference, or a hybrid (original footage + new PT-BR narration + new edit). Cost was **not** the deciding factor — hybrid and from-scratch-with-stock both land at ~US$0.03/short (Whisper is local, OpenRouter is sub-cent, Polly neural on ~1300 chars is ~US$0.02); only AI *video* generation was disqualifying (~US$15–50/short, i.e. US$450–1500/month at one short/day) and is not built. The operator chose hybrid explicitly **because the short should look like the same video** — "não quero criar um video diferente". The Content ID / reused-content exposure that comes with that is a known, accepted operator decision; do not re-litigate it.
- **The trim cap is now REAL, revoking a previously locked decision.** "Optional and uncapped" is dead. A YouTube Short is vertical/square and **at most 180s** (raised from 60s in October 2024). `webapp/schemas.py::SHORT_MAX_SECONDS` rejects longer clips (422) and the frontend's trim bar clamps to it.
- **60 seconds is a second, separate hard line — and it is about music, not length.** A Short **longer than 60s** that uses non-royalty-free music gets a Content ID claim and is **blocked**. This matters here specifically because `DUB_KEEP_MUSIC` is now **on by default** (the operator wants the original soundtrack kept — "as musicas sao interessantes e nao quero ter que remover"). The frontend shows a warning when the selected range crosses 60s; it does not block, because the operator asked to keep control of the duration. Target is ~60s anyway (best-performing Shorts length is 30–60s).
- **`DUB_KEEP_MUSIC=true` does NOT mean "music only."** It ducks the **entire** original track by -18 dB — music *and* the original English voice — under the PT-BR dub. That is classic voice-over style, and it is what ships today by explicit decision ("comece com ducking para testar"). True music isolation would need a source-separation model (Demucs); `torch 2.14+cpu` is already in the worker image via `pyannote.audio`, so that is an incremental add, not a heavy one — **only build it if the operator says the English bleed bothers them.**
- **Source already in the target language is a first-class path (2026-09).** Picking a video that is already in Portuguese used to be impossible in the UI and broken underneath it. Three separate defects, all fixed: (a) `target_lang` was collected by the frontend, stored in Redis and returned by `GET /api/jobs/{id}` but **never reached the engine** — `worker.py` didn't pass it and `pipeline.run` had no such parameter, so the "Traduzir para" selector was decorative; (b) the frontend's `refreshLangSelects()` deliberately disabled the source language in the target select ("traduzir de um idioma para ele mesmo nao e um caso valido"), which forced the operator to mislabel a Portuguese video as English and hand Whisper the wrong language; (c) had it gotten through, `translate_openrouter.py`'s anti-echo guard (`_ECHO_THRESHOLD = 0.5`) would have read a *correct* PT→PT translation — which legitimately returns the original text — as the model echoing its input, burning `_MAX_ECHO_ATTEMPTS = 3` calls before failing with a confusing error. Now `pipeline._same_language(source, target)` (base code comparison, so `pt-BR` == `pt`; empty source means Whisper autodetect and therefore returns False, the safe direction) short-circuits: translation is skipped (`seg.translation = seg.text`), **dubbing and diarization are skipped entirely**, the original audio is kept, and the job still produces the reframed + captioned vertical MP4. It costs nothing beyond local Whisper and ffmpeg — no OpenRouter call, no Polly call.
- **Discovery and publishing are deliberately NOT built.** The operator pastes the link by hand (no YouTube Data API trending/ranking subsystem), and downloads the MP4 to upload manually. Direct YouTube publishing is a wanted *later* step ("depois podemos integrar"), so keep `webapp/main.py` seamed for it — it would be the project's first OAuth/user-credential integration.

All 5 original build phases (see "Build phases" below) are implemented and validated end-to-end with real dubbing (Amazon Polly), including the video-trim feature (§ Frontend) and friendly error handling / logging (§ Quality bar). The original architecture spec (`docs/youclone-arquitetura.md`, in Portuguese) is kept as historical reference; this file is the up-to-date source of truth. See `docs/ARQUITETURA.md` (design/why) and `docs/FLUXO.md` (request trace/how) for the current, detailed documentation. Google Cloud TTS was implemented earlier and then **removed** by explicit operator decision (2026-09) in favor of Polly alone — do not re-add it unless asked.

**Translation went through two providers before landing on OpenRouter as the sole one (2026-09).** First **Gemini** (`google-genai`) — worked, but the free tier hit a hard wall: `generativelanguage.googleapis.com/generate_content_free_tier_requests` caps at **20 requests/day per model**, a ceiling of ~20 videos/day regardless of length, no graceful degradation (`429 RESOURCE_EXHAUSTED`). Paying to lift that cap was not the direction chosen; `translate_gemini.py` and `google-genai` were deleted outright. It was replaced by **Amazon Translate** (`translate_aws.py`) — one `translate_text` call per subtitle segment, reusing the AWS credentials already required for Polly, no new secret needed. That also didn't stick: literal/context-blind translation in practice (e.g. "long trunks" → "baús longos" instead of "trombas compridas"), no prompt-based tone control (`TRANSLATE_STYLE` doesn't apply to a plain MT API). `translate_aws.py` was deleted too, once **OpenRouter** (`translate_openrouter.py`) proved out — a generic bridge to OpenRouter's OpenAI-compatible `/chat/completions` endpoint, so any model OpenRouter carries (Gemini included, ironically, just without the free-tier trap) can be swapped in purely via the `OPENROUTER_MODEL` env var (e.g. `deepseek/deepseek-v4-flash`) — no code change to switch models. Keeps the same single-call, numbered-batch prompt style the Gemini provider used, but asks for a JSON **object** (`{"translations": [...]}`) rather than a bare array, since `response_format: json_object` — the most broadly supported way to request structured JSON across different models — generally requires an object at the top level.

`TRANSLATE_PROVIDER=openrouter` is the only supported value today (and the default) — the `Translator` protocol/factory in `engine/steps/translate.py` is still pluggable, but there is currently exactly one implementation. Two lessons carried over from the two removals, both still true of `translate_openrouter.py`: retry-with-backoff on the specific transient/rate-limit codes the API actually returns (HTTP 429/5xx), not a blanket retry-everything (a 4xx like a bad API key won't succeed on a second attempt); and — learned specifically from OpenRouter's own multi-provider routing — a cheap model can return HTTP 200 with the original text echoed back instead of translated, so `translate_batch` also checks the fraction of lines identical to the source and retries (as a *different* failure class) if that fraction is too high.

**Frontend redesigned as 5 client-routed screens (2026-09).** The original single-panel frontend (URL input + inline progress/result/error all inside one form) was replaced by a 5-screen SPA per a detailed operator-supplied visual spec: Tela 1 (`/`, paste link), Tela 2 (`/configurar`, language/subtitle/trim options), Tela 3 (`/v/<id>`, processing), Tela 4 (`/v/<id>` done, result/player/downloads), Tela 5 (`/v/<id>` failed/cancelled, generic error component). This came with real backend support that didn't exist before: job cancellation (`PipelineCancelled`, checked only between pipeline steps), heuristic error classification (`error_code` on the job), WebVTT subtitle generation (`subtitle.build_vtt`, for the Tela 4 `<video><track>`), and HMAC-signed short-lived download URLs. See "Web service", "Worker service", and "Frontend" below for specifics. Two explicit operator decisions worth remembering: file cleanup stays **discard-on-pagehide** (not a fixed 24h expiration — Tela 4 deliberately has no "apagamos em 24h" messaging), and there is still **no accounts/quota/billing system** — cancellation is real, not a quota gate.

**YouTube URL is the only input.** File upload was removed entirely (CLI, web service, and web frontend) in favor of pasting a YouTube URL — the operator never handles the source file at all. Only `youtube.com`/`youtu.be`/`m.youtube.com`/`music.youtube.com` over http/https are accepted. Trimming (start + duration) is optional but **capped at 180s** since the Shorts pivot (this revokes the earlier "uncapped" decision — see "Current state"); the frontend's trim bar is a two-handle range that opens pre-selected to the first 180s (or the whole video, whichever is shorter). When a clip duration is given, the download step uses yt-dlp's `download_ranges` to fetch only that segment instead of the whole video.

**Three independent services, deliberately with zero shared source code (2026-09).** The original single FastAPI process (which called the engine in-process via a Python thread) was split into `webapp/` (the site) and `worker/` (the engine), talking to each other **only through Redis** — never through a shared import, a shared volume of code, or a direct function call. This was an explicit operator requirement: each of `webapp/` and `worker/` must be a self-contained folder that could be copied out and deployed on its own, with no reference to anything outside itself. Concretely:

- `worker/` owns the entire motor (`worker/engine/`, moved there wholesale) plus `worker/cli.py` (a way to run the engine standalone, no queue) and `worker/worker.py` (the queue consumer). It has its own `requirements.txt` (heavy: faster-whisper, google-genai, boto3, yt-dlp) and needs system ffmpeg.
- `webapp/` owns `main.py` (FastAPI), `schemas.py`, `web/index.html`, and two small standalone helpers: `youtube_probe.py` (a from-scratch yt-dlp metadata lookup, **not** a call into `worker/engine/`) and `queue_client.py` (the producer half of the Redis protocol). Its `requirements.txt` is light: fastapi, uvicorn, redis, yt-dlp, pydantic — no ffmpeg, no faster-whisper, no cloud SDKs.
- Each service has its **own** `queue_client.py` and its **own** `logging_setup.py` (identical utility, duplicated on purpose) rather than importing a shared module. `queue_client.py` is genuinely two halves of one Redis protocol (`webapp/queue_client.py`: `create_job`/`get_job`/`subscribe`; `worker/queue_client.py`: `get_job`/`update_job`/`publish_event`/`dequeue`) — **if the Redis key/field naming changes in one copy, it must change in the other, there is no compiler to catch drift.** This is a known, accepted trade-off in exchange for true deployability independence.
- Each service has its own `.env`/`.env.example`, `.gitignore`, `.dockerignore`, and `Dockerfile`, and its Docker build `context` is its own folder (not the repo root) — so an image literally cannot `COPY` anything from outside its own service directory.
- `docker-compose.yml` at the repo root is the **only** place that references both services together (that's orchestration, not code sharing — normal and expected).
- The `redis` service uses the stock `redis:7-alpine` image; there's no code for it in this repo.

**Deployment target shifted from laptop to a cloud VM.** Still single-user in the sense that there's one operator and no accounts/auth, but the queue now genuinely supports multiple concurrent jobs processed by multiple worker replicas (`docker compose up -d --scale worker=N`) — this was an explicit ask, not speculative scaling. Job state lives in Redis (hash `youcreate:job:<id>`, list `youcreate:queue`, pub/sub channel `youcreate:job:<id>:events`), with a 24h TTL on job metadata (`JOB_TTL_SECONDS`) so Redis doesn't grow unbounded — final results themselves live on disk (`storage/outputs/`, shared between `webapp` and `worker` containers via a Docker named volume), not in Redis. `worker/worker.py`'s `main()` sweeps any leftover subdirectory of `WORK_DIR` on startup (defensive: a worker container restart normally wipes its own ephemeral disk anyway, but this guards against a persistent-volume misconfiguration).

**A Redis client gotcha worth remembering:** `redis-py`'s blocking commands (`BLPOP` in `worker/queue_client.py::dequeue`) need the connection's `socket_timeout` set comfortably higher than the blocking command's own timeout — otherwise the client raises `redis.exceptions.TimeoutError` on the socket before Redis gets to reply with `nil` at the end of the block. Both `queue_client.py` copies set `socket_timeout=30` on the shared `redis.Redis` client for this reason (dequeue's timeout is 5s; `webapp`'s SSE loop also polls `pubsub.get_message(timeout=5.0, ...)`, same class of risk).

**Corporate TLS-inspecting proxies break `docker build` (2026-09) — fixed, but worth understanding.** On the operator's dev machine (behind a corporate Zscaler proxy that re-signs all outbound HTTPS with its own CA), every `Dockerfile`'s `pip install` failed with `CERTIFICATE_VERIFY_FAILED`, and even after making the container trust Zscaler's CA (`update-ca-certificates`), `yt-dlp` calls from inside the container **still** failed the same way. Root cause layered in two parts, both now handled in `webapp/Dockerfile` and `worker/Dockerfile`:
1. `pip`/`requests` (and anything using the vendored `certifi` package) don't consult the OS trust store by default — they need `REQUESTS_CA_BUNDLE`/`SSL_CERT_FILE`/`PIP_CERT` explicitly pointed at `/etc/ssl/certs/ca-certificates.crt` (which itself needs the extra CA merged in via `update-ca-certificates` first).
2. `yt-dlp` goes a step further and reads `certifi.where()`'s bundle file **directly**, ignoring those env vars entirely — there's no `--trust-extra-ca` flag, only the insecure `--no-check-certificates`. The only fix is to `cat` the extra CA(s) onto the end of that installed `cacert.pem` file at build time, after `pip install` has put `certifi` in place.

Both Dockerfiles do this via an **optional, gitignored `certs/` folder** per service (`webapp/certs/`, `worker/certs/`, each with a `.gitkeep` so the folder always exists for `COPY` to succeed even when empty): if a machine needs to trust an extra CA to build, drop `.crt` file(s) there; on a machine with normal internet access (the actual deploy VM), the folder is empty and the extra steps are no-ops. **Do not bake a specific company's CA into the repo** — this is a per-machine, gitignored workaround, not a project dependency.

## Project goal

A machine for producing **Brazilian-market YouTube Shorts** out of videos that already went viral internationally: paste the link, pick the segment, and get back a vertical 1080x1920 MP4 (~60s) with PT-BR dubbing and burned-in Shorts-style captions, plus the `.srt`, no watermark. The source footage is reused (see "hybrid" in "Current state"), so the output is recognizably the same video, in Portuguese and in portrait. Single operator, no accounts/billing; the architecture is already split into independently deployable/scalable services (see "Current state") without over-building auth/multi-tenancy that hasn't been asked for (see "Future seams" below).

## Locked decisions (do not revisit)

| Decision | Value |
|---|---|
| Output | Vertical 1080x1920 MP4 (Shorts) with dubbing **and** burned-in captions, plus the `.srt` file |
| Input | YouTube URL only — no file upload anywhere (CLI/web service/frontend) |
| Clip length | Capped at **180s** (YouTube Shorts maximum); ~60s is the target, and crossing 60s warns about the music/Content ID rule |
| Source material | **Hybrid**: the original footage is reused, with new PT-BR narration over it. Not recreated from scratch — the short must look like the same video |
| Input languages | **EN and PT only** (`webapp/languages.py::SOURCE_ENABLED`). A product decision, not a technical limit — Whisper handles all 24 in `LANGUAGES` and the translator takes any of them to PT-BR; opening another one is adding its code to that set. EN = full flow; PT = cut + captions only |
| Output resolution | **No quality selector** — always automatic, derived from the reframe mode (2160p for `crop`, 1080p for `blur`). The `video_quality` API field survives as a manual override for CLI/debug, but nothing in the UI sets it |
| Vertical format | Per-video choice: `crop` (center crop, default) / `blur` (blurred background). **Every output is 1080x1920** — a third mode, `none` (keep 16:9), existed and was removed once the operator confirmed he'd never use it, since a 16:9 upload doesn't enter the Shorts feed. Face-tracking crop was deferred, not rejected; a draggable crop-position picker was proposed and dropped |
| Original audio | `DUB_KEEP_MUSIC=true` by default — original track ducked -18 dB under the dub (keeps music, also keeps the English voice faintly) |
| Scope | Single operator, no login/accounts; queue supports multiple concurrent jobs via worker replicas |
| Environment | Docker Compose on a cloud VM (`webapp` + `worker` + `redis`), or run each service locally without Docker |
| Language | Python (worker/engine and webapp) |
| Service boundary | `webapp` and `worker` share **zero source files** — communicate only via Redis (see "Current state") |
| Web framework | FastAPI (`webapp/main.py`) |
| Live progress | SSE (Server-Sent Events), backed by Redis pub/sub |
| Frontend | Plain HTML/JS (`webapp/web/index.html`), must be swappable for React without touching the backend |
| Transcription | faster-whisper (local, free) |
| Translation | Any LLM via OpenRouter (`OPENROUTER_MODEL`, only provider today); Gemini and Amazon Translate were both tried and removed — see "Current state"; provider must be pluggable |
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
- `worker/requirements.txt`: `redis`, `faster-whisper` (local transcription), `pyannote.audio` + `soundfile` (optional speaker diarization, see "Dubbing"), `boto3` (Polly TTS only — Amazon Translate was removed), `requests` (OpenRouter translation), `yt-dlp` (download), `pydantic`, `python-dotenv`.

System dependency (not pip, only needed inside `worker/`'s image/environment): **ffmpeg**.

Secrets via environment variables only, never hardcoded: AWS credentials for Polly (`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_DEFAULT_REGION`), `OPENROUTER_API_KEY` (translation, the only provider), and `HF_TOKEN` if diarization is enabled — all live only in `worker/.env` (the webapp never needs them). See "Pluggable providers" below for why the AWS credentials are read explicitly instead of via boto3's default chain.

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
    ├── requirements.txt      # heavy: faster-whisper, pyannote.audio, boto3, requests, yt-dlp
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
        │   ├── translate_openrouter.py  # any LLM via OpenRouter, model set by OPENROUTER_MODEL -- only provider today
        │   ├── translate_base.py
        │   ├── tts_polly.py  # Amazon Polly -- only TTS provider implemented
        │   └── tts_base.py   # abstract TTS interface
        └── steps/
            ├── download.py   # (1) yt-dlp -- YouTube-only, probe() + download_video() with optional range clip
            ├── audio.py      # (2) extract audio (ffmpeg)
            ├── transcribe.py # (3) faster-whisper (with timestamps)
            ├── translate.py  # (4) calls the active translation provider
            ├── subtitle.py   # (5) generate .srt/.vtt (the files the operator downloads)
            ├── captions.py   # (5b) .ass -- burned Shorts caption (few words, big)
            ├── dub.py        # (6) TTS + temporal fitting
            ├── reframe.py    # (6b) 9:16 ffmpeg filter (crop/blur/none) -- builds it, does not run it
            └── render.py     # (7) single ffmpeg pass: reframe + captions + dubbed audio
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
- `captions.build_ass(segments, out_path, opts) -> Path` — the burned Shorts caption, as `.ass` (the only format ffmpeg/libass accepts with font/outline/position control). Chunks each segment's PT-BR text into `opts["max_words"]` (default 3) pieces and splits the segment's time slot among them **weighted by character count**. Note why it cannot use Whisper word timestamps: Whisper times the **English** audio, but what gets displayed is the PT-BR translation — different words, different count, so those timings do not map. `WrapStyle: 0` is required, not cosmetic — long Portuguese chunks overflow the frame without it (found on the first real render). **`opts["play_res"]` must be the real output resolution**, also not cosmetic: libass scales PlayResX and PlayResY to the frame *independently*, so a mismatch both shrinks the text and visibly distorts its outline (stretched horizontally, squashed vertically) — observed on a real render, back when the `none` mode could emit 1920x1080. Today every output is 1080x1920, so `pipeline.py` always passes that; the parameter stays because the failure it prevents is silent and ugly. Font size, outline and margins are given for a 1920-tall frame and rescaled proportionally to `play_res`.
- `render.build_final(video, srt, dub_audio, out_path, opts)` takes **`dub_audio: Path | None`** — `None` means "no dub, keep the original audio", the same-language path. The ffmpeg input indices (`[0:v]`, `[1:a]`, the soft-subtitle stream) are computed rather than hardcoded, because dropping the dub input shifts everything after it.
- `reframe.build_filter(mode, in_label, out_label) -> list[str]` — returns filter_complex fragments and **does not run ffmpeg**, so `render` can do reframe + captions in a single encode instead of two (no generational quality loss, half the CPU). `reframe.recommended_max_height(mode)` is what backs the "Automático" quality option.
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

Provider selection happens via `config.TRANSLATE_PROVIDER` / `config.TTS_PROVIDER` through a simple factory (in `engine/steps/translate.py` and `engine/steps/dub.py`, respectively). `config.TRANSLATE_PROVIDER` only supports `"openrouter"` today (any LLM behind OpenRouter's OpenAI-compatible API, model chosen via `OPENROUTER_MODEL`) — both Gemini (`google-genai`) and Amazon Translate (`translate_aws.py`) were implemented and then removed (see "Current state"). `config.TTS_PROVIDER` only supports `"polly"` (`tts_polly.PollyTTS`) — Google Cloud TTS was implemented and then deliberately removed too (see "Current state"). Polly returns headerless PCM from the AWS API, wrapped into a proper `.wav` via the stdlib `wave` module (PCM output only supports 8000/16000 Hz sample rates on Polly, unlike its mp3/ogg formats).

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
- **subtitles**, burned in or attached as a soft track — expose as `BURN_SUBS` (default: burn, to guarantee display in the YouTube/Shorts feed). When an `ass_path` is given (the normal Shorts path) it wins over the `.srt`: the `ass=` filter is what provides font/outline/position control. The `.srt` is still generated and still downloadable — it just isn't what goes on the image.
- **9:16 reframing** via `opts["reframe_mode"]`, chained ahead of the caption filter so the ASS `PlayResX/Y` (1080x1920) matches the real output resolution. Reframing is always on, so the video is always re-encoded — there is no `-c:v copy` path any more.

**Source resolution matters more than it looks, and only in `crop` mode.** Center-cropping 9:16 out of a 1920x1080 source leaves 608x1080, which then *upscales* to 1080x1920 — visibly soft. A 3840x2160 source leaves 1215x2160, which *downscales* — sharp. So the worker asks yt-dlp for 2160p in `crop` mode and only 1080p in `blur` mode (where the whole frame fits the width and 4K would be wasted bandwidth). This is decided by `reframe.recommended_max_height(mode)` in `worker.py`, **not** by the operator: the quality selector was removed from Tela 2 entirely, since the right answer is always "whatever makes this reframe mode sharp".

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
        url_clip_duration: float | None = None,
        source_lang: str | None = None,
        target_lang: str | None = None,
        max_height: int | None = None,
        reframe_mode: str | None = None,
        should_cancel: Callable[[], bool] | None = None) -> PipelineResult:
    # order: [download if source_url] → audio → transcribe → translate → subtitle → dub → render(final)
    # when source_lang == target_lang: translate and dub are skipped (see
    # _same_language); render still runs, keeping the original audio
    # emit on_progress at each step with increasing pct and a clear PT-BR message
```

`video_path` and `source_url` are mutually exclusive-ish (`source_url` wins by populating `video_path` internally after download) but at least one is required. In practice every current caller (`worker.py`, `cli.py`) passes only `source_url` — `video_path` is kept as an engine-level affordance (e.g. ad hoc scripts/tests against a local sample file).

The orchestrator knows nothing about HTTP, Redis, or jobs — only the callback.

`run` wraps the whole pipeline in a try/except and, on any failure, raises `PipelineError(message, partial_result)` — `partial_result` is the `PipelineResult` built so far (e.g. `srt_path` already set even if dubbing later fails). Callers (`cli.py`, `worker.py`) catch `PipelineError` specifically to recover whatever artifacts were already produced instead of losing them when a downstream step fails.

## Web service (`webapp/`)

Endpoints (`main.py`):
- `GET /api/probe?url=`: reads `{ duration, title }` for a YouTube URL via `youtube_probe.probe` (standalone, not a call into `worker/engine/`), without downloading it — used by the frontend to size the trim bar before submitting.
- `POST /api/jobs` (JSON body, `JobCreateRequest`: `{url, start?, clip_duration?, source_lang, target_lang, include_subtitles, video_title?, video_duration?, video_quality?, reframe_mode?}`): calls `queue_client.create_job` — writes the job hash to Redis and pushes its id onto `youcreate:queue`. Returns immediately with `{ "id": <job_id> }`; the download and processing happen entirely inside whichever `worker` picks it up. `video_title`/`video_duration` are supplied by the frontend from its earlier `GET /api/probe` call purely so the processing/result screens (Telas 3/4) can render immediately from `GET /api/jobs/{id}` without re-probing.
- `GET /api/jobs/{id}` (`JobStatus`): reads current state from the Redis hash via `queue_client.get_job` — `status` (`queued|running|done|error|cancelled`), `pct`, `step`, `message`, `error_code`, `created_at`, `source_url`, `source_lang`, `target_lang`, `video_title`, `video_duration`, `include_subtitles`, `reframe_mode`, `clip_start`/`clip_duration`, plus `video_name`/`srt_name` (bare filenames) and `video_url`/`srt_url`/`vtt_url` (signed, time-limited download links — see below). This single endpoint is what lets the frontend fully reconstruct any of Telas 3/4/5 from a cold page load at `/v/{id}` (refresh, or a link opened later), since every field the UI needs is in this one response.
- `GET /api/jobs/{id}/events`: **SSE** stream. Subscribes to the job's Redis pub/sub channel (`queue_client.subscribe`), checks current status first (handles the job having already finished before the subscribe call), then loops on `pubsub.get_message(timeout=5.0, ...)`, re-checking status on every timeout as a safety net against a missed final message. Emits `data: {step,pct,message}\n\n` per event, plus a final `data: {done:true,status}\n\n` (`status` includes `cancelled`).
- `POST /api/jobs/{id}/cancel`: sets `cancel_requested=1` on the job's Redis hash (`queue_client.request_cancel`). The worker only checks this **between** pipeline steps (`PipelineCancelled` in `engine/pipeline.py`), never mid-subprocess — a deliberately simple cancellation model, not a hard kill.
- `GET /api/download/{filename}?exp=&sig=`: serves files from `OUTPUTS_DIR` (the shared Docker volume), but only with a valid HMAC-SHA256 signature and an unexpired `exp` timestamp (`DOWNLOAD_URL_TTL_SECONDS`, 1h) — see "Signed download links" below. Bare `/api/download/{filename}` with no query params is rejected (422).
- `GET /`, `GET /configurar`, `GET /v/{job_id}`: all three serve the same `web/index.html` — routing between Telas 1–5 is entirely client-side (`history.pushState`/`popstate`); serving the same file on all three routes means a hard refresh on any of them (including a `/v/{id}` link shared or reopened later) doesn't 404.

**The Tela 5 "tentar de novo" must resend the clip range.** `retryJob` rebuilds a `POST /api/jobs` body from `GET /api/jobs/{id}`, and it originally omitted the trim — which was survivable while trimming was uncapped, but under the Shorts cap it would silently reprocess the *whole* source video (the 180s validator only fires when `clip_duration` is present, and a retry sent none). `JobStatus` therefore exposes `clip_start`/`clip_duration`, and the retry carries them plus `reframe_mode`. Any new field that changes what gets produced has to be added to `retryJob` too — there is no mechanism keeping them in sync.

**Signed download links.** Since there are no accounts/sessions, `webapp/main.py` signs `{filename}:{exp}` with HMAC-SHA256 using `DOWNLOAD_SIGN_SECRET` (from `.env`, or a random one generated per-process if unset — acceptable because a restart only invalidates old links, and there's nothing more sensitive being protected than "don't let a stale/guessed filename download indefinitely"). `JobStatus.video_url`/`srt_url`/`vtt_url` already carry the `?exp=&sig=` query string — the frontend never constructs a download URL itself, only uses what `GET /api/jobs/{id}` returns.

**Error classification.** `worker/worker.py::_classify_error` does best-effort string matching on the exception message (mostly yt-dlp's own text) to produce a short `error_code` (`video_privado`, `video_indisponivel`, `restrito_regiao`, `sem_audio`, or the fallback `falha_interna`; `video_longo` exists in the frontend's error-content map but is never emitted by the backend — there's no video-length cap in the engine, see "Locked decisions"). This is inherently a moving target — yt-dlp doesn't expose a structured error type — and is expected to be extended as new real failure messages show up in `storage/youcreate-worker.log`. The Tela 5 error screen (`webapp/web/index.html`) is a **generic, code-driven component**: unknown/unmapped codes render the `falha_interna` copy, so adding a backend classification case is optional, not required, for an error to display reasonably.

`queue_client.py` (producer half — see "Current state" for why this isn't imported from `worker/`):
```python
@dataclass
class JobRecord:
    id: str; source_url: str
    created_at: float = 0.0
    url_clip_start: float = 0.0; url_clip_duration: float | None = None
    source_lang: str = ""; target_lang: str = ""
    include_subtitles: bool = True
    video_title: str = ""; video_duration: float = 0.0
    video_quality: str = ""  # "" = automatico (derivado do reframe_mode)
    reframe_mode: str = ""   # "" = padrao do worker; ou crop|blur|none
    status: str = "queued"   # queued|running|done|error|cancelled
    pct: int = 0; step: str = ""; message: str = ""
    error_code: str = ""; cancel_requested: bool = False
    result_video: str = ""; result_srt: str = ""; result_vtt: str = ""

def create_job(source_url, url_clip_start=0.0, url_clip_duration=None, source_lang="", target_lang="", include_subtitles=True, video_title="", video_duration=0.0) -> str  # job id
def get_job(job_id) -> JobRecord | None
def request_cancel(job_id) -> bool
def subscribe(job_id) -> redis.client.PubSub
```

## Worker service (`worker/`)

`worker.py`'s `main()` loop: `queue_client.dequeue(timeout=5)` (BLPOP — atomic across replicas, each job consumed by exactly one worker) → `_process(job_id)`, which reads the job from Redis, calls `pipeline.run(..., should_cancel=lambda: queue_client.is_cancelled(job_id))` with an `on_progress` that does `queue_client.update_job(...)` + `queue_client.publish_event(...)`, and in a `finally` block `shutil.rmtree`s the job's `work_dir` regardless of success/failure — the downloaded video and every intermediate artifact are temporary; only `OUTPUTS_DIR` (shared volume) needs to survive. `PipelineCancelled` is caught separately from `PipelineError` and sets `status="cancelled"` (distinct from `"error"` — the frontend's Tela 5 shows different copy for each). Any other `PipelineError`/`Exception` is run through `_classify_error(message)` (see "Web service" above) to set `error_code` before publishing the failure.

`queue_client.py` (consumer half):
```python
def get_job(job_id) -> JobRecord | None
def is_cancelled(job_id) -> bool   # reads cancel_requested fresh from Redis, no caching
def update_job(job_id, **fields) -> None
def publish_event(job_id, event: dict) -> None
def dequeue(timeout=5) -> str | None   # BLPOP
```

Run multiple replicas for concurrent processing: `docker compose up -d --scale worker=N`.

## Frontend (`webapp/web/index.html`)

Single-page dark-theme SPA, five client-side-routed screens sharing one `:root` token system (see the file's `<style>` block for exact colors/spacing) and one `<script>`, no framework — must stay trivial to replace with React without touching the backend:

1. **Tela 1** (`/`, `#screen-home`) — paste a YouTube URL, "Processar" calls `GET /api/probe`.
2. **Tela 2** (`/configurar`, `#screen-configure`) — source/target language selects (native `<select>`, populated from `GET /api/languages`; only `pt` is `enabled_as_target`, others show "(em breve)"). **Choosing the same language on both sides is allowed and meaningful** — `#lang-note` then explains that there will be no translation or dubbing, only the vertical cut and captions, and the time estimate drops accordingly, subtitle on/off, **the vertical-format selector** (`name="reframe"`: corte central / fundo desfocado), the trim bar (see below), "Gerar vídeo" → `POST /api/jobs` → navigates to `/v/{id}`.
3. **Tela 3** (`/v/{id}` while queued/running, `#screen-processing`) — giant percentage + current-stage label driven directly by the `pct`/`step` the backend already emits (no client-side interpolation — a deliberate simplicity choice: the engine's own step weights, e.g. `transcribe→20`, `dub→75`, already read fine as "progress"), a video info card, and a bottom band with "pode fechar a aba" copy, a rough ETA, and an inline (non-`window.confirm`) Cancelar → `POST /api/jobs/{id}/cancel`.
4. **Tela 4** (`/v/{id}` when `status=done`, `#screen-result`) — native `<video controls>` with a `<track kind="subtitles">` pointed at the signed `vtt_url` (only when `include_subtitles`), download cards for the MP4 (always) and SRT (hidden entirely, MP4 card goes full-width, when subtitles were off), file sizes fetched via a `HEAD` request against the signed URL, and "Traduzir para outro idioma" (re-runs Tela 2's probe flow against the same `source_url`).
5. **Tela 5** (`/v/{id}` when `status=error|cancelled`, or the job id isn't found, `#screen-error`) — generic code→content component (`ERROR_CONTENT` map in the script), `role="alert"`, focuses the title on entry; never shows a raw stack trace, only the short `error_code`-driven copy plus (for `falha_interna`) the first 8 chars of the job id, for support purposes.

Loading `/v/{id}` directly (fresh tab, refresh, a link opened later) fully reconstructs whichever of Telas 3/4/5 applies from a single `GET /api/jobs/{id}` call — there is no reliance on in-memory state surviving a reload, since the backend now returns everything the UI needs (title, duration, languages, subtitle choice, error code, signed URLs).

**File cleanup stays discard-on-pagehide, not a fixed TTL** (explicit operator decision): `navigator.sendBeacon('/api/jobs/{id}/discard')` fires on `pagehide` for whichever job is currently "active" on Tela 4 (or Tela 5, if a partial `.srt` survived a failed dub) — this is why Tela 4 has no "apagamos em 24h" messaging; the file lives until the visitor actually navigates away.

**Trim bar — optional, capped at the Shorts limit.** After "Processar" resolves via `GET /api/probe`, the trim bar (Tela 2) always appears (any duration) with the full range pre-selected — leaving it untouched means the whole video is processed. It's a two-handle range (`#trim-window` with `.trim-grip.left`/`.trim-grip.right`), each independently draggable to narrow the clip, plus a body-drag to move the whole window; `setClipRange(start, end)` enforces `MIN_CLIP_SECONDS = 1` **and `MAX_CLIP_SECONDS = 180`** (clamping by pulling the *right* edge in, so dragging the left handle never feels stuck), recomputes `needsTrim`, refreshes the time estimate, and toggles `#trim-warning` once the range passes `MUSIC_SAFE_SECONDS = 60` — the music/Content ID rule from "Current state". The warning informs, it does not block: the operator asked to keep control of the duration. On submit, `start`/`clip_duration` are included in the `POST /api/jobs` JSON body only when `needsTrim` is true. There's no local video preview (the source is a remote URL, not a local file) — just the track + time labels.

## Configuration (`.env` per service)

`webapp/.env` (see `webapp/.env.example`): `REDIS_URL`, `STORAGE_DIR`, `OUTPUTS_DIR`. Nothing else — the web service has no secrets.

`worker/.env` (see `worker/.env.example`): `REDIS_URL`, storage paths (`STORAGE_DIR`/`OUTPUTS_DIR`/`WORK_DIR`), AWS credentials (Polly only — Amazon Translate was removed), `OPENROUTER_API_KEY`/`OPENROUTER_MODEL` (translation, the only provider), `SOURCE_LANG`/`TARGET_LANG`, `WHISPER_MODEL`/`WHISPER_DEVICE`/`WHISPER_COMPUTE_TYPE`, `TRANSLATE_PROVIDER`/`TRANSLATE_STYLE`, `TTS_PROVIDER`/`DUB_VOICE`/`POLLY_ENGINE`/`DUB_MAX_SPEEDUP`/`DUB_KEEP_MUSIC`, `HF_TOKEN`/`DUB_ENABLE_DIARIZATION` (optional speaker diarization), `BURN_SUBS`, and the Shorts block: `SHORT_MAX_SECONDS` (180), `REFRAME_MODE` (fallback when a job doesn't specify one), `CAPTION_MAX_WORDS`/`CAPTION_UPPERCASE`/`CAPTION_FONT`/`CAPTION_FONT_SIZE`/`CAPTION_MARGIN_V`. Note `CAPTION_FONT` can only name a font actually installed in the worker image — today that's DejaVu Sans alone; a punchier Shorts face (Montserrat ExtraBold and the like) means adding it to `worker/Dockerfile` too.

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
- Full setup/config walkthrough (obtaining AWS/Polly credentials, `OPENROUTER_API_KEY`): see `README.md`
