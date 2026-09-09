"""Etapa (3): transcricao local via faster-whisper, com timestamps."""
import logging
from pathlib import Path

from faster_whisper import WhisperModel

from engine import config
from engine.models import Segment

logger = logging.getLogger(__name__)

_model: WhisperModel | None = None


def _get_model() -> WhisperModel:
    global _model
    if _model is None:
        _model = WhisperModel(
            config.WHISPER_MODEL,
            device=config.WHISPER_DEVICE,
            compute_type=config.WHISPER_COMPUTE_TYPE,
        )
    return _model


def transcribe(audio_path: Path, language: str | None = None) -> list[Segment]:
    """Transcreve audio_path e devolve segmentos com timestamps (idioma origem).

    language segue o codigo ISO 639-1 esperado pelo Whisper (ex: "en", "pt").
    None deixa o Whisper autodetectar o idioma falado.
    """
    model = _get_model()
    segments_iter, info = model.transcribe(
        str(audio_path),
        language=language,
        vad_filter=True,
        # beam_size=5 (default da lib) faz busca em feixe -- mais preciso,
        # bem mais lento em CPU. Audio falado claro (sem sobreposicao/ruido
        # pesado) perde pouca precisao com busca gulosa (1) e acelera
        # bastante a etapa mais demorada do pipeline.
        beam_size=1,
    )
    logger.info("Transcricao: iniciando (audio de %.0fs).", info.duration)

    # model.transcribe() devolve um generator lazy -- sem este log, a etapa
    # inteira fica muda por minutos em audio longo (nenhuma lib usada aqui
    # loga por segmento sozinha). Loga a cada 10% avancado no tempo do audio,
    # nao a cada segmento, pra nao inundar o log em audios com fala densa.
    segments: list[Segment] = []
    last_logged_pct = -1
    for seg in segments_iter:
        segments.append(Segment(start=seg.start, end=seg.end, text=seg.text.strip()))
        if info.duration:
            pct = int(seg.end / info.duration * 100)
            if pct >= last_logged_pct + 10:
                logger.info("Transcricao: %d%% (%.0fs/%.0fs)", pct, seg.end, info.duration)
                last_logged_pct = pct

    if not segments:
        raise RuntimeError(
            "Nenhuma fala detectada no audio. Verifique se o video contem voz "
            "audivel no idioma selecionado."
        )
    logger.info("Transcricao concluida: %d segmentos.", len(segments))
    return segments
