"""Site (FastAPI): recebe a URL do YouTube, enfileira o job no Redis e serve
status/progresso/downloads. Nunca processa video -- isso e trabalho do
worker, um servico totalmente separado. Os dois so se falam via Redis
(queue_client.py) -- nao ha nenhum arquivo compartilhado entre eles.
"""
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import queue_client
from languages import languages_payload
from logging_setup import setup_logging
from schemas import JobCreated, JobCreateRequest, JobStatus, LanguageOption, ProbeResult
from youtube_probe import probe as probe_source

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

STORAGE_DIR = Path(os.environ.get("STORAGE_DIR", BASE_DIR / "storage"))
OUTPUTS_DIR = Path(os.environ.get("OUTPUTS_DIR", STORAGE_DIR / "outputs"))
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

setup_logging(STORAGE_DIR / "youcreate-web.log")
logger = logging.getLogger(__name__)

# Assina os links de download (Tela 4) para que nao fiquem validos pra sempre
# nem previsiveis so pelo nome do arquivo. Sem contas/sessao no projeto, entao
# nao ha "dono" do link pra checar -- so validade curta + assinatura. Sem
# DOWNLOAD_SIGN_SECRET no .env, gera uma por processo (perfeitamente aceitavel
# aqui: so invalida links antigos apos um restart do container, nao ha nada
# sensivel sendo protegido de fato, e o arquivo em si some quando a pagina de
# resultado e fechada de qualquer forma).
DOWNLOAD_SIGN_SECRET = os.environ.get("DOWNLOAD_SIGN_SECRET") or secrets.token_hex(32)
DOWNLOAD_URL_TTL_SECONDS = 60 * 60

app = FastAPI(title="youcreate")
app.mount("/assets", StaticFiles(directory=BASE_DIR / "web" / "assets"), name="assets")

_INDEX_PATH = BASE_DIR / "web" / "index.html"


def _sign(filename: str, exp: int) -> str:
    msg = f"{filename}:{exp}".encode()
    return hmac.new(DOWNLOAD_SIGN_SECRET.encode(), msg, hashlib.sha256).hexdigest()


def _download_url(filename: str) -> str:
    if not filename:
        return ""
    exp = int(time.time()) + DOWNLOAD_URL_TTL_SECONDS
    sig = _sign(filename, exp)
    return f"/api/download/{filename}?exp={exp}&sig={sig}"


@app.get("/")
@app.get("/configurar")
@app.get("/v/{job_id}")
def index(job_id: str = "") -> HTMLResponse:
    """Serve o mesmo SPA pras tres telas ('/', '/configurar' e '/v/<id>') --
    a troca entre elas e so roteamento client-side (history.pushState), sem
    reload de pagina. Servir o mesmo arquivo nas tres rotas evita 404 se o
    usuario der refresh direto numa delas."""
    if not _INDEX_PATH.exists():
        raise HTTPException(status_code=404, detail="web/index.html ainda nao existe.")
    return HTMLResponse(_INDEX_PATH.read_text(encoding="utf-8"))


@app.get("/api/languages", response_model=list[LanguageOption])
def languages() -> list[LanguageOption]:
    """Idiomas suportados -- fonte unica para a faixa da Tela 1 e os
    seletores da Tela 2 (ver languages.py)."""
    return [LanguageOption(**lang) for lang in languages_payload()]


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
        source_lang=payload.source_lang,
        target_lang=payload.target_lang,
        include_subtitles=payload.include_subtitles,
        video_title=payload.video_title,
        video_duration=payload.video_duration,
        video_quality=payload.video_quality,
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
        error_code=job.error_code,
        created_at=job.created_at,
        source_url=job.source_url,
        source_lang=job.source_lang,
        video_title=job.video_title,
        video_duration=job.video_duration,
        target_lang=job.target_lang,
        include_subtitles=job.include_subtitles,
        video_quality=job.video_quality,
        video_name=job.result_video,
        srt_name=job.result_srt,
        video_url=_download_url(job.result_video),
        srt_url=_download_url(job.result_srt),
        vtt_url=_download_url(job.result_vtt),
    )


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    """Pede pro worker cancelar o job na proxima checagem entre etapas (ver
    PipelineCancelled em engine/pipeline.py) -- nao interrompe uma etapa em
    andamento, so evita comecar a proxima."""
    ok = queue_client.request_cancel(job_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Job nao encontrado.")
    logger.info("Job %s: cancelamento solicitado", job_id)
    return {"ok": True}


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
            if current and current.status in ("done", "error", "cancelled"):
                yield f"data: {json.dumps({'done': True, 'status': current.status})}\n\n"
                return

            while True:
                message = pubsub.get_message(timeout=5.0, ignore_subscribe_messages=True)
                if message is None:
                    # Sem evento nos ultimos 5s -- reconfere o status como rede
                    # de seguranca (cobre perder a mensagem final publicada
                    # entre nosso get_job() acima e a inscricao no canal).
                    current = queue_client.get_job(job_id)
                    if current and current.status in ("done", "error", "cancelled"):
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
def download(filename: str, exp: int, sig: str) -> FileResponse:
    safe_name = Path(filename).name
    if time.time() > exp:
        raise HTTPException(status_code=403, detail="Link expirado.")
    if not hmac.compare_digest(_sign(safe_name, exp), sig):
        raise HTTPException(status_code=403, detail="Link invalido.")
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
    for name in (job.result_video, job.result_srt, job.result_vtt):
        if not name:
            continue
        path = OUTPUTS_DIR / Path(name).name
        if path.exists():
            path.unlink()
            removed.append(name)

    if removed:
        logger.info("Job %s: resultado descartado (%s)", job_id, ", ".join(removed))
    return {"ok": True}
