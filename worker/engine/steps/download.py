"""Etapa (1): download via yt-dlp a partir de uma URL do YouTube -- unica
forma de entrada do youcreate (nao ha mais upload de arquivo local).

Quando um recorte (start/duration) e pedido, usamos download_ranges do
yt-dlp para trazer so o trecho escolhido, sem baixar o video inteiro.
"""
import uuid
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp

_YOUTUBE_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com",
    "music.youtube.com", "youtu.be",
}


def _validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise RuntimeError("URL invalida: apenas links http/https sao aceitos.")
    if parsed.netloc.lower() not in _YOUTUBE_HOSTS:
        raise RuntimeError("Apenas links do YouTube sao aceitos.")


def probe(url: str) -> dict:
    """Consulta duracao/titulo do video sem baixar (usado para montar a barra de corte)."""
    _validate_url(url)
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        raise RuntimeError(f"Nao foi possivel ler esse link: {exc}") from exc
    return {"duration": info.get("duration") or 0, "title": info.get("title") or ""}


def download_video(
    url: str,
    out_dir: Path,
    start: float = 0.0,
    duration: float | None = None,
) -> Path:
    """Baixa o video da URL para out_dir; devolve o caminho do arquivo baixado.

    Se duration for informado, baixa apenas o trecho start..start+duration
    em vez do video inteiro.
    """
    _validate_url(url)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{uuid.uuid4().hex[:8]}_source"
    out_template = str(out_dir / f"{stem}.%(ext)s")

    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/best",
        "merge_output_format": "mp4",
        "outtmpl": out_template,
    }

    if duration:
        end = start + duration
        opts["download_ranges"] = lambda info, ydl, _s=start, _e=end: [
            {"start_time": _s, "end_time": _e}
        ]
        opts["force_keyframes_at_cuts"] = True

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except yt_dlp.utils.DownloadError as exc:
        raise RuntimeError(f"Falha ao baixar o video da URL informada: {exc}") from exc

    matches = sorted(out_dir.glob(f"{stem}.*"))
    if not matches:
        raise RuntimeError("Download concluido, mas nenhum arquivo de video foi encontrado.")
    return matches[0]
