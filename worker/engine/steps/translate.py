"""Etapa (4): traduz os segmentos via o provider de traducao ativo."""
from engine import config
from engine.models import Segment
from engine.providers.translate_base import Translator


def _build_provider() -> Translator:
    if config.TRANSLATE_PROVIDER == "openrouter":
        from engine.providers.translate_openrouter import OpenRouterTranslator
        return OpenRouterTranslator()
    raise ValueError(f"TRANSLATE_PROVIDER desconhecido: {config.TRANSLATE_PROVIDER!r}")


def translate_segments(segments: list[Segment]) -> list[Segment]:
    """Traduz o texto de todos os segmentos em lote e preenche seg.translation.

    Passa a duracao de cada fala (seg.end - seg.start) pro provider, que usa
    isso pra sugerir um tamanho de traducao compativel com o tempo de fala
    original -- ver comentario em translate_openrouter.py sobre por que
    (reduz o descompasso entre a dublagem e o video que so a etapa de
    encaixe temporal, sozinha, nao resolve bem).
    """
    provider = _build_provider()
    texts = [seg.text for seg in segments]
    durations = [seg.end - seg.start for seg in segments]
    translations = provider.translate_batch(texts, durations)
    for seg, translation in zip(segments, translations):
        seg.translation = translation
    return segments
