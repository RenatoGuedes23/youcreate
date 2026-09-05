"""Worker: consome jobs da fila no Redis e roda o motor (engine/) para cada
um. E o unico servico que de fato baixa/transcreve/traduz/dubla/renderiza --
o site e um processo totalmente separado, que so enfileira jobs e le status.
Os dois so se falam via Redis (queue_client.py) -- nao ha nenhum arquivo
compartilhado entre eles.

Rode varias replicas em paralelo para processar jobs simultaneamente
(`docker compose up --scale worker=N`) -- BLPOP no Redis garante que cada job
e consumido por exatamente um worker, sem duplicar trabalho.
"""
import logging
import shutil

from engine import config
from engine.ffmpeg_utils import is_ffmpeg_available
from engine.pipeline import PipelineError
from engine.pipeline import run as run_pipeline
from logging_setup import setup_logging

import queue_client

setup_logging(config.STORAGE_DIR / "youcreate-worker.log")
logger = logging.getLogger(__name__)


def _process(job_id: str) -> None:
    job = queue_client.get_job(job_id)
    if job is None:
        logger.warning("Job %s sumiu do Redis antes de ser processado (TTL expirado?).", job_id)
        return

    work_dir = config.WORK_DIR / job_id
    queue_client.update_job(job_id, status="running")
    logger.info("Processando job %s (%s)", job_id, job.source_url)

    def on_progress(step_id: str, label: str, pct: int, message: str) -> None:
        queue_client.update_job(job_id, step=step_id, pct=pct, message=message)
        queue_client.publish_event(job_id, {"step": step_id, "label": label, "pct": pct, "message": message})

    try:
        result = run_pipeline(
            on_progress=on_progress,
            work_dir=work_dir,
            source_url=job.source_url,
            url_clip_start=job.url_clip_start,
            url_clip_duration=job.url_clip_duration,
        )
        queue_client.update_job(
            job_id,
            status="done",
            result_video=result.video_out.name if result.video_out else "",
            result_srt=result.srt_path.name if result.srt_path else "",
        )
        queue_client.publish_event(job_id, {"done": True, "status": "done"})
        logger.info("Job %s concluido", job_id)
    except PipelineError as exc:
        result_srt = exc.partial_result.srt_path.name if exc.partial_result.srt_path else ""
        queue_client.update_job(job_id, status="error", message=str(exc), result_srt=result_srt)
        queue_client.publish_event(job_id, {"done": True, "status": "error"})
        logger.error("Job %s falhou: %s", job_id, exc)
    except Exception as exc:
        queue_client.update_job(job_id, status="error", message=str(exc))
        queue_client.publish_event(job_id, {"done": True, "status": "error"})
        logger.exception("Job %s falhou de forma inesperada", job_id)
    finally:
        # Video baixado e artefatos intermediarios (audio extraido, clipes de
        # dublagem) sao temporarios -- so os resultados finais em OUTPUTS_DIR
        # (que web e worker compartilham via volume) precisam sobreviver ao job.
        shutil.rmtree(work_dir, ignore_errors=True)


def main() -> None:
    if not is_ffmpeg_available():
        logger.warning(
            "ffmpeg/ffprobe nao encontrados no PATH. O processamento de video "
            "vai falhar ate que sejam instalados (ver README)."
        )
    # WORK_DIR e local ao container do worker (nao compartilhado entre
    # replicas) -- qualquer pasta ja existente na inicializacao pertence a um
    # job que nao sobreviveu a um restart anterior (crash, kill, redeploy).
    if config.WORK_DIR.exists():
        for entry in config.WORK_DIR.iterdir():
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)

    logger.info("Worker iniciado (Redis: %s), aguardando jobs...", queue_client.REDIS_URL)
    while True:
        job_id = queue_client.dequeue(timeout=5)
        if job_id is None:
            continue
        _process(job_id)


if __name__ == "__main__":
    main()
