"""Consulta duracao/titulo de uma URL do YouTube via yt-dlp, sem baixar nada.

Copia standalone do site: nao importa engine/ (que pertence so ao worker,
que e quem de fato baixa/processa o video). Duplica a validacao de dominio
usada pelo worker em engine/steps/download.py -- se a lista de hosts do
YouTube mudar la, precisa mudar aqui tambem.
"""
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
