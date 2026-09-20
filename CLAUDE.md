# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current state

**Product pivot (2026-09): from "localize a video" to "a YouTube Shorts machine."** The original goal was to take any YouTube video and hand back a PT-BR localized copy of it. The operator redefined the product: it now exists to take videos that **already succeeded internationally** and reissue them as **Brazilian-market Shorts** — vertical, ~60s, dubbed and captioned. Everything downstream of that decision:

- **The "hybrid" approach was chosen deliberately, over recreating from scratch.** Three options were weighed: reuse the original clip as-is, rebuild a new video from scratch using the original only as a script/format reference, or a hybrid (original footage + new PT-BR narration + new edit). Cost was **not** the deciding factor — hybrid and from-scratch-with-stock both land at ~US$0.03/short (Whisper is local, OpenRouter is sub-cent, Polly neural on ~1300 chars is ~US$0.02); only AI *video* generation was disqualifying (~US$15–50/short, i.e. US$450–1500/month at one short/day) and is not built. The operator chose hybrid explicitly **because the short should look like the same video** — "não quero criar um video diferente". The Content ID / reused-content exposure that comes with that is a known, accepted operator decision; do not re-litigate it.
- **The trim cap is now REAL, revoking a previously locked decision.** "Optional and uncapped" is dead. A YouTube Short is vertical/square and **at most 180s** (raised from 60s in October 2024). `webapp/schemas.py::SHORT_MAX_SECONDS` rejects longer clips (422) and the frontend's trim bar clamps to it.
- **The clip is a FIXED 60-second window; only its position is chosen.** This replaced a resizable two-handle range after the operator tried it in practice — "vamos manter a barra fixa em 60s, isso evita que eu tenha que ficar tentando ajustar". `CLIP_SECONDS = 60` in `webapp/web/index.html`; the window is dragged along the track (or the track is clicked to centre it), and `setClipStart(start)` replaced `setClipRange(start, end)`. Two consequences worth knowing: the grips and the `#trim-warning` element are **gone**, because a second hard YouTube rule can no longer be crossed — a Short **longer than 60s** using non-royalty-free music gets a Content ID claim and is **blocked**, which mattered here because `DUB_KEEP_MUSIC` is on by default (the operator wants the original soundtrack — "as musicas sao interessantes e nao quero ter que remover"). Pinning the window at 60s removes that risk by construction. 30–60s is also the best-performing Shorts length. **The backend still accepts up to 180s** (`schemas.py::SHORT_MAX_SECONDS`, the real YouTube maximum) — the 60 is this workflow's rule, not the API's.
- **Ducking was tried and REPLACED by real source separation (2026-09).** The first implementation of `DUB_KEEP_MUSIC=true` ducked the **entire** original track by -18 dB — music *and* the original English voice — under the PT-BR dub, classic voice-over style. It shipped deliberately as the cheap first try ("comece com ducking para testar"), and the operator rejected it after hearing a real English job: "o ducking ficou muito ruim". It is now **gone** — `render.build_final` has no `keep_music` option and will never mix the raw original track. In its place, `engine/steps/separate.py` runs **Demucs** (`htdemucs`, `--two-stems=vocals`) and mixes only the `no_vocals` bed under the dub. Measured in this project, not guessed: **~45s of CPU per minute of audio** at `--jobs 4` on 12 cores — and more jobs is *worse* (`--jobs 12` took 114s for the same 60s clip, because each process spawns its own torch threads and they fight). Separation quality was verified objectively by transcribing each stem: Whisper found 14 segments of lyrics in `vocals.wav` and **zero speech** in `no_vocals.wav`. The ~80MB model downloads to `/root/.cache/huggingface`, which is **already the persistent `whisper-cache` volume**, so it is fetched once ever and needs no Dockerfile change. **If separation fails, the fallback is dub-only (no music) — never a silent return to ducking**, which would reintroduce exactly the audio the operator rejected. `DUB_MUSIC_DB` (default -6) is a by-ear parameter: the bed no longer has to bury a competing voice, so it sits far higher than the old -18 dB.
- **Source already in the target language is a first-class path (2026-09).** Picking a video that is already in Portuguese used to be impossible in the UI and broken underneath it. Three separate defects, all fixed: (a) `target_lang` was collected by the frontend, stored in Redis and returned by `GET /api/jobs/{id}` but **never reached the engine** — `worker.py` didn't pass it and `pipeline.run` had no such parameter, so the "Traduzir para" selector was decorative; (b) the frontend's `refreshLangSelects()` deliberately disabled the source language in the target select ("traduzir de um idioma para ele mesmo nao e um caso valido"), which forced the operator to mislabel a Portuguese video as English and hand Whisper the wrong language; (c) had it gotten through, `translate_openrouter.py`'s anti-echo guard (`_ECHO_THRESHOLD = 0.5`) would have read a *correct* PT→PT translation — which legitimately returns the original text — as the model echoing its input, burning `_MAX_ECHO_ATTEMPTS = 3` calls before failing with a confusing error. Now `pipeline._same_language(source, target)` (base code comparison, so `pt-BR` == `pt`; empty source means Whisper autodetect and therefore returns False, the safe direction) short-circuits: translation is skipped (`seg.translation = seg.text`), **dubbing and diarization are skipped entirely**, the original audio is kept, and the job still produces the reframed + captioned vertical MP4. It costs nothing beyond local Whisper and ffmpeg — no OpenRouter call, no Polly call.
- **Discovery and publishing are deliberately NOT built.** The operator pastes the link by hand (no YouTube Data API trending/ranking subsystem), and downloads the MP4 to upload manually. Direct YouTube publishing is a wanted *later* step ("depois podemos integrar"), so keep `webapp/main.py` seamed for it — it would be the project's first OAuth/user-credential integration.

All 5 original build phases (see "Build phases" below) are implemented and validated end-to-end with real dubbing (Amazon Polly), including the video-trim feature (§ Frontend) and friendly error handling / logging (§ Quality bar). The original architecture spec (`docs/youclone-arquitetura.md`, in Portuguese) is kept as historical reference; this file is the up-to-date source of truth. See `docs/ARQUITETURA.md` (design/why) and `docs/FLUXO.md` (request trace/how) for the current, detailed documentation. Google Cloud TTS was implemented earlier and then **removed** by explicit operator decision (2026-09) in favor of Polly alone — do not re-add it unless asked.

**Dubbing moved from Amazon Polly to OpenRouter TTS (2026-09), and AWS left the project entirely.** Polly was the only TTS provider for most of the project's life. It was replaced after the operator listened to a real EN→PT short and called the voice robotic. The diagnosis was three compounding causes, all measured rather than guessed: (a) `DUB_VOICE=Thiago` is capped at Polly's `neural` engine — in pt-BR only **Camila** reaches `generative`, per `describe_voices` on the account; (b) the provider asked Polly for `pcm`, which Polly only serves at 8/16 kHz, so every dub was **16 kHz** (mp3/ogg would have given 24 kHz); (c) time-fitting compressed finished audio with `atempo` up to 1.3x, and since PT-BR runs 15–30% longer than EN, nearly every line hit that cap — one measured line needed 1.33x. All three were fixed on Polly first (auto best-engine per voice, 24 kHz, resynthesis instead of `atempo`), and those fixes are what the OpenRouter provider inherited.

The operator then compared voices by ear across the OpenRouter catalogue and kept 10, spread across Gemini, Grok and Kokoro. `Algieba` (Gemini) was the first pick on sound alone, but **the default is `pm_alex` (`hexgrad/kokoro-82m`)** because Kokoro is the only model measured to honour `speed`, and that decides whether the dub lands on time: same clip, max drift from the picture was **0.31s with Kokoro against 1.50s (the cap) with Gemini**. The Kokoro voices are ordered first in `DUB_VOICE_POOL` so multi-speaker jobs get the well-fitted ones before falling back to Gemini/Grok. Polly, `boto3` and every `AWS_*` variable were deleted — the project now has **one** cloud credential, `OPENROUTER_API_KEY`, covering translation and dubbing. Two things were lost in the trade and are worth knowing: Gemini **ignores the `speed` parameter**, so the resynthesis fix does not apply to the default voice (it falls back to `atempo`), and Gemini's voices speak slower than Polly's, so lines overrun their slots more often.

**Voice cloning was investigated and is NOT available here (2026-09).** The obvious follow-up — reuse the original speaker's own voice in PT-BR — looked viable because Voxtral Mini TTS does zero-shot cross-lingual cloning from ~3s of reference, at US$16/M characters (the same price as Polly neural, ~US$0.02/short), and `separate.py` already produces a clean `vocals.wav` that would be the ideal reference. It is blocked at the hosting layer: all three OpenRouter endpoints for Voxtral report **`supports_voice_cloning: false`**, and no model in the catalogue advertises it, even though `/audio/speech` accepts an `input_references` field. Going ahead would mean Mistral's API directly, self-hosting the open weights, or ElevenLabs. **Before building it, note the rights question is a different one from the Content ID risk already accepted:** cloning an identifiable person's voice engages personality rights (in Brazil, CC arts. 11–21 and CF art. 5º, X), not copyright, and YouTube has a separate complaint process for synthetic media simulating a real individual. That is an operator decision that has not been made.

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
| Original audio | `DUB_KEEP_MUSIC` gates **source separation**, never ducking. Code default is **`false`** (`engine/config.py`); `worker/.env.example` ships `true`, and the operator's live `worker/.env` currently has `false`. When on, Demucs strips the original voice and only the `no_vocals` bed is mixed under the dub at `DUB_MUSIC_DB` (-6). The raw original track is never mixed in |
| Speaker count | Asked on Tela 2 (`speaker_count`: 0 = automatic, 1–6). **1 skips diarization entirely** — it is the slowest step after source separation and changes nothing when there is a single voice; N>1 is passed to pyannote as `num_speakers`, removing the part it most often gets wrong |
| Scope | Single operator, no login/accounts; queue supports multiple concurrent jobs via worker replicas |
| Environment | Docker Compose on a cloud VM (`webapp` + `worker` + `redis`), or run each service locally without Docker |
| Language | Python (worker/engine and webapp) |
| Service boundary | `webapp` and `worker` share **zero source files** — communicate only via Redis (see "Current state") |
| Web framework | FastAPI (`webapp/main.py`) |
| Live progress | SSE (Server-Sent Events), backed by Redis pub/sub |
| Frontend | Plain HTML/JS (`webapp/web/index.html`), must be swappable for React without touching the backend |
| Transcription | faster-whisper (local, free) |
| Translation | Any LLM via OpenRouter (`OPENROUTER_MODEL`, only provider today); Gemini and Amazon Translate were both tried and removed — see "Current state"; provider must be pluggable |
| TTS (dubbing) | **OpenRouter `/audio/speech`** (`tts_openrouter.py`), default `hexgrad/kokoro-82m` / `pm_alex`. Amazon Polly *and* Google Cloud TTS were both implemented and removed — see "Current state". Same `OPENROUTER_API_KEY` as translation; no AWS account in the project any more |
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
- `worker/requirements.txt`: `redis`, `faster-whisper` (local transcription), `pyannote.audio` + `soundfile` (optional speaker diarization, see "Dubbing"), `demucs` (source separation, see "Dubbing"), `requests` (OpenRouter — both translation *and* TTS), `yt-dlp` (download), `pydantic`, `python-dotenv`. **No `boto3`** — it left with Polly.

System dependency (not pip, only needed inside `worker/`'s image/environment): **ffmpeg**.

Secrets via environment variables only, never hardcoded: `OPENROUTER_API_KEY` (translation **and** dubbing) and `HF_TOKEN` if diarization is enabled — both live only in `worker/.env` (the webapp never needs them). **There are no AWS credentials in this project any more.** The explicit-credentials rule that governed them is kept below anyway, because it applies to whatever cloud integration comes next.

## Directory structure

```
docker-compose.yml          # the ONLY file that references both services together
.github/workflows/deploy.yml # push to main -> SSH deploy to the EC2 box (see "Deployment")
deploy/setup-ec2.sh         # one-shot bootstrap of a fresh Ubuntu EC2 (docker + git)
docs/                       # ARQUITETURA.md (design/why), FLUXO.md (request trace/how)
webapp/                      # SERVICE 1: the site -- fully self-contained
│   ├── Dockerfile           # build context = this folder
│   ├── requirements.txt     # light: fastapi, redis, yt-dlp, pydantic
│   ├── .env / .env.example  # REDIS_URL, STORAGE_DIR, OUTPUTS_DIR only
│   ├── main.py              # FastAPI app: /, /api/probe, /api/jobs, /api/jobs/{id}, /api/jobs/{id}/events, /api/download/{filename}
│   ├── schemas.py           # request/response Pydantic models
│   ├── queue_client.py      # Redis protocol, PRODUCER half (create_job/get_job/subscribe)
│   ├── youtube_probe.py     # standalone yt-dlp metadata lookup (NOT a call into worker/engine/)
│   ├── languages.py         # LANGUAGES + SOURCE_ENABLED/TARGET_ENABLED, served by GET /api/languages
│   ├── logging_setup.py     # own copy of the logging utility
│   ├── cookies.txt          # YouTube session cookies -- COMMITTED, see "YouTube access"
│   └── web/
│       ├── index.html       # the whole SPA (5 client-routed screens, one <style>, one <script>)
│       └── assets/          # self-hosted Inter woff2 subsets + favicon.svg (no CDN font)
└── worker/                    # SERVICE 2: the motor -- fully self-contained
    ├── Dockerfile            # build context = this folder; installs system ffmpeg
    ├── requirements.txt      # heavy: faster-whisper, pyannote.audio, boto3, requests, yt-dlp
    ├── .env / .env.example   # everything engine-related + REDIS_URL + storage paths
    ├── worker.py             # queue consumer: BLPOP -> pipeline.run -> publish progress -> cleanup
    ├── cli.py                # run the engine standalone from the terminal, no queue
    ├── queue_client.py       # Redis protocol, CONSUMER half (get_job/update_job/publish_event/dequeue)
    ├── logging_setup.py      # own copy of the logging utility
    ├── cookies.txt           # YouTube session cookies -- COMMITTED, see "YouTube access"
    └── engine/               # THE MOTOR -- pure Python, no web, no Redis
        ├── config.py         # env-based config (loads .env via python-dotenv)
        ├── models.py         # dataclasses (Segment, PipelineResult)
        ├── pipeline.py       # orchestrates steps + emits progress; PipelineError
        ├── ffmpeg_utils.py   # shared run_ffmpeg/probe_duration/is_ffmpeg_available
        ├── providers/        # pluggable implementations
        │   ├── translate_openrouter.py  # any LLM via OpenRouter, model set by OPENROUTER_MODEL -- only provider today
        │   ├── translate_base.py
        │   ├── tts_openrouter.py # OpenRouter /audio/speech -- only TTS provider
        │   └── tts_base.py   # abstract TTS interface (synthesize + supports_speed)
        └── steps/
            ├── download.py   # (1) yt-dlp -- YouTube-only, probe() + download_video()/download_audio() with optional range clip
            ├── audio.py      # (2) extract audio (ffmpeg)
            ├── transcribe.py # (3) faster-whisper (with timestamps)
            ├── translate.py  # (4) calls the active translation provider
            ├── subtitle.py   # (5) generate .srt/.vtt (the files the operator downloads)
            ├── captions.py   # (5b) .ass -- burned Shorts caption (few words, big)
            ├── separate.py   # (5c) demucs: strips the original voice, keeps the music bed
            ├── diarize.py    # (5d) optional pyannote: who speaks when -> one Polly voice per speaker
            ├── dub.py        # (6) TTS + temporal fitting
            ├── reframe.py    # (6b) 9:16 ffmpeg filter (crop/blur) -- builds it, does not run it
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
- `download.download_video(url: str, out_dir: Path, start: float = 0.0, duration: float | None = None, max_height: int | None = None) -> Path` — downloads via yt-dlp; when `duration` is given, uses `download_ranges` to fetch only that segment.
- `download.download_audio(...)` — same options, `bestaudio/best`. It exists because **only `render` needs the video**: transcription, translation and dubbing need audio alone, so `pipeline.run` fetches audio and video on two threads (`ThreadPoolExecutor(max_workers=2)`, both I/O-bound) and the combined wait is the slower of the two instead of their sum.
- Both `probe` and the two downloaders merge `_cookie_opts()` + `_bot_check_opts()` — see "YouTube access" below.
- `audio.extract_audio(video_path: Path, work_dir: Path) -> Path` — `.wav` 16kHz mono via ffmpeg.
- `transcribe.transcribe(audio_path: Path, language: str | None = None) -> list[Segment]` — faster-whisper, `vad_filter=True`. The language comes **per job** (Tela 2 → Redis → `pipeline.run(source_lang=...)`); `None` means Whisper autodetects. There is no `config.SOURCE_LANG` — it was removed when source language became a per-job choice.
- `translate.translate_segments(segments: list[Segment]) -> list[Segment]` — delegates to the active provider; translates in **chunks of `OPENROUTER_BATCH_SIZE` lines (default 250)**, numbered, response as an indexed JSON object to preserve order. Fills `seg.translation`. It was a single call over the whole video until a ~1400-line job made that call routinely time out — smaller batches answer faster and more reliably, at the cost of more HTTP round-trips and the prompt instructions being re-sent per batch. Segment durations are passed alongside the text (`_word_budget`) so the model is told roughly how many words each line has room for.
- `subtitle.build_srt(segments, out_path, translated=True) -> Path` — standard `.srt` (index, `HH:MM:SS,mmm --> ...`, text, blank line).
- `captions.build_ass(segments, out_path, opts) -> Path` — the burned Shorts caption, as `.ass` (the only format ffmpeg/libass accepts with font/outline/position control). Chunks each segment's PT-BR text into `opts["max_words"]` (default 3) pieces and splits the segment's time slot among them **weighted by character count**. Note why it cannot use Whisper word timestamps: Whisper times the **English** audio, but what gets displayed is the PT-BR translation — different words, different count, so those timings do not map. `WrapStyle: 0` is required, not cosmetic — long Portuguese chunks overflow the frame without it (found on the first real render). **`opts["play_res"]` must be the real output resolution**, also not cosmetic: libass scales PlayResX and PlayResY to the frame *independently*, so a mismatch both shrinks the text and visibly distorts its outline (stretched horizontally, squashed vertically) — observed on a real render, back when the `none` mode could emit 1920x1080. Today every output is 1080x1920, so `pipeline.py` always passes that; the parameter stays because the failure it prevents is silent and ugly. Font size, outline and margins are given for a 1920-tall frame and rescaled proportionally to `play_res`.
- `render.build_final(video, srt, dub_audio, out_path, opts)` takes **`dub_audio: Path | None`** — `None` means "no dub, keep the original audio", the same-language path. The ffmpeg input indices (`[0:v]`, `[1:a]`, the soft-subtitle stream) are computed rather than hardcoded, because dropping the dub input shifts everything after it.
- `separate.extract_music(audio_path, work_dir) -> Path | None` — the `no_vocals` bed, or `None` when separation fails. `None` is a deliberate degradation (short without background music), not an error to propagate: the alternative fallback, reusing the full original track, is the very thing that was rejected.
- `reframe.build_filter(mode, in_label, out_label) -> list[str]` — returns filter_complex fragments and **does not run ffmpeg**, so `render` can do reframe + captions in a single encode instead of two (no generational quality loss, half the CPU). `reframe.recommended_max_height(mode)` is what backs the "Automático" quality option.
- `dub.synthesize_dub(segments, work_dir) -> Path` — see "Dubbing" below.
- `render.build_final(video, srt, dub_audio, out_path, opts) -> Path` — see "Final render" below.

### Pluggable providers (`providers/`)

```python
# translate_base.py
class Translator(Protocol):
    def translate_batch(self, texts: list[str],
                        durations: list[float] | None = None) -> list[str]: ...

# tts_base.py
class TTS(Protocol):
    def synthesize(self, text: str, voice: str, model: str | None = None,
                   speed: float | None = None) -> bytes: ...   # .wav bytes, 24kHz mono
    def supports_speed(self, model: str | None = None) -> bool: ...
```

Provider selection happens via `config.TRANSLATE_PROVIDER` / `config.TTS_PROVIDER` through a simple factory (in `engine/steps/translate.py` and `engine/steps/dub.py`, respectively). `config.TRANSLATE_PROVIDER` only supports `"openrouter"` today (any LLM behind OpenRouter's OpenAI-compatible API, model chosen via `OPENROUTER_MODEL`) — both Gemini (`google-genai`) and Amazon Translate (`translate_aws.py`) were implemented and then removed (see "Current state"). `config.TTS_PROVIDER` only supports `"openrouter"` (`tts_openrouter.OpenRouterTTS`) — Google Cloud TTS and Amazon Polly were both implemented and then removed (see "Current state"). Everything is normalised to **24 kHz mono `.wav`** before it reaches `dub.py`, because the dub track is one mixed file and mixing sample rates would break it.

**Two rules inherited from the removed Polly integration, both still binding.** (1) *Explicit, project-scoped credentials only.* Polly's credentials were read literally from `worker/.env`, never via `boto3`'s default chain, because that chain falls back to `~/.aws/credentials`'s `default` profile, shell env vars, or an IAM role — any of which could belong to an unrelated account. This bit once for real: a test silently ran against the operator's employer AWS account because it was the only profile on the machine. If any cloud integration is added here again, follow that pattern — never the SDK's ambient-credential discovery. (2) *An empty env var is set, not absent.* The `AWS_PROFILE=` attempt failed because an empty value in `.env` still sets the process variable, and boto3 then tried to resolve a profile literally named `""`. The same trap reappeared in `DUB_VOICE_POOL`: `os.environ.get(key, default)` returns `""`, not the default, so `config.py` uses `os.environ.get(key) or default` for every var whose blank value should mean "use the default".

### Dubbing — the trickiest part (`dub.py`)

Core problem: PT-BR speech tends to run longer than the English original, so the dubbed audio can overrun the segment's time slot. Approach:

1. For each `Segment`, generate TTS audio from `seg.translation`.
2. Compute the target duration `dur = seg.end - seg.start`.
3. **Fit to the time slot:**
   - If TTS clip > `dur`: ask the **model** for faster speech (`speed`) when it honours the parameter; otherwise fall back to ffmpeg `atempo`. Cap ~1.3x either way; beyond that, allow bleed into the next gap. Resynthesis beats `atempo` because the model re-plans prosody instead of resampling finished audio — `atempo` at 1.3x was a measured cause of the "robotic" complaint.
   - If TTS clip < `dur` by a wide margin (`_SLOWDOWN_TRIGGER_RATIO` 0.85): **slow it down** instead of leaving a long silence, floored at `_SLOWDOWN_MIN_RATE_PERCENT` (75%).
   - Otherwise: pad with trailing silence.
4. **Assemble the full track:** build a silent track the length of the whole video and place each clip at its `seg.start` (via `adelay`/`amix` or concatenation with computed silence gaps). Result: a single `.wav` matching video length, each line in its correct spot.
5. Return the path to that dubbed track.

**Multi-voice via diarization (`steps/diarize.py`).** With `DUB_ENABLE_DIARIZATION=true` *and* an `HF_TOKEN`, pyannote (`speaker-diarization-3.1`, a gated HF model — the terms must be accepted on both `speaker-diarization-3.1` and `segmentation-3.0` first) labels who speaks when over the **same `.wav` already extracted for transcription**, and runs on its own thread **in parallel with Whisper**, since both only read that file. `dub._voice_pool()` then gives each detected speaker a different voice: `(DUB_MODEL, DUB_VOICE)` first, then the `model|voice` entries of `DUB_VOICE_POOL`. The pool **spans three different OpenRouter models** (Gemini, Grok, Kokoro) because that is how the voices the operator approved by ear happened to fall — which is why a pool entry carries its model, not just a voice id. Any failure, a missing token, or the flag off returns an empty list and falls back to one voice for everyone — diarization is never allowed to fail a job.

**Per-model quirks live in `tts_openrouter.py`, all measured on this account — none of them are documented together anywhere upstream:**
- **Output format.** Gemini TTS rejects `response_format: "mp3"` with HTTP 400 and only accepts `"pcm"` (raw 24 kHz 16-bit mono, wrapped into `.wav` locally). Others take mp3. Hence `_PCM_ONLY_MODELS`.
- **`speed` is advertised API-wide but not honoured by every model.** Same sentence, `speed` absent / 1.3 / 0.8: Gemini returned 5.40s / 5.68s / 5.80s (**ignores it** — the spread is the model's own non-determinism), Grok sped up but would not slow down, and only Kokoro responded correctly in both directions. `_SPEED_CAPABLE_MODELS` is therefore an allowlist, deliberately conservative: asking a model that ignores `speed` would silently leave the line out of time.
- **Lines that still overrun are pushed, not overlapped.** `_stagger_starts` places each clip at `max(planned start, end of the previous one)`, because `_mix_track` sums everything and a clip that outran its slot otherwise played *on top of* the next line. The accumulated delay dissolves at the first pause in the video (a gap absorbs it and the dub re-syncs with the picture); `DUB_MAX_DRIFT` (1.5s) caps how far a line may be pushed before overlap is preferred, since a visibly out-of-sync dub is worse than two lines touching.
- **The subtitles follow the dub, not Whisper.** Whisper times the *English* audio, while what plays is the PT-BR dub, refitted and possibly shifted. `synthesize_dub` therefore rewrites `seg.start`/`seg.end` to where each line actually plays. This was found the hard way: with only the audio shifted, **the caption ran ahead of the voice** on a real render. `pipeline.run` also rewrites the `.srt`/`.vtt` *after* dubbing, because they are generated before it and would otherwise ship ahead of the MP4's audio.
- **`_mix_track` uses `amix=duration=first`**, so the silent base track must be sized by `max(start + clip_duration)` — sizing it by `max(seg.end)` **truncated the last line mid-word**.

Relevant config: `DUB_MODEL`/`DUB_VOICE` (default `google/gemini-3.1-flash-tts-preview` / `Algieba`), `DUB_VOICE_POOL` (extra `model|voice` entries for multi-speaker; may mix models), `DUB_MAX_SPEEDUP` (1.3), `DUB_KEEP_MUSIC` (bool — turns source separation on), `DUB_MUSIC_DB` (bed level, by ear), `DUB_SEPARATION_JOBS` (4; see "Current state" for why more is slower), `HF_TOKEN`/`DUB_ENABLE_DIARIZATION`.

### Final render (`render.py`)

`build_final` must produce a single mp4 combining:
- the original video (image untouched);
- **dubbed audio** replacing the original voice:
  - if `DUB_KEEP_MUSIC=True`, the pipeline separates the original audio first and passes `opts["music_audio"]` — the voice-free bed — which `render` mixes under the dub at `DUB_MUSIC_DB`;
  - if `False` (or separation failed), the audio is the dub alone. The raw original track is never mixed in, because it carries the original voice.
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
    # order: [download audio ‖ download video] → transcribe (‖ diarize) → translate
    #        → subtitle → dub → [separate] → render(final)
    # when source_lang == target_lang: translate and dub are skipped (see
    # _same_language); render still runs, keeping the original audio
    # emit on_progress at each step with increasing pct and a clear PT-BR message
```

`video_path` and `source_url` are mutually exclusive-ish (`source_url` wins by populating `video_path` internally after download) but at least one is required. In practice every current caller (`worker.py`, `cli.py`) passes only `source_url` — `video_path` is kept as an engine-level affordance (e.g. ad hoc scripts/tests against a local sample file).

The orchestrator knows nothing about HTTP, Redis, or jobs — only the callback.

`run` wraps the whole pipeline in a try/except and, on any failure, raises `PipelineError(message, partial_result)` — `partial_result` is the `PipelineResult` built so far (e.g. `srt_path` already set even if dubbing later fails). Callers (`cli.py`, `worker.py`) catch `PipelineError` specifically to recover whatever artifacts were already produced instead of losing them when a downstream step fails.

## Web service (`webapp/`)

Endpoints (`main.py`):
- `GET /api/languages`: the `LANGUAGES` list from `webapp/languages.py` with `enabled_as_source`/`enabled_as_target` flags — the single source of truth behind Tela 2's selects, so neither the template nor the JS hardcodes a language list.
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
3. **Tela 3** (`/v/{id}` while queued/running, `#screen-processing`) — giant percentage + current-stage label driven directly by the `pct`/`step` the backend already emits. **The stage list is derived from the job, not fixed**: `PROC_STAGES_TRANSLATED` vs `PROC_STAGES_SAME_LANG`, chosen by `isSameLanguageJob(status)`, because a PT→PT job never translates or dubs and announcing those stages was simply false. The engine still emits the `translate` and `dub` step ids on that path with a different meaning, so `PROC_LABELS_SAME_LANG` relabels them ("Preparando a legenda", "Mantendo o áudio original"). `separate` also needs an entry, or the separation step (~35s) shows up as "Preparando" (no client-side interpolation — a deliberate simplicity choice: the engine's own step weights, e.g. `transcribe→20`, `dub→75`, already read fine as "progress"), a video info card, and a bottom band with "pode fechar a aba" copy, a rough ETA, and an inline (non-`window.confirm`) Cancelar → `POST /api/jobs/{id}/cancel`.
4. **Tela 4** (`/v/{id}` when `status=done`, `#screen-result`) — **durations shown are the clip's, not the source's** (`status.clip_duration || status.video_duration`); a one-hour source cut to 60s was being described as a one-hour result, on both Tela 3 and Tela 4 including its ETA. On the same-language path the audio line reads "áudio original" rather than "voz em Português", since no voice was synthesised, and `buildLangMeta` says "Português — sem tradução" instead of "Português para Português" — native `<video controls>` with a `<track kind="subtitles">` pointed at the signed `vtt_url` (only when `include_subtitles`), download cards for the MP4 (always) and SRT (hidden entirely, MP4 card goes full-width, when subtitles were off), file sizes fetched via a `HEAD` request against the signed URL, and "Traduzir para outro idioma" (re-runs Tela 2's probe flow against the same `source_url`).
5. **Tela 5** (`/v/{id}` when `status=error|cancelled`, or the job id isn't found, `#screen-error`) — generic code→content component (`ERROR_CONTENT` map in the script), `role="alert"`, focuses the title on entry; never shows a raw stack trace, only the short `error_code`-driven copy plus (for `falha_interna`) the first 8 chars of the job id, for support purposes.

Loading `/v/{id}` directly (fresh tab, refresh, a link opened later) fully reconstructs whichever of Telas 3/4/5 applies from a single `GET /api/jobs/{id}` call — there is no reliance on in-memory state surviving a reload, since the backend now returns everything the UI needs (title, duration, languages, subtitle choice, error code, signed URLs).

**Orphaned outputs are not collected automatically.** `storage/outputs/` accumulates files whose job records have already expired from Redis (24h TTL) — discard-on-pagehide only fires while a tab is open on that result. 343MB of pre-pivot leftovers were swept manually in 2026-09 by listing `OUTPUTS_DIR` and deleting anything not named in a live job hash's `result_video`/`result_srt`/`result_vtt`. There is no cron for this; if it becomes a nuisance, that cross-reference is the safe rule to automate.

**File cleanup stays discard-on-pagehide, not a fixed TTL** (explicit operator decision): `navigator.sendBeacon('/api/jobs/{id}/discard')` fires on `pagehide` for whichever job is currently "active" on Tela 4 (or Tela 5, if a partial `.srt` survived a failed dub) — this is why Tela 4 has no "apagamos em 24h" messaging; the file lives until the visitor actually navigates away.

**Trim bar — a fixed 60s window that slides.** After "Processar" resolves via `GET /api/probe`, the trim bar (Tela 2) appears with a `CLIP_SECONDS`-wide window at the start of the video. It is **not resizable** — `#trim-window` has no grips; it is dragged along `#trim-track`, or the track is clicked to centre the window on that point. `setClipStart(start)` clamps the start so the window stays inside the video, falls back to the whole video when it is shorter than 60s, recomputes `needsTrim` and refreshes the estimate. `.trim-window`'s `min-width: 28px` is load-bearing: on a one-hour source, 60s is ~1.6% of the track and the window would otherwise be too thin to grab. On submit, `start`/`clip_duration` are included in the `POST /api/jobs` JSON body only when `needsTrim` is true. There's no local video preview (the source is a remote URL, not a local file) — just the track + time labels.

## Configuration (`.env` per service)

`webapp/.env` (see `webapp/.env.example`): `REDIS_URL`, `STORAGE_DIR`, `OUTPUTS_DIR`, `YOUTUBE_COOKIES_FILE`. No API keys — the web service has no cloud secrets (but see "YouTube access" on the cookie file, which *is* a credential).

`worker/.env` (see `worker/.env.example`): `REDIS_URL`, storage paths (`STORAGE_DIR`/`OUTPUTS_DIR`/`WORK_DIR`), `OPENROUTER_API_KEY`/`OPENROUTER_MODEL`/`OPENROUTER_BATCH_SIZE` (translation, the only provider), `TARGET_LANG` (there is no `SOURCE_LANG` any more — the source language arrives per job from Tela 2), `YOUTUBE_COOKIES_FILE`, `WHISPER_MODEL`/`WHISPER_DEVICE`/`WHISPER_COMPUTE_TYPE`, `TRANSLATE_PROVIDER`/`TRANSLATE_STYLE`, `TTS_PROVIDER`/`DUB_MODEL`/`DUB_VOICE`/`DUB_VOICE_POOL`/`DUB_MAX_SPEEDUP`/`DUB_KEEP_MUSIC`/`DUB_MUSIC_DB`/`DUB_SEPARATION_JOBS`, `HF_TOKEN`/`DUB_ENABLE_DIARIZATION` (optional speaker diarization), `BURN_SUBS`, and the Shorts block: `SHORT_MAX_SECONDS` (180), `REFRAME_MODE` (fallback when a job doesn't specify one), `CAPTION_MAX_WORDS`/`CAPTION_UPPERCASE`/`CAPTION_FONT`/`CAPTION_FONT_SIZE`/`CAPTION_MARGIN_V`. Note `CAPTION_FONT` can only name a font actually installed in the worker image — today that's DejaVu Sans alone; a punchier Shorts face (Montserrat ExtraBold and the like) means adding it to `worker/Dockerfile` too.

In `docker-compose.yml`, both services get `REDIS_URL=redis://redis:6379/0` injected via `environment:` (overriding whatever's in the `.env` file, which defaults to `redis://localhost:6379/0` for non-Docker local runs).

## YouTube access (cookies + bot check)

YouTube intermittently demands "confirm you're not a robot" before serving metadata or streams, and both services hit it — `webapp/youtube_probe.py` on every `GET /api/probe`, `worker/engine/steps/download.py` on every job. Two layers handle it, and they are **duplicated per service** like everything else across the boundary:

- **`_bot_check_opts()`** (`download.py`) forces `extractor_args={"youtube": {"player_client": ["android", "web"]}}`. Both Dockerfiles carry a comment saying that without this, *even valid cookies* fail with "No video formats found". Keep both clients — `android` is the one that usually works, `web` is the fallback.
- **`cookies.txt`** — a Netscape-format export of a logged-in YouTube session, path overridable via `YOUTUBE_COOKIES_FILE` (defaults to `cookies.txt` beside `webapp/`/`worker/`). Used only when the file exists, so a machine without one still works for public videos.

⚠️ **The cookie files are committed to git** (`webapp/cookies.txt`, `worker/cookies.txt`), and they contain live Google account session cookies (`SID`, `SAPISID`, `__Secure-1PSID`, `LOGIN_INFO`, …) — credentials that grant access to the operator's Google account, not just to YouTube. `engine/config.py` documents this as an explicit operator decision; **both `.env.example` files contradict it**, claiming the file is "nunca commitado (ver .gitignore)", and `.github/workflows/deploy.yml` notes the GitHub repo is public. If you touch this area: don't paste cookie contents into logs, issues, or any outbound request, and don't "fix" the situation by rotating or deleting them unprompted — but do surface the discrepancy rather than trusting the `.env.example` comment.

## Deployment (GitHub Actions → EC2)

`.github/workflows/deploy.yml` deploys on every push to `main` (plus manual `workflow_dispatch`) — deliberately **not** on `pull_request`, so secrets never run against fork code on a public repo. It SSHes into the EC2 box (`appleboy/ssh-action`) and:

1. `git fetch origin main && git reset --hard origin/main` in `~/youcreate` (cloning first if absent) — **the EC2 instance is a deploy target, not an editing surface; any change made directly on the server is discarded on the next deploy.**
2. Rewrites `worker/.env` and `webapp/.env` from scratch out of the `WORKER_ENV_FILE`/`WEBAPP_ENV_FILE` GitHub Secrets — so *the way to change production config is to edit those secrets*, never to edit the file on the box.
3. `docker compose up -d --build && docker image prune -f`.
4. Health check: `curl -sf http://localhost:8000/`, failing the job if the webapp doesn't answer.

Required repo secrets: `EC2_HOST`, `EC2_USER`, `EC2_SSH_KEY`, `WORKER_ENV_FILE`, `WEBAPP_ENV_FILE`.

`deploy/setup-ec2.sh` is the one-shot bootstrap for a fresh Ubuntu instance (Docker Engine + Compose plugin + git, and adds the user to the `docker` group — which only takes effect in a new SSH session). Run it manually once, before the first deploy.

`docker-compose.yml` pins `worker` to `replicas: 1` for the current testing phase; scale with `docker compose up -d --scale worker=N` rather than editing the file. Named volumes: `redis-data`, `outputs` (shared web↔worker), `whisper-cache` (`/root/.cache/huggingface` — holds both the Whisper and the ~80MB Demucs model, which is why neither is re-downloaded on restart).

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

**There is no test suite and no linter configured** — no `tests/` directory, no pytest/ruff/mypy in either `requirements.txt`, no CI check beyond the deploy workflow's `curl` health probe. Verification today is running a real job end to end (`cd worker && python cli.py <url> --start 0 --duration 60`) and reading `storage/youcreate-worker.log`. Don't invent a test command; if you add tests, add the runner to the relevant `requirements.txt` too.

**Docker Compose** (recommended — this is the actual deploy target):
- Start everything: `docker compose up -d --build`
- Scale worker replicas: `docker compose up -d --scale worker=3`
- Logs: `docker compose logs -f web` / `docker compose logs -f worker`
- Full setup/config walkthrough (obtaining AWS/Polly credentials, `OPENROUTER_API_KEY`): see `README.md`
