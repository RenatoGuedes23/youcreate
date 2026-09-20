"""Etapa (7): remontagem final - video + audio dublado + legenda queimada.

Tudo num unico passe de ffmpeg (reenquadrar, legendar e mixar audio). Fazer
em passes separados recodificaria o video mais de uma vez: perda geracional
de qualidade e o dobro (ou triplo) do tempo de CPU.
"""
from pathlib import Path

from engine.ffmpeg_utils import probe_duration, run_ffmpeg
from engine.steps import reframe


def _escape_filter_path(path: Path) -> str:
    """Escapa o caminho para uso dentro dos filtros subtitles=/ass= do ffmpeg."""
    return str(path).replace("\\", "\\\\").replace(":", "\\:")


def build_final(video: Path, srt: Path | None, dub_audio: Path | None, out_path: Path, opts: dict) -> Path:
    """Combina video + audio (+ reenquadramento 9:16 + legenda) num mp4.

    dub_audio None significa "sem dublagem, mantem o audio original" -- o caso
    de um video que ja esta no idioma de destino, onde nao ha o que traduzir
    nem o que dublar, so reenquadrar e legendar.

    opts:
      - reframe_mode: "crop" | "blur" | "none" (ver engine/steps/reframe.py)
      - ass_path: legenda estilo Shorts (.ass). Tem prioridade sobre o .srt
        na hora de queimar -- o .srt continua sendo gerado e entregue como
        arquivo, mas quem vai na imagem e o ASS, que controla fonte e posicao.
      - burn_subs: queima a legenda na imagem; se False e houver .srt, ele
        entra como faixa soft (mov_text).
      - keep_music: mixa o audio original abaixado (-18 dB) sob a dublagem,
        preservando trilha e ambiencia, em vez de substituir o audio todo.
    """
    reframe_mode = opts.get("reframe_mode", reframe.MODE_CROP)
    ass_path = opts.get("ass_path")
    burn_enabled = bool(opts.get("burn_subs", True))
    keep_music = bool(opts.get("keep_music", False))
    crf = int(opts.get("crf", 20))
    preset = str(opts.get("preset", "medium"))

    burn_ass = burn_enabled and ass_path is not None
    burn_srt = burn_enabled and not burn_ass and srt is not None
    soft_subs = srt is not None and not burn_enabled

    out_path.parent.mkdir(parents=True, exist_ok=True)
    video_duration = probe_duration(video)

    # Os indices das entradas do ffmpeg ([0:v], [1:a]...) dependem de quais
    # entradas existem neste job -- sem dublagem nao ha segunda entrada, e a
    # legenda soft passaria a ser a 1 em vez da 2. Calculados aqui em vez de
    # escritos na mao pra nao apontarem pro stream errado.
    inputs = ["-i", str(video)]
    next_index = 1
    dub_index = None
    if dub_audio is not None:
        dub_index = next_index
        inputs += ["-i", str(dub_audio)]
        next_index += 1
    srt_index = None
    if soft_subs:
        srt_index = next_index
        inputs += ["-i", str(srt)]
        next_index += 1

    # --- video ---
    video_parts = reframe.build_filter(reframe_mode, "0:v", "vref")
    label = "vref" if video_parts else "0:v"
    if burn_ass:
        video_parts.append(f"[{label}]ass='{_escape_filter_path(ass_path)}'[vout]")
        label = "vout"
    elif burn_srt:
        video_parts.append(f"[{label}]subtitles='{_escape_filter_path(srt)}'[vout]")
        label = "vout"

    # --- audio ---
    audio_parts = []
    if dub_index is None:
        # Sem dublagem: o audio original passa intacto. keep_music nao se
        # aplica aqui -- nao ha nada sob o que abaixar a trilha.
        audio_parts.append("[0:a]anull[amixed]")
    elif keep_music:
        # Abaixa a faixa original e mixa sob a dublagem. Atencao: isso
        # preserva a trilha E a voz original baixinha (estilo voice-over) --
        # separar musica de voz exigiria um modelo de separacao de fontes.
        audio_parts.append("[0:a]volume=-18dB[orig_low]")
        audio_parts.append(f"[orig_low][{dub_index}:a]amix=inputs=2:duration=first:normalize=0[amixed]")
    else:
        audio_parts.append(f"[{dub_index}:a]anull[amixed]")
    # A dublagem pode terminar antes do video (ultima fala no meio do clipe);
    # sem apad o mp4 ficaria com audio mais curto que a imagem. Inofensivo
    # quando o audio e o original (ja tem a duracao do video).
    audio_parts.append(f"[amixed]apad=whole_dur={video_duration:.4f}[aout]")

    filter_parts = video_parts + audio_parts

    # Sempre recodifica: o reenquadramento 9:16 e obrigatorio, entao nunca
    # ha o caso de so copiar o stream de video.
    maps = ["-map", f"[{label}]", "-map", "[aout]"]
    video_codec = [
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        # yuv420p: perfil que todo player/plataforma aceita. Sem isso o
        # ffmpeg pode manter um formato (ex: yuv444p) que o YouTube e
        # players de celular recusam.
        "-pix_fmt", "yuv420p",
    ]

    cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(filter_parts), *maps]
    if soft_subs:
        cmd += ["-map", f"{srt_index}:s", "-c:s", "mov_text"]
    # faststart move o indice do mp4 pro inicio do arquivo: o video comeca a
    # tocar antes do download terminar (importa no player da tela de resultado).
    cmd += [*video_codec, "-c:a", "aac", "-movflags", "+faststart", str(out_path)]

    run_ffmpeg(cmd)
    return out_path
