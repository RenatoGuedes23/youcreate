"""Store de jobs em memoria (v1) -- atras de uma interface trocavel.

No futuro (ver CLAUDE.md, "Future seams"), create/get podem ser reimplementados
sobre Redis/worker e persistencia em banco, sem tocar nos endpoints.
"""
import logging
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from queue import Queue

from engine import config
from engine.pipeline import PipelineError
from engine.pipeline import run as run_pipeline

logger = logging.getLogger(__name__)

_jobs: dict[str, "Job"] = {}
_lock = threading.Lock()


@dataclass
class Job:
    id: str
    video_path: Path
    status: str = "queued"   # queued|running|done|error
    pct: int = 0
    step: str = ""
    message: str = ""
    result_video: str = ""
    result_srt: str = ""
    events: Queue = field(default_factory=Queue)


def _run_job(job: Job) -> None:
    job.status = "running"

    def on_progress(step_id: str, label: str, pct: int, message: str) -> None:
        job.step = step_id
        job.pct = pct
        job.message = message
        job.events.put({"step": step_id, "label": label, "pct": pct, "message": message})

    try:
        result = run_pipeline(
            job.video_path,
            on_progress=on_progress,
            work_dir=config.WORK_DIR / job.id,
        )
        if result.video_out:
            job.result_video = result.video_out.name
        if result.srt_path:
            job.result_srt = result.srt_path.name
        job.status = "done"
        logger.info("Job %s concluido", job.id)
    except PipelineError as exc:
        job.status = "error"
        job.message = str(exc)
        if exc.partial_result.srt_path:
            job.result_srt = exc.partial_result.srt_path.name
        logger.error("Job %s falhou: %s", job.id, exc)
        job.events.put({"step": "error", "label": "Erro", "pct": job.pct, "message": str(exc)})
    except Exception as exc:
        job.status = "error"
        job.message = str(exc)
        logger.exception("Job %s falhou de forma inesperada", job.id)
        job.events.put({"step": "error", "label": "Erro", "pct": job.pct, "message": str(exc)})
    finally:
        job.events.put(None)


def create(video_path: Path) -> Job:
    """Cria um job e dispara o processamento em uma thread de fundo."""
    job = Job(id=str(uuid.uuid4()), video_path=video_path)
    with _lock:
        _jobs[job.id] = job
    logger.info("Job %s criado para %s", job.id, video_path)
    threading.Thread(target=_run_job, args=(job,), daemon=True).start()
    return job


def get(job_id: str) -> Job | None:
    with _lock:
        return _jobs.get(job_id)
