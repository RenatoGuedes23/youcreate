"""Orquestrador do motor: encadeia as etapas e emite progresso.

Nao conhece HTTP nem jobs -- apenas o callback on_progress (e, opcionalmente,
should_cancel, para permitir interromper entre etapas).
"""
import logging
from pathlib import Path
from typing import Callable

from engine import config
from engine.models import PipelineResult
from engine.steps import audio, download, subtitle, transcribe, translate

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str, str, int, str], None]


def _noop(step_id: str, label: str, pct: int, message: str) -> None:
    pass


class PipelineError(RuntimeError):
    """Erro do pipeline que preserva os artefatos parciais ja gerados.

    Se a legenda ja tiver sido gerada quando a dublagem falhar, por exemplo,
    partial_result.srt_path continua disponivel mesmo com a excecao levantada.
    """

    def __init__(self, message: str, partial_result: PipelineResult):
        super().__init__(message)
        self.partial_result = partial_result


class PipelineCancelled(RuntimeError):
    """Levantada quando should_cancel() diz sim entre duas etapas.

    So cancela ENTRE etapas (nunca no meio de uma chamada de ffmpeg/Whisper/
    tradutor/Polly em andamento) -- e uma escolha deliberada de simplicidade:
    interromper um subprocesso de ffmpeg ou uma chamada de API a meio caminho
    exigiria infraestrutura de cancelamento bem mais complexa.
    """

    def __init__(self, partial_result: PipelineResult):
        super().__init__("Cancelado pelo operador.")
        self.partial_result = partial_result


def run(
    video_path: Path | None = None,
    on_progress: ProgressCallback = _noop,
    make_subs: bool = True,
    make_dub: bool = True,
    work_dir: Path | None = None,
    source_url: str | None = None,
    url_clip_start: float = 0.0,
    url_clip_duration: float | None = None,
    source_lang: str | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> PipelineResult:
    if video_path is None and source_url is None:
        raise ValueError("Informe video_path ou source_url.")

    work_dir = work_dir or config.WORK_DIR
    result = PipelineResult()

    def _check_cancelled() -> None:
        if should_cancel is not None and should_cancel():
            raise PipelineCancelled(result)

    try:
        _check_cancelled()
        if source_url is not None:
            on_progress("download", "Baixando video", 2, "Baixando video da URL informada...")
            video_path = download.download_video(
                source_url,
                work_dir / "source",
                start=url_clip_start,
                duration=url_clip_duration,
            )

        _check_cancelled()
        on_progress("audio", "Extraindo audio", 5, "Extraindo audio do video...")
        audio_path = audio.extract_audio(video_path, work_dir)

        _check_cancelled()
        on_progress("transcribe", "Transcrevendo", 20, "Transcrevendo audio original...")
        segments = transcribe.transcribe(audio_path, language=source_lang or None)
        result.segments = segments

        _check_cancelled()
        on_progress("translate", "Traduzindo", 45, "Traduzindo falas para PT-BR...")
        translate.translate_segments(segments)

        if make_subs:
            _check_cancelled()
            on_progress("subtitle", "Gerando legenda", 60, "Gerando arquivo .srt...")
            srt_path = config.OUTPUTS_DIR / f"{video_path.stem}.pt-BR.srt"
            result.srt_path = subtitle.build_srt(segments, srt_path, translated=True)
            vtt_path = config.OUTPUTS_DIR / f"{video_path.stem}.pt-BR.vtt"
            result.vtt_path = subtitle.build_vtt(segments, vtt_path, translated=True)

        if make_dub:
            from engine.steps import dub, render

            _check_cancelled()
            on_progress("dub", "Dublando", 75, "Gerando dublagem PT-BR...")
            result.dub_audio_path = dub.synthesize_dub(segments, work_dir)

            _check_cancelled()
            on_progress("render", "Renderizando", 90, "Montando video final...")
            video_out = config.OUTPUTS_DIR / f"{video_path.stem}.pt-BR.mp4"
            result.video_out = render.build_final(
                video_path,
                result.srt_path,
                result.dub_audio_path,
                video_out,
                opts={"burn_subs": config.BURN_SUBS, "keep_music": config.DUB_KEEP_MUSIC},
            )
    except PipelineCancelled:
        logger.info("Pipeline cancelado para %s", video_path)
        raise
    except Exception as exc:
        logger.exception("Falha no pipeline para %s", video_path)
        raise PipelineError(str(exc), result) from exc

    on_progress("done", "Concluido", 100, "Processamento concluido.")
    logger.info("Pipeline concluido para %s", video_path)
    return result
