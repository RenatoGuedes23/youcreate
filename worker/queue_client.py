"""Cliente Redis do worker: consome jobs da fila e publica progresso (lado
consumidor).

Isto e metade de um protocolo de fila compartilhado com o site -- a outra
metade (produtor: create_job/get_job/subscribe) mora em webapp/queue_client.py,
como copia propria, nao como import. Os dois servicos so se falam atraves do
Redis (chaves/canais abaixo), nunca por codigo compartilhado -- se o formato
do hash ou o nome das chaves mudar aqui, precisa mudar la tambem.
"""
import json
import os
from dataclasses import dataclass

import redis

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
QUEUE_KEY = "youcreate:queue"
JOB_TTL_SECONDS = 24 * 60 * 60  # metadados do job somem do Redis 24h depois de criado

_client: redis.Redis | None = None


def _redis() -> redis.Redis:
    global _client
    if _client is None:
        # socket_timeout precisa ser maior que o timeout usado em dequeue()
        # (BLPOP) -- senao o cliente estoura o timeout do proprio socket
        # antes do Redis responder com nil ao fim do bloqueio.
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


def is_cancelled(job_id: str) -> bool:
    """Le direto do Redis (sem cache) se o site marcou o job para cancelamento.
    Chamado pelo pipeline entre etapas -- ver PipelineCancelled."""
    return _redis().hget(_job_key(job_id), "cancel_requested") == "1"


def update_job(job_id: str, **fields) -> None:
    key = _job_key(job_id)
    r = _redis()
    r.hset(key, mapping=fields)
    r.expire(key, JOB_TTL_SECONDS)


def publish_event(job_id: str, event: dict) -> None:
    _redis().publish(_channel(job_id), json.dumps(event))


def dequeue(timeout: int = 5) -> str | None:
    """Espera ate timeout segundos por um job na fila (BLPOP -- atomico entre
    varias replicas do worker, cada job e consumido por exatamente uma)."""
    result = _redis().blpop(QUEUE_KEY, timeout=timeout)
    if result is None:
        return None
    _, job_id = result
    return job_id
