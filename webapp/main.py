"""Site (FastAPI): recebe a URL do YouTube, enfileira o job no Redis e serve
status/progresso/downloads. Nunca processa video -- isso e trabalho do
worker, um servico totalmente separado. Os dois so se falam via Redis
(queue_client.py) -- nao ha nenhum arquivo compartilhado entre eles.
"""
import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

import queue_client
from logging_setup import setup_logging
from schemas import JobCreated, JobCreateRequest, JobStatus, ProbeResult
from youtube_probe import probe as probe_source

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

STORAGE_DIR = Path(os.environ.get("STORAGE_DIR", BASE_DIR / "storage"))
OUTPUTS_DIR = Path(os.environ.get("OUTPUTS_DIR", STORAGE_DIR / "outputs"))
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

setup_logging(STORAGE_DIR / "youcreate-web.log")
logger = logging.getLogger(__name__)

app = FastAPI(title="youcreate")

_INDEX_PATH = BASE_DIR / "web" / "index.html"


@app.get("/")
def index() -> HTMLResponse:
    if not _INDEX_PATH.exists():
        raise HTTPException(status_code=404, detail="web/index.html ainda nao existe.")
    return HTMLResponse(_INDEX_PATH.read_text(encoding="utf-8"))


@app.get("/api/probe", response_model=ProbeResult)
def probe(url: str) -> ProbeResult:
    """Consulta duracao/titulo de uma URL do YouTube, sem baixa-la (usado para
    montar a barra de corte antes de disparar o job)."""
    try:
        info = probe_source(url)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ProbeResult(duration=info["duration"], title=info["title"])


@app.post("/api/jobs", response_model=JobCreated)
def create_job(payload: JobCreateRequest) -> JobCreated:
    job_id = queue_client.create_job(
        source_url=payload.url,
        url_clip_start=payload.start,
        url_clip_duration=payload.clip_duration,
    )
    logger.info("Job %s criado e enfileirado para %s", job_id, payload.url)
    return JobCreated(id=job_id)


@app.get("/api/jobs/{job_id}", response_model=JobStatus)
def get_job(job_id: str) -> JobStatus:
    job = queue_client.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job nao encontrado.")
    return JobStatus(
        id=job.id,
        status=job.status,
        pct=job.pct,
        step=job.step,
        message=job.message,
        video=job.result_video,
        srt=job.result_srt,
    )


@app.get("/api/jobs/{job_id}/events")
def job_events(job_id: str) -> StreamingResponse:
    job = queue_client.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job nao encontrado.")

    def event_stream():
        pubsub = queue_client.subscribe(job_id)
        try:
            # O job pode ja ter terminado antes de nos inscrevermos no canal
            # (ex: worker rapido, ou reconexao do cliente) -- confere o estado
            # atual primeiro para nao esperar por um evento que nunca vira.
            current = queue_client.get_job(job_id)
            if current and current.status in ("done", "error"):
                yield f"data: {json.dumps({'done': True, 'status': current.status})}\n\n"
                return

            while True:
                message = pubsub.get_message(timeout=5.0, ignore_subscribe_messages=True)
                if message is None:
                    # Sem evento nos ultimos 5s -- reconfere o status como rede
                    # de seguranca (cobre perder a mensagem final publicada
                    # entre nosso get_job() acima e a inscricao no canal).
                    current = queue_client.get_job(job_id)
                    if current and current.status in ("done", "error"):
                        yield f"data: {json.dumps({'done': True, 'status': current.status})}\n\n"
                        return
                    continue
                data = json.loads(message["data"])
                yield f"data: {json.dumps(data)}\n\n"
                if data.get("done"):
                    return
        finally:
            pubsub.close()

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/download/{filename}")
def download(filename: str) -> FileResponse:
    safe_name = Path(filename).name
    path = OUTPUTS_DIR / safe_name
    if not path.exists():
        raise HTTPException(status_code=404, detail="Arquivo nao encontrado.")
    return FileResponse(path)


@app.post("/api/jobs/{job_id}/discard")
def discard_job(job_id: str) -> dict:
    """Apaga os arquivos finais (video + srt) de um job ja concluido.

    Chamado pelo frontend quando o usuario sai da tela de resultado -- volta
    para o inicio ou fecha/navega para fora da pagina (via
    navigator.sendBeacon no evento pagehide) -- ja que ate esse momento ele
    ainda pode baixar de novo. Nao apaga nada do Redis (o TTL do job cuida
    disso); so os arquivos em OUTPUTS_DIR, que sao o que ocupa disco."""
    job = queue_client.get_job(job_id)
    if job is None:
        return {"ok": True}

    removed = []
    for name in (job.result_video, job.result_srt):
        if not name:
            continue
        path = OUTPUTS_DIR / Path(name).name
        if path.exists():
            path.unlink()
            removed.append(name)

    if removed:
        logger.info("Job %s: resultado descartado (%s)", job_id, ", ".join(removed))
    return {"ok": True}
