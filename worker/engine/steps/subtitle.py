"""Etapa (5): geracao de legenda .srt a partir dos segmentos."""
from pathlib import Path

from engine.models import Segment


def _format_timestamp(seconds: float) -> str:
    """Converte segundos para o formato SRT HH:MM:SS,mmm."""
    total_ms = round(seconds * 1000)
    hours, rem_ms = divmod(total_ms, 3_600_000)
    minutes, rem_ms = divmod(rem_ms, 60_000)
    secs, ms = divmod(rem_ms, 1_000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def build_srt(segments: list[Segment], out_path: Path, translated: bool = True) -> Path:
    """Gera um arquivo .srt padrao a partir dos segmentos.

    Se translated=True usa seg.translation, senao usa seg.text (original).
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for i, seg in enumerate(segments, start=1):
        text = seg.translation if translated else seg.text
        lines.append(str(i))
        lines.append(f"{_format_timestamp(seg.start)} --> {_format_timestamp(seg.end)}")
        lines.append(text)
        lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def build_vtt(segments: list[Segment], out_path: Path, translated: bool = True) -> Path:
    """Gera um .vtt (WebVTT) a partir dos mesmos segmentos do .srt -- usado
    pela faixa <track> do player na tela de resultado, para nao depender so
    da legenda queimada no video. Mesma timestamp do SRT, so troca a virgula
    dos milissegundos por ponto (formato exigido pelo WebVTT) e adiciona o
    cabecalho "WEBVTT".
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["WEBVTT", ""]
    for seg in segments:
        text = seg.translation if translated else seg.text
        start = _format_timestamp(seg.start).replace(",", ".")
        end = _format_timestamp(seg.end).replace(",", ".")
        lines.append(f"{start} --> {end}")
        lines.append(text)
        lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path
