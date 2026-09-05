"""Backend FastAPI: upload, status, progresso via SSE e download.

Este modulo apenas embrulha o motor (engine/) com HTTP -- nenhuma logica de
processamento de video vive aqui.
"""
import json
import logging
import shutil
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

from api import jobs
from api.schemas import JobCreated, JobStatus
from engine import config
from engine.ffmpeg_utils import is_ffmpeg_available
from engine.steps.trim import trim_video
from logging_config import setup_logging

setup_logging(config.STORAGE_DIR / "youcreate.log")
logger = logging.getLogger(__name__)

app = FastAPI(title="youcreate")


@app.on_event("startup")
def _check_ffmpeg() -> None:
    if not is_ffmpeg_available():
        logger.warning(
            "ffmpeg/ffprobe nao encontrados no PATH. O processamento de video "
            "vai falhar ate que sejam instalados (ver README)."
        )


@app.get("/")
def index() -> HTMLResponse:
    index_path = Path("web/index.html")
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="web/index.html ainda nao existe.")
    return HTMLResponse(index_path.read_text(encoding="utf-8"))


@app.post("/api/jobs", response_model=JobCreated)
async def create_job(
    file: UploadFile = File(...),
    start: float | None = Form(None),
    clip_duration: float | None = Form(None),
) -> JobCreated:
    if not file.filename or not file.filename.lower().endswith(".mp4"):
        raise HTTPException(status_code=400, detail="Apenas arquivos .mp4 sao aceitos.")

    safe_name = Path(file.filename).name
    dest = config.UPLOADS_DIR / f"{uuid.uuid4()}_{safe_name}"
    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)

    video_path = dest
    if clip_duration is not None and clip_duration > 0:
        trimmed = config.UPLOADS_DIR / f"{dest.stem}_trim{dest.suffix}"
        video_path = trim_video(dest, start or 0.0, clip_duration, trimmed)

    job = jobs.create(video_path)
    return JobCreated(id=job.id)


@app.get("/api/jobs/{job_id}", response_model=JobStatus)
def get_job(job_id: str) -> JobStatus:
    job = jobs.get(job_id)
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
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job nao encontrado.")

    def event_stream():
        while True:
            event = job.events.get()
            if event is None:
                yield f"data: {json.dumps({'done': True, 'status': job.status})}\n\n"
                break
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/download/{filename}")
def download(filename: str) -> FileResponse:
    safe_name = Path(filename).name
    path = config.OUTPUTS_DIR / safe_name
    if not path.exists():
        raise HTTPException(status_code=404, detail="Arquivo nao encontrado.")
    return FileResponse(path)
