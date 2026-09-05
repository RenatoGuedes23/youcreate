"""Etapa (7): remontagem final - video original + audio dublado + legenda."""
from pathlib import Path

from engine.ffmpeg_utils import probe_duration, run_ffmpeg


def _escape_subtitles_path(path: Path) -> str:
    """Escapa o caminho para uso dentro do filtro subtitles= do ffmpeg."""
    return str(path).replace("\\", "\\\\").replace(":", "\\:")


def build_final(video: Path, srt: Path | None, dub_audio: Path, out_path: Path, opts: dict) -> Path:
    """Combina video original + audio dublado (+ legenda) em um unico mp4.

    - audio: substitui a voz original pela dublada, ou mixa com a original
      (ducking) se opts['keep_music'] for True.
    - legenda: queimada (hardsub) se opts['burn_subs'] for True, ou anexada
      como faixa soft (mov_text) caso contrario. Sem efeito se srt for None.
    """
    burn_subs = bool(opts.get("burn_subs", True)) and srt is not None
    keep_music = bool(opts.get("keep_music", False))
    soft_subs = srt is not None and not burn_subs

    out_path.parent.mkdir(parents=True, exist_ok=True)
    video_duration = probe_duration(video)

    inputs = ["-i", str(video), "-i", str(dub_audio)]
    if soft_subs:
        inputs += ["-i", str(srt)]

    filter_parts = []
    if keep_music:
        filter_parts.append("[0:a]volume=-18dB[orig_low]")
        filter_parts.append("[orig_low][1:a]amix=inputs=2:duration=first:normalize=0[amixed]")
    else:
        filter_parts.append("[1:a]anull[amixed]")
    filter_parts.append(f"[amixed]apad=whole_dur={video_duration:.4f}[aout]")

    if burn_subs:
        srt_escaped = _escape_subtitles_path(srt)
        filter_parts.append(f"[0:v]subtitles='{srt_escaped}'[vout]")
        maps = ["-map", "[vout]", "-map", "[aout]"]
        video_codec = ["-c:v", "libx264"]
    else:
        maps = ["-map", "0:v", "-map", "[aout]"]
        video_codec = ["-c:v", "copy"]

    cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(filter_parts), *maps]
    if soft_subs:
        cmd += ["-map", "2:s", "-c:s", "mov_text"]
    cmd += [*video_codec, "-c:a", "aac", str(out_path)]

    run_ffmpeg(cmd)
    return out_path
