"""Cliente Redis do site: cria jobs e le status/eventos (lado produtor).

Isto e metade de um protocolo de fila compartilhado com o worker -- a outra
metade (consumidor: dequeue/update_job/publish_event) mora em
worker/queue_client.py, como copia propria, nao como import. Os dois
servicos so se falam atraves do Redis (chaves/canais abaixo), nunca por
codigo compartilhado -- se o formato do hash ou o nome das chaves mudar
aqui, precisa mudar la tambem.
"""
import os
import time
import uuid
from dataclasses import dataclass

import redis

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
QUEUE_KEY = "youcreate:queue"
JOB_TTL_SECONDS = 24 * 60 * 60  # metadados do job somem do Redis 24h depois de criado

_client: redis.Redis | None = None


def _redis() -> redis.Redis:
    global _client
    if _client is None:
        # socket_timeout generoso: o SSE (job_events) faz pubsub.get_message
        # com timeout de alguns segundos em loop -- socket_timeout precisa
        # ser confortavelmente maior para nao estourar antes da hora.
        _client = redis.Redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=30)
    return _client


def _job_key(job_id: str) -> str:
    return f"youcreate:job:{job_id}"


def _channel(job_id: str) -> str:
    return f"youcreate:job:{job_id}:events"


@dataclass
class JobRecord:
    id: str
    source_url: str
    created_at: float = 0.0
    url_clip_start: float = 0.0
    url_clip_duration: float | None = None
    source_lang: str = ""
    target_lang: str = ""
    include_subtitles: bool = True
    video_title: str = ""
    video_duration: float = 0.0
    status: str = "queued"   # queued|running|done|error|cancelled
    pct: int = 0
    step: str = ""
    message: str = ""
    error_code: str = ""
    cancel_requested: bool = False
    result_video: str = ""
    result_srt: str = ""
    result_vtt: str = ""


def create_job(
    source_url: str,
    url_clip_start: float = 0.0,
    url_clip_duration: float | None = None,
    source_lang: str = "",
    target_lang: str = "",
    include_subtitles: bool = True,
    video_title: str = "",
    video_duration: float = 0.0,
) -> str:
    """Cria o job no Redis e o enfileira para o worker pegar; devolve o id."""
    job_id = str(uuid.uuid4())
    r = _redis()
    key = _job_key(job_id)
    r.hset(key, mapping={
        "id": job_id,
        "source_url": source_url,
        "created_at": time.time(),
        "url_clip_start": url_clip_start,
        "url_clip_duration": url_clip_duration if url_clip_duration is not None else "",
        "source_lang": source_lang,
        "target_lang": target_lang,
        "include_subtitles": "1" if include_subtitles else "",
        "video_title": video_title,
        "video_duration": video_duration,
        "status": "queued",
        "pct": 0,
        "step": "",
        "message": "",
        "error_code": "",
        "cancel_requested": "",
        "result_video": "",
        "result_srt": "",
        "result_vtt": "",
    })
    r.expire(key, JOB_TTL_SECONDS)
    r.rpush(QUEUE_KEY, job_id)
    return job_id


def get_job(job_id: str) -> JobRecord | None:
    data = _redis().hgetall(_job_key(job_id))
    if not data:
        return None
    return JobRecord(
        id=data["id"],
        source_url=data["source_url"],
        created_at=float(data.get("created_at") or 0.0),
        url_clip_start=float(data.get("url_clip_start") or 0.0),
        url_clip_duration=float(data["url_clip_duration"]) if data.get("url_clip_duration") else None,
        source_lang=data.get("source_lang", ""),
        target_lang=data.get("target_lang", ""),
        include_subtitles=data.get("include_subtitles", "1") == "1",
        video_title=data.get("video_title", ""),
        video_duration=float(data.get("video_duration") or 0.0),
        status=data.get("status", "queued"),
        pct=int(data.get("pct") or 0),
        step=data.get("step", ""),
        message=data.get("message", ""),
        error_code=data.get("error_code", ""),
        cancel_requested=data.get("cancel_requested") == "1",
        result_video=data.get("result_video", ""),
        result_srt=data.get("result_srt", ""),
        result_vtt=data.get("result_vtt", ""),
    )


def request_cancel(job_id: str) -> bool:
    """Marca o job para ser cancelado na proxima checagem do worker (so entre
    etapas, ver PipelineCancelled em engine/pipeline.py). Devolve False se o
    job nao existe (ja pode ter terminado/expirado)."""
    key = _job_key(job_id)
    if not _redis().exists(key):
        return False
    _redis().hset(key, "cancel_requested", "1")
    return True


def subscribe(job_id: str) -> "redis.client.PubSub":
    """Devolve um pubsub ja inscrito no canal de eventos do job (usado pelo SSE)."""
    pubsub = _redis().pubsub()
    pubsub.subscribe(_channel(job_id))
    return pubsub
