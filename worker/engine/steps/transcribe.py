"""Etapa (3): transcricao local via faster-whisper, com timestamps."""
from pathlib import Path

from faster_whisper import WhisperModel

from engine import config
from engine.models import Segment

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
    segments_iter, _info = model.transcribe(
        str(audio_path),
        language=language,
        vad_filter=True,
    )
    segments = [
        Segment(start=seg.start, end=seg.end, text=seg.text.strip())
        for seg in segments_iter
    ]
    if not segments:
        raise RuntimeError(
            "Nenhuma fala detectada no audio. Verifique se o video contem voz "
            "audivel no idioma selecionado."
        )
    return segments
