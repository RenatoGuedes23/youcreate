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
from engine.pipeline import PipelineCancelled, PipelineError
from engine.pipeline import run as run_pipeline
from logging_setup import setup_logging

import queue_client

setup_logging(config.STORAGE_DIR / "youcreate-worker.log")
logger = logging.getLogger(__name__)


def _classify_error(message: str) -> str:
    """Mapeia heuristicamente a mensagem de erro (yt-dlp ou interna) para um
    codigo curto que a Tela 5 do site usa pra escolher titulo/texto/acoes.
    Best-effort por string matching -- nao ha API estruturada de erros do
    yt-dlp -- entao isto deve ser revisto/ampliado conforme novos casos
    reais aparecerem nos logs. video_longo nao e verificado aqui (nao ha
    limite de duracao no motor); o codigo existe so para a Tela 5 poder
    exibi-lo quando/se um limite desses for adicionado no futuro."""
    text = message.lower()
    if "private video" in text or "sign in if you" in text:
        return "video_privado"
    if "video unavailable" in text or "has been removed" in text or "no longer available" in text:
        return "video_indisponivel"
    if "not made this video available in your country" in text or "blocked it in your country" in text:
        return "restrito_regiao"
    if "does not contain any stream" in text or "no audio" in text or "audio stream not found" in text:
        return "sem_audio"
    return "falha_interna"


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
            source_lang=job.source_lang or None,
            max_height=int(job.video_quality) if job.video_quality else None,
            make_subs=job.include_subtitles,
            make_dub=True,
            should_cancel=lambda: queue_client.is_cancelled(job_id),
        )
        queue_client.update_job(
            job_id,
            status="done",
            result_video=result.video_out.name if result.video_out else "",
            result_srt=result.srt_path.name if result.srt_path else "",
            result_vtt=result.vtt_path.name if result.vtt_path else "",
        )
        queue_client.publish_event(job_id, {"done": True, "status": "done"})
        logger.info("Job %s concluido", job_id)
    except PipelineCancelled:
        queue_client.update_job(job_id, status="cancelled", message="Cancelado.")
        queue_client.publish_event(job_id, {"done": True, "status": "cancelled"})
        logger.info("Job %s cancelado pelo operador", job_id)
    except PipelineError as exc:
        result_srt = exc.partial_result.srt_path.name if exc.partial_result.srt_path else ""
        error_code = _classify_error(str(exc))
        queue_client.update_job(
            job_id, status="error", message=str(exc), result_srt=result_srt, error_code=error_code,
        )
        queue_client.publish_event(job_id, {"done": True, "status": "error"})
        logger.error("Job %s falhou (%s): %s", job_id, error_code, exc)
    except Exception as exc:
        queue_client.update_job(job_id, status="error", message=str(exc), error_code="falha_interna")
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
