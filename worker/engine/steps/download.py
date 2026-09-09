"""Etapa (1): download via yt-dlp a partir de uma URL do YouTube -- unica
forma de entrada do youcreate (nao ha mais upload de arquivo local).

Quando um recorte (start/duration) e pedido, usamos download_ranges do
yt-dlp para trazer so o trecho escolhido, sem baixar o video inteiro.
"""
import uuid
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp

from engine import config

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


def _cookie_opts() -> dict:
    """Cookies opcionais de uma sessao logada -- usadas quando o YouTube exige
    confirmar "nao sou um robo" antes de servir video/metadados."""
    path = Path(config.YOUTUBE_COOKIES_FILE)
    return {"cookiefile": str(path)} if path.exists() else {}


def _bot_check_opts() -> dict:
    """Mitigacoes extras contra o bloqueio "Sign in to confirm you're not a
    bot" do YouTube, complementares aos cookies (que sozinhos as vezes nao
    bastam, sobretudo de IP de datacenter/cloud):

    - player_client=android: o cliente android do YouTube historicamente
      nao exige a verificacao anti-bot que o cliente web (usado por
      padrao) exige, pra muitos videos publicos. "web" fica de fallback
      caso o android falhe por outro motivo.
    - sleep_interval_requests: pequeno atraso entre as requisicoes internas
      que o proprio yt-dlp faz (nao afeta o tempo de download em si) --
      reduz o padrao de trafego que dispara heuristicas de bot, relevante
      sobretudo com varias replicas do worker batendo no YouTube do mesmo IP.
    """
    return {
        "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
        "sleep_interval_requests": 1,
    }


def probe(url: str) -> dict:
    """Consulta duracao/titulo do video sem baixar (usado para montar a barra de corte)."""
    _validate_url(url)
    opts = {
        "quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True,
        **_cookie_opts(), **_bot_check_opts(),
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        raise RuntimeError(f"Nao foi possivel ler esse link: {exc}") from exc
    return {"duration": info.get("duration") or 0, "title": info.get("title") or ""}


def _format_string(max_height: int | None) -> str:
    """Formato do yt-dlp -- sem max_height, pega o melhor bv*/ba disponivel
    (comportamento historico). Com max_height, limita a altura do video em
    cada alternativa da cadeia de fallback, sem limitar o audio (a faixa de
    audio pesa pouco perto do video, nao vale a pena limitar)."""
    if not max_height:
        return "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/best"
    return (
        f"bv*[height<={max_height}][ext=mp4]+ba[ext=m4a]/"
        f"b[height<={max_height}][ext=mp4]/"
        f"bv*[height<={max_height}]+ba/"
        f"b[height<={max_height}]"
    )


def _range_opts(start: float, duration: float | None) -> dict:
    if not duration:
        return {}
    end = start + duration
    return {
        "download_ranges": lambda info, ydl, _s=start, _e=end: [{"start_time": _s, "end_time": _e}],
        "force_keyframes_at_cuts": True,
    }


def download_video(
    url: str,
    out_dir: Path,
    start: float = 0.0,
    duration: float | None = None,
    max_height: int | None = None,
) -> Path:
    """Baixa o video da URL para out_dir; devolve o caminho do arquivo baixado.

    Se duration for informado, baixa apenas o trecho start..start+duration
    em vez do video inteiro. Se max_height for informado (ex: 720), limita a
    altura do video baixado -- reduz tempo de download e, mais adiante, o
    tempo de render (menos pixels pra recodificar ao queimar legenda).
    """
    _validate_url(url)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{uuid.uuid4().hex[:8]}_source"
    out_template = str(out_dir / f"{stem}.%(ext)s")

    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "format": _format_string(max_height),
        "merge_output_format": "mp4",
        "outtmpl": out_template,
        **_cookie_opts(),
        **_bot_check_opts(),
        **_range_opts(start, duration),
    }

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except yt_dlp.utils.DownloadError as exc:
        raise RuntimeError(f"Falha ao baixar o video da URL informada: {exc}") from exc

    matches = sorted(out_dir.glob(f"{stem}.*"))
    if not matches:
        raise RuntimeError("Download concluido, mas nenhum arquivo de video foi encontrado.")
    return matches[0]


def download_audio(
    url: str,
    out_dir: Path,
    start: float = 0.0,
    duration: float | None = None,
) -> Path:
    """Baixa so a trilha de audio (sem video) -- usada em paralelo com
    download_video() para transcricao/traducao/dublagem nao precisarem
    esperar o download do video completo (bem mais pesado que o audio
    sozinho) para comecar. O stem tem um sufixo proprio (_audio, contra
    _source do video) para as duas buscas por glob no mesmo out_dir nao
    colidirem quando rodam ao mesmo tempo.
    """
    _validate_url(url)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{uuid.uuid4().hex[:8]}_audio"
    out_template = str(out_dir / f"{stem}.%(ext)s")

    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "format": "bestaudio/best",
        "outtmpl": out_template,
        **_cookie_opts(),
        **_bot_check_opts(),
        **_range_opts(start, duration),
    }

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except yt_dlp.utils.DownloadError as exc:
        raise RuntimeError(f"Falha ao baixar o audio da URL informada: {exc}") from exc

    matches = sorted(out_dir.glob(f"{stem}.*"))
    if not matches:
        raise RuntimeError("Download de audio concluido, mas nenhum arquivo foi encontrado.")
    return matches[0]
