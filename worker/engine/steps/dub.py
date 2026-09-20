"""Etapa (6): dublagem - sintese de voz + encaixe temporal dos segmentos.

O problema central: a fala em PT-BR costuma ser mais longa que em EN, entao o
audio dublado pode "estourar" o tempo do trecho original. Cada clipe e
acelerado (ate DUB_MAX_SPEEDUP) ou preenchido com silencio para caber no seu
intervalo; se mesmo acelerado ao maximo ainda for mais longo, o clipe invade
levemente o proximo trecho (nao e cortado). Acelerar pede uma nova sintese
mais rapida ao TTS (SSML <prosody rate>) em vez de comprimir o audio pronto
com atempo -- o atempo ficou so pro residuo, porque reamostrar a fala
inteira era o que deixava a dublagem com som mecanico no caminho EN->PT. Depois, cada clipe e posicionado
no seu tempo absoluto (adelay) e todos sao somados (amix) em uma unica trilha
do tamanho total da fala.

Caso oposto (fala PT-BR bem mais curta que o slot): investigado com um caso
real (video da Dr. Jennifer Doudna) onde uma fala especifica do narrador
original foi dita mais devagar que o resto do video (enfase em "CRISPR-Cas9")
-- o Whisper mediu certo (7.5s pra 14 palavras em ingles), mas o TTS em
ritmo normal falou a traducao em ~4s, sobrando ~3.5s de silencio morto no
meio da dublagem. Preencher toda folga grande com silencio soa artificial;
em vez disso, _fit_segment_clip pede uma segunda sintese mais lenta (SSML
parametro speed do TTS) pra aproximar de como um dublador humano falaria
mais devagar ali, e so preenche com silencio o que sobrar depois disso --
ou a folga toda, quando o modelo nao honra speed (ver tts_openrouter.py:
o Gemini, hoje o padrao, ignora o parametro).
"""
import logging
import wave
from concurrent.futures import ThreadPoolExecutor
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
# chamada extra ao TTS.
_SLOWDOWN_TRIGGER_RATIO = 0.85
# Nao desacelera alem disso -- fala mais lenta que ~75% do ritmo normal
# comeca a soar arrastada/estranha, entao a partir dai e melhor sobrar um
# pouco de silencio do que forcar uma voz lenta demais.
_SLOWDOWN_MIN_SPEED = 0.75

# Folga minima entre o fim de uma fala e o inicio da proxima, pra duas linhas
# nao ficarem coladas quando a anterior estourou o slot.
_MIN_GAP = 0.06


def _build_tts_provider() -> TTS:
    if config.TTS_PROVIDER == "openrouter":
        from engine.providers.tts_openrouter import OpenRouterTTS
        return OpenRouterTTS()
    raise ValueError(f"TTS_PROVIDER desconhecido: {config.TTS_PROVIDER!r}")


def _voice_pool() -> list[tuple[str, str]]:
    """Vozes na ordem de atribuicao, como (modelo, voz).

    A primeira e sempre DUB_MODEL/DUB_VOICE -- e a unica usada quando a
    diarizacao esta desligada ou nao detecta mais de um locutor. As demais
    vem de DUB_VOICE_POOL e podem ser de modelos diferentes entre si, o que
    e o normal aqui: as vozes que passaram no teste de escuta estao
    espalhadas por tres modelos do OpenRouter.
    """
    pool: list[tuple[str, str]] = [(config.DUB_MODEL, config.DUB_VOICE)]
    for item in config.DUB_VOICE_POOL.split(","):
        item = item.strip()
        if not item:
            continue
        model, _, voice = item.partition("|")
        model, voice = model.strip(), voice.strip()
        if not voice:
            logger.warning(
                "Entrada invalida em DUB_VOICE_POOL: %r (esperado 'modelo|voz').", item,
            )
            continue
        if (model, voice) not in pool:
            pool.append((model, voice))
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
    resynthesize_slower: Callable[[float], bytes] | None = None,
    resynthesize_fast: Callable[[float], bytes] | None = None,
) -> None:
    """Ajusta o clipe TTS para caber em target_dur (acelera, desacelera ou preenche com silencio)."""
    clip_dur, _rate = _wav_info(raw_path)

    if clip_dur > target_dur and clip_dur > 0:
        natural_dur = clip_dur
        speedup = min(clip_dur / target_dur, config.DUB_MAX_SPEEDUP)

        # Primeiro tenta pedir a fala mais rapida ao proprio TTS: o modelo
        # reajusta a prosodia (pausas, enfases) como um locutor falando mais
        # rapido, enquanto o atempo so comprime o audio ja pronto e e o que
        # soava mecanico. Vale a pena porque no caminho EN->PT quase toda
        # fala cai aqui -- o PT-BR e 15-30% mais longo que o ingles. So
        # acontece com modelo que honra speed; ver synthesize_dub.
        if resynthesize_fast is not None:
            try:
                fast_path = raw_path.with_name(raw_path.stem + "_fast.wav")
                fast_path.write_bytes(resynthesize_fast(speedup))
                fast_dur, _ = _wav_info(fast_path)
                if fast_dur > 0:
                    raw_path, clip_dur = fast_path, fast_dur
            except Exception:
                logger.warning(
                    "Falha ao gerar versao mais rapida de %s (speed=%.2f) -- "
                    "seguindo com atempo.",
                    raw_path, speedup, exc_info=True,
                )

        # O TTS nao acerta o alvo na mosca, entao o atempo entra so no
        # residuo. O limite aqui e a duracao MINIMA permitida pela fala
        # original (natural / DUB_MAX_SPEEDUP), nao o slot: senao o teto
        # seria aplicado duas vezes -- 1.3x na ressintese e 1.3x de novo
        # aqui, compondo ~1.7x e justamente o som comprimido que o teto
        # existe pra evitar. Quando o slot e mais curto que esse minimo, a
        # fala invade levemente o proximo trecho, como sempre foi.
        floor_dur = max(natural_dur / config.DUB_MAX_SPEEDUP, 0.01)
        final_target = max(target_dur, floor_dur)
        if clip_dur > final_target:
            residual = clip_dur / final_target
            if residual > 1.001:
                run_ffmpeg([
                    "ffmpeg", "-y",
                    "-i", str(raw_path),
                    "-filter:a", f"atempo={residual:.4f}",
                    str(out_path),
                ])
                return
        run_ffmpeg(["ffmpeg", "-y", "-i", str(raw_path), str(out_path)])
        return

    if clip_dur < target_dur:
        ratio = clip_dur / target_dur if target_dur > 0 else 1.0
        if resynthesize_slower is not None and ratio < _SLOWDOWN_TRIGGER_RATIO:
            speed = max(ratio, _SLOWDOWN_MIN_SPEED)
            try:
                slow_path = raw_path.with_name(raw_path.stem + "_slow.wav")
                slow_path.write_bytes(resynthesize_slower(speed))
                slow_dur, _ = _wav_info(slow_path)
                raw_path, clip_dur = slow_path, slow_dur
            except Exception:
                logger.warning(
                    "Falha ao gerar versao mais lenta de %s (speed=%.2f) -- "
                    "seguindo com preenchimento por silencio.",
                    raw_path, speed, exc_info=True,
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


def _stagger_starts(
    fitted_paths: list[tuple[float, Path]],
) -> list[tuple[float, Path]]:
    """Adia o inicio de uma fala que comecaria antes da anterior terminar.

    Sem isto, uma fala que estourou o slot toca POR CIMA da seguinte (o
    _mix_track soma tudo), o que soa como duas pessoas falando juntas. E o
    caso comum desde que o TTS padrao passou a ser um modelo que nao aceita
    o parametro speed: o clipe so pode ser encurtado ate DUB_MAX_SPEEDUP e
    frequentemente ainda sobra.

    O atraso acumulado se resolve sozinho na primeira pausa do video --
    como cada fala e posicionada em max(inicio_previsto, fim_da_anterior),
    um intervalo de silencio do narrador original absorve o atraso e a
    dublagem volta a casar com a imagem. DUB_MAX_DRIFT limita quanto uma
    fala pode atrasar antes de preferirmos a sobreposicao: passar disso
    tiraria a dublagem de sincronia com o video de forma perceptivel, o que
    e pior que duas linhas se tocarem de leve.
    """
    adjusted: list[tuple[float, Path]] = []
    cursor = 0.0
    atrasos: list[float] = []

    for start, path in fitted_paths:
        novo = max(start, cursor)
        if novo - start > config.DUB_MAX_DRIFT:
            novo = start + config.DUB_MAX_DRIFT
        if novo > start:
            atrasos.append(novo - start)
        adjusted.append((novo, path))
        cursor = novo + _wav_info(path)[0] + _MIN_GAP

    if atrasos:
        logger.info(
            "Dublagem: %d de %d falas adiadas pra nao sobrepor a anterior "
            "(atraso maximo %.2fs, teto %.2fs).",
            len(atrasos), len(fitted_paths), max(atrasos), config.DUB_MAX_DRIFT,
        )
    return adjusted


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
    # pool -- DUB_MODEL/DUB_VOICE.
    pool = _voice_pool()
    speaker_voice: dict[str, tuple[str, str]] = {}

    def _voice_for(seg: Segment) -> tuple[str, str]:
        if not seg.speaker:
            return pool[0]
        if seg.speaker not in speaker_voice:
            speaker_voice[seg.speaker] = pool[len(speaker_voice) % len(pool)]
        return speaker_voice[seg.speaker]

    # A atribuicao de voz roda ANTES e em sequencia, de proposito: cada
    # locutor novo pega a proxima voz do pool na ordem em que aparece no
    # video, e isso depende da ordem dos segmentos. Feita dentro das
    # threads, a ordem viraria corrida e o mesmo video daria vozes
    # diferentes a cada execucao.
    voices = [_voice_for(seg) for seg in segments]

    def _make_clip(i: int) -> Path:
        seg = segments[i]
        model, voice = voices[i]
        raw_path = work_dir / f"dub_raw_{i:04d}.wav"
        raw_path.write_bytes(provider.synthesize(seg.translation, voice, model))

        target_dur = max(seg.end - seg.start, 0.05)
        fitted_path = work_dir / f"dub_fit_{i:04d}.wav"

        # So vale pedir outra velocidade ao modelo se ele honrar o
        # parametro. Pra quem ignora (o Gemini, medido), passar None faz
        # _fit_segment_clip cair direto no atempo em vez de gastar uma
        # chamada e receber de volta um audio do mesmo tamanho.
        if provider.supports_speed(model):
            def _resynthesize(speed: float, _text=seg.translation,
                              _voice=voice, _model=model) -> bytes:
                return provider.synthesize(_text, _voice, _model, speed=speed)
        else:
            _resynthesize = None

        _fit_segment_clip(
            raw_path, target_dur, fitted_path,
            resynthesize_slower=_resynthesize,
            resynthesize_fast=_resynthesize,  # mesma chamada; speed>1 acelera
        )
        return fitted_path

    # Uma fala nao depende do audio de nenhuma outra, e ~96% do tempo desta
    # etapa era espera de rede (medido): as chamadas rodam concorrentes.
    # Nao muda nada do que e enviado nem do que volta -- o custo e por
    # caractere sintetizado, nao por chamada, e o audio e identico. O teto
    # existe pra nao disparar o limite de taxa do provedor.
    # executor.map preserva a ordem de entrada, entao fitted[i] continua
    # sendo o clipe do segmento i.
    workers = max(1, min(config.DUB_TTS_CONCURRENCY, len(segments)))
    logger.info(
        "Dublagem: sintetizando %d falas com ate %d chamadas simultaneas.",
        len(segments), workers,
    )
    with ThreadPoolExecutor(max_workers=workers) as executor:
        fitted = list(executor.map(_make_clip, range(len(segments))))

    fitted_paths: list[tuple[float, Path]] = [
        (seg.start, path) for seg, path in zip(segments, fitted)
    ]
    # A taxa de amostragem sai do primeiro clipe pronto (o provider
    # normaliza todos pra mesma taxa; ver tts_openrouter._SAMPLE_RATE).
    _, sample_rate = _wav_info(fitted[0])

    fitted_paths = _stagger_starts(fitted_paths)

    # A legenda tem que seguir a VOZ, nao o audio original: os tempos do
    # Whisper cronometram a fala em ingles, e a dublagem foi encaixada
    # (acelerada/adiada) por cima disso. Sem reescrever aqui, a legenda
    # aparece antes da voz correspondente -- observado em video real. Cada
    # segmento passa a valer o intervalo em que a fala dublada realmente
    # toca, que e o que captions.build_ass e subtitle.build_srt usam.
    for seg, (novo_start, path) in zip(segments, fitted_paths):
        seg.start = novo_start
        seg.end = novo_start + _wav_info(path)[0]

    # A trilha tem que caber a ULTIMA fala inteira, nao so ir ate o fim do
    # ultimo segmento: quando o TTS nao aceita speed (o Gemini, padrao hoje)
    # o clipe so pode ser encurtado ate DUB_MAX_SPEEDUP e costuma passar do
    # slot. Como _mix_track usa amix duration=first, dimensionar pelo
    # max(seg.end) cortava essa sobra no meio da palavra. O render depois
    # ajusta a duracao ao video.
    total_duration = max(seg.end for seg in segments)
    for start, path in fitted_paths:
        total_duration = max(total_duration, start + _wav_info(path)[0])
    out_path = work_dir / "dub_track.wav"
    _mix_track(fitted_paths, total_duration, sample_rate, out_path)
    return out_path
