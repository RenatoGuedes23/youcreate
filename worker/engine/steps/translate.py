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
    """Traduz o texto de todos os segmentos em lote e preenche seg.translation."""
    provider = _build_provider()
    translations = provider.translate_batch([seg.text for seg in segments])
    for seg, translation in zip(segments, translations):
        seg.translation = translation
    return segments
