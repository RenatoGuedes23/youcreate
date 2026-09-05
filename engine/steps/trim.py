"""Etapa opcional: recorte de um trecho do video antes do processamento.

Usada quando o operador envia um video maior que a janela suportada e escolhe
qual trecho quer localizar (ver web/index.html - barra de corte).
"""
from pathlib import Path

from engine.ffmpeg_utils import run_ffmpeg


def trim_video(video_path: Path, start: float, duration: float, out_path: Path) -> Path:
    """Recorta duration segundos a partir de start (ambos em segundos)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg([
        "ffmpeg", "-y",
        "-ss", f"{max(start, 0):.3f}",
        "-i", str(video_path),
        "-t", f"{max(duration, 0.1):.3f}",
        "-c", "copy",
        str(out_path),
    ])
    return out_path
