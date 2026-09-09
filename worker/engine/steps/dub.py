"""Etapa (6): dublagem - sintese de voz + encaixe temporal dos segmentos.

O problema central: a fala em PT-BR costuma ser mais longa que em EN, entao o
audio dublado pode "estourar" o tempo do trecho original. Cada clipe e
acelerado (ate DUB_MAX_SPEEDUP) ou preenchido com silencio para caber no seu
intervalo; se mesmo acelerado ao maximo ainda for mais longo, o clipe invade
levemente o proximo trecho (nao e cortado). Depois, cada clipe e posicionado
no seu tempo absoluto (adelay) e todos sao somados (amix) em uma unica trilha
do tamanho total da fala.

Caso oposto (fala PT-BR bem mais curta que o slot): investigado com um caso
real (video da Dr. Jennifer Doudna) onde uma fala especifica do narrador
original foi dita mais devagar que o resto do video (enfase em "CRISPR-Cas9")
-- o Whisper mediu certo (7.5s pra 14 palavras em ingles), mas o TTS em
ritmo normal falou a traducao em ~4s, sobrando ~3.5s de silencio morto no
meio da dublagem. Preencher toda folga grande com silencio soa artificial;
em vez disso, _fit_segment_clip pede uma segunda sintese mais lenta (SSML
<prosody rate>, ver tts_polly.py) pra aproximar de como um dublador humano
falaria mais devagar ali, e so preenche com silencio o que sobrar depois
disso (ou a folga toda, se a voz nao aceitar SSML -- fallback silencioso).
"""
import logging
import wave
from pathlib import Path
from typing import Callable

from engine import config
from engine.ffmpeg_utils import run_ffmpeg
from engine.models import Segment
from engine.providers.tts_base import TTS

logger = logging.getLogger(__name__)

# So vale a pena tentar desacelerar quando a folga e grande o suficiente pra
# ser perceptivel -- folgas pequenas (a pausa natural entre falas) ja sao
# bem cobertas pelo preenchimento de silencio de sempre, sem gastar uma
# chamada extra ao Polly.
_SLOWDOWN_TRIGGER_RATIO = 0.85
# Nao desacelera alem disso -- fala mais lenta que ~75% do ritmo normal
# comeca a soar arrastada/estranha, entao a partir dai e melhor sobrar um
# pouco de silencio do que forcar uma voz lenta demais.
_SLOWDOWN_MIN_RATE_PERCENT = 75


def _build_tts_provider() -> TTS:
    if config.TTS_PROVIDER == "polly":
        from engine.providers.tts_polly import PollyTTS
        return PollyTTS()
    raise ValueError(f"TTS_PROVIDER desconhecido: {config.TTS_PROVIDER!r}")


# Pool de vozes PT-BR pra multi-locutor -- restrito por escolha do operador
# a essas tres (Thiago/neural, Camila/generative, Vitoria/neural -- as que
# soaram naturais nos testes de audio; Ricardo ficou de fora por so suportar
# o engine "standard", mais robotico). Engine fixo por voz aqui, nao usa
# config.POLLY_ENGINE pras vozes extras. DUB_VOICE/POLLY_ENGINE do .env
# sempre e a primeira do pool, pra manter o comportamento de hoje (uma voz
# so) quando a diarizacao nao detecta ou nao esta configurada.
_EXTRA_VOICES = [
    ("Thiago", "neural"),
    ("Camila", "generative"),
    ("Vitoria", "neural"),
]


def _voice_pool() -> list[tuple[str, str]]:
    pool = [(config.DUB_VOICE, config.POLLY_ENGINE)]
    for voice, engine in _EXTRA_VOICES:
        if voice != config.DUB_VOICE:
            pool.append((voice, engine))
    return pool


def _wav_info(path: Path) -> tuple[float, int]:
    """Devolve (duracao_segundos, sample_rate) de um .wav PCM."""
    with wave.open(str(path), "rb") as wf:
        frames = wf.getnframes()
        rate = wf.getframerate()
        return (frames / rate if rate else 0.0, rate)


def _fit_segment_clip(
    raw_path: Path,
    target_dur: float,
    out_path: Path,
    resynthesize_slower: Callable[[int], bytes] | None = None,
) -> None:
    """Ajusta o clipe TTS para caber em target_dur (acelera, desacelera ou preenche com silencio)."""
    clip_dur, _rate = _wav_info(raw_path)

    if clip_dur > target_dur and clip_dur > 0:
        speedup = min(clip_dur / target_dur, config.DUB_MAX_SPEEDUP)
        run_ffmpeg([
            "ffmpeg", "-y",
            "-i", str(raw_path),
            "-filter:a", f"atempo={speedup:.4f}",
            str(out_path),
        ])
        return

    if clip_dur < target_dur:
        ratio = clip_dur / target_dur if target_dur > 0 else 1.0
        if resynthesize_slower is not None and ratio < _SLOWDOWN_TRIGGER_RATIO:
            rate_percent = max(round(ratio * 100), _SLOWDOWN_MIN_RATE_PERCENT)
            try:
                slow_path = raw_path.with_name(raw_path.stem + "_slow.wav")
                slow_path.write_bytes(resynthesize_slower(rate_percent))
                slow_dur, _ = _wav_info(slow_path)
                raw_path, clip_dur = slow_path, slow_dur
            except Exception:
                logger.warning(
                    "Falha ao gerar versao mais lenta de %s (rate=%d%%) -- "
                    "seguindo com preenchimento por silencio.",
                    raw_path, rate_percent, exc_info=True,
                )

        if clip_dur < target_dur:
            pad = target_dur - clip_dur
            run_ffmpeg([
                "ffmpeg", "-y",
                "-i", str(raw_path),
                "-af", f"apad=pad_dur={pad:.4f}",
                str(out_path),
            ])
        else:
            run_ffmpeg(["ffmpeg", "-y", "-i", str(raw_path), str(out_path)])
        return

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

    # Cada locutor distinto (seg.speaker, preenchido pela diarizacao em
    # diarize.py) recebe uma voz diferente do pool, na ordem em que aparece
    # no video. Segmentos sem locutor atribuido (seg.speaker == "", inclusive
    # o caso comum de diarizacao desligada) sempre caem no primeiro item do
    # pool -- a voz configurada em DUB_VOICE.
    pool = _voice_pool()
    speaker_voice: dict[str, tuple[str, str]] = {}

    def _voice_for(seg: Segment) -> tuple[str, str]:
        if not seg.speaker:
            return pool[0]
        if seg.speaker not in speaker_voice:
            speaker_voice[seg.speaker] = pool[len(speaker_voice) % len(pool)]
        return speaker_voice[seg.speaker]

    fitted_paths: list[tuple[float, Path]] = []
    sample_rate = 24000
    for i, seg in enumerate(segments):
        voice, engine = _voice_for(seg)
        raw_path = work_dir / f"dub_raw_{i:04d}.wav"
        raw_path.write_bytes(provider.synthesize(seg.translation, voice, engine))
        if i == 0:
            _, sample_rate = _wav_info(raw_path)

        target_dur = max(seg.end - seg.start, 0.05)
        fitted_path = work_dir / f"dub_fit_{i:04d}.wav"

        def _resynthesize_slower(
            rate_percent: int, _text=seg.translation, _voice=voice, _engine=engine
        ) -> bytes:
            return provider.synthesize(_text, _voice, _engine, rate_percent=rate_percent)

        _fit_segment_clip(raw_path, target_dur, fitted_path, resynthesize_slower=_resynthesize_slower)
        fitted_paths.append((seg.start, fitted_path))

    total_duration = max(seg.end for seg in segments)
    out_path = work_dir / "dub_track.wav"
    _mix_track(fitted_paths, total_duration, sample_rate, out_path)
    return out_path
