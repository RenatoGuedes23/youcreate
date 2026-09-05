"""Etapa (6): dublagem - sintese de voz + encaixe temporal dos segmentos.

O problema central: a fala em PT-BR costuma ser mais longa que em EN, entao o
audio dublado pode "estourar" o tempo do trecho original. Cada clipe e
acelerado (ate DUB_MAX_SPEEDUP) ou preenchido com silencio para caber no seu
intervalo; se mesmo acelerado ao maximo ainda for mais longo, o clipe invade
levemente o proximo trecho (nao e cortado). Depois, cada clipe e posicionado
no seu tempo absoluto (adelay) e todos sao somados (amix) em uma unica trilha
do tamanho total da fala.
"""
import wave
from pathlib import Path

from engine import config
from engine.ffmpeg_utils import run_ffmpeg
from engine.models import Segment
from engine.providers.tts_base import TTS


def _build_tts_provider() -> TTS:
    if config.TTS_PROVIDER == "polly":
        from engine.providers.tts_polly import PollyTTS
        return PollyTTS()
    raise ValueError(f"TTS_PROVIDER desconhecido: {config.TTS_PROVIDER!r}")


def _wav_info(path: Path) -> tuple[float, int]:
    """Devolve (duracao_segundos, sample_rate) de um .wav PCM."""
    with wave.open(str(path), "rb") as wf:
        frames = wf.getnframes()
        rate = wf.getframerate()
        return (frames / rate if rate else 0.0, rate)


def _fit_segment_clip(raw_path: Path, target_dur: float, out_path: Path) -> None:
    """Ajusta o clipe TTS para caber em target_dur (acelera ou preenche com silencio)."""
    clip_dur, _rate = _wav_info(raw_path)

    if clip_dur > target_dur and clip_dur > 0:
        speedup = min(clip_dur / target_dur, config.DUB_MAX_SPEEDUP)
        run_ffmpeg([
            "ffmpeg", "-y",
            "-i", str(raw_path),
            "-filter:a", f"atempo={speedup:.4f}",
            str(out_path),
        ])
    elif clip_dur < target_dur:
        pad = target_dur - clip_dur
        run_ffmpeg([
            "ffmpeg", "-y",
            "-i", str(raw_path),
            "-af", f"apad=pad_dur={pad:.4f}",
            str(out_path),
        ])
    else:
        run_ffmpeg(["ffmpeg", "-y", "-i", str(raw_path), str(out_path)])


def _mix_track(
    fitted_paths: list[tuple[float, Path]],
    total_duration: float,
    sample_rate: int,
    out_path: Path,
) -> None:
    """Monta uma trilha silenciosa do tamanho total e soma cada clipe no seu tempo."""
    inputs = [
        "-f", "lavfi", "-t", f"{total_duration:.4f}",
        "-i", f"anullsrc=r={sample_rate}:cl=mono",
    ]
    filter_parts = []
    mix_labels = ["[0:a]"]

    for i, (start, path) in enumerate(fitted_paths):
        inputs += ["-i", str(path)]
        delay_ms = max(round(start * 1000), 0)
        label = f"d{i}"
        filter_parts.append(f"[{i + 1}:a]adelay={delay_ms}:all=1[{label}]")
        mix_labels.append(f"[{label}]")

    filter_parts.append(
        f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:duration=first:normalize=0[out]"
    )

    run_ffmpeg([
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", ";".join(filter_parts),
        "-map", "[out]",
        str(out_path),
    ])


def synthesize_dub(segments: list[Segment], work_dir: Path) -> Path:
    """Gera a trilha de dublagem completa, com cada fala encaixada no seu tempo."""
    if not segments:
        raise RuntimeError("Nenhum segmento para dublar.")

    work_dir.mkdir(parents=True, exist_ok=True)
    provider = _build_tts_provider()

    fitted_paths: list[tuple[float, Path]] = []
    sample_rate = 24000
    for i, seg in enumerate(segments):
        raw_path = work_dir / f"dub_raw_{i:04d}.wav"
        raw_path.write_bytes(provider.synthesize(seg.translation, config.DUB_VOICE))
        if i == 0:
            _, sample_rate = _wav_info(raw_path)

        target_dur = max(seg.end - seg.start, 0.05)
        fitted_path = work_dir / f"dub_fit_{i:04d}.wav"
        _fit_segment_clip(raw_path, target_dur, fitted_path)
        fitted_paths.append((seg.start, fitted_path))

    total_duration = max(seg.end for seg in segments)
    out_path = work_dir / "dub_track.wav"
    _mix_track(fitted_paths, total_duration, sample_rate, out_path)
    return out_path
