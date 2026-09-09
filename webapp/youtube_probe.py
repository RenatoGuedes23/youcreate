"""Consulta duracao/titulo de uma URL do YouTube via yt-dlp, sem baixar nada.

Copia standalone do site: nao importa engine/ (que pertence so ao worker,
que e quem de fato baixa/processa o video). Duplica a validacao de dominio
usada pelo worker em engine/steps/download.py -- se a lista de hosts do
YouTube mudar la, precisa mudar aqui tambem.
"""
import os
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp

_YOUTUBE_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com",
    "music.youtube.com", "youtu.be",
}

# Cookies opcionais (Netscape cookies.txt) de uma sessao logada no YouTube --
# o YouTube as vezes exige confirmar "nao sou um robo" antes de servir
# metadados/video, e sem uma sessao logada nao ha como passar por isso.
# ATENCAO: contem cookies de sessao da conta Google (SID/SAPISID/etc.) --
# decisao explicita do operador manter versionado neste repo (nao esta no
# .gitignore); trate como credencial sensivel mesmo assim. Copia propria do
# worker, que tem a sua em engine/steps/download.py (nao compartilhado,
# mesma logica duplicada de proposito -- ver cabecalho de queue_client.py
# sobre esse padrao no projeto).
BASE_DIR = Path(__file__).resolve().parent
COOKIES_FILE = Path(os.environ.get("YOUTUBE_COOKIES_FILE") or (BASE_DIR / "cookies.txt"))


def _validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise RuntimeError("URL invalida: apenas links http/https sao aceitos.")
    if parsed.netloc.lower() not in _YOUTUBE_HOSTS:
        raise RuntimeError("Apenas links do YouTube sao aceitos.")


def probe(url: str) -> dict:
    """Consulta duracao/titulo do video sem baixar (usado para montar a barra de corte)."""
    _validate_url(url)
    opts = {
        "quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True,
        # player_client=android: o cliente android do YouTube historicamente
        # nao exige a verificacao "Sign in to confirm you're not a bot" que o
        # cliente web (padrao) exige, pra muitos videos publicos -- "web"
        # fica de fallback. sleep_interval_requests: pequeno atraso entre
        # requisicoes internas do proprio yt-dlp, reduz o padrao de trafego
        # que dispara heuristicas de bot. Copia da mesma mitigacao do worker
        # (engine/steps/download.py::_bot_check_opts) -- nao compartilhado,
        # mesmo padrao de duplicacao proposital do resto do projeto.
        "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
        "sleep_interval_requests": 1,
    }
    if COOKIES_FILE.exists():
        opts["cookiefile"] = str(COOKIES_FILE)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        raise RuntimeError(f"Nao foi possivel ler esse link: {exc}") from exc
    return {"duration": info.get("duration") or 0, "title": info.get("title") or ""}
