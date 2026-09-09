"""Etapa opcional: diarizacao de locutor (quem fala quando) via pyannote.audio.

Roda sobre o mesmo .wav ja extraido para a transcricao -- nao baixa nada
novo, so mais uma passada sobre o mesmo arquivo. Usada so para dub.py poder
atribuir uma voz Polly diferente por locutor detectado; se estiver desligada
(DUB_ENABLE_DIARIZATION=false ou sem HF_TOKEN) ou falhar por qualquer motivo,
devolve lista vazia e o pipeline cai de volta no comportamento historico
(uma unica voz pra todo mundo, ver dub.py).

O modelo (pyannote/speaker-diarization-3.1) e fechado no Hugging Face --
precisa aceitar os termos de uso em huggingface.co/pyannote/speaker-
diarization-3.1 e .../segmentation-3.0, e gerar um token de leitura em
huggingface.co/settings/tokens (HF_TOKEN no .env).
"""
import logging
from pathlib import Path

from engine import config
from engine.models import Segment

logger = logging.getLogger(__name__)

_pipeline = None  # cache do modelo carregado, mesmo padrao de transcribe.py


class _LoggingHook:
    """Hook do pyannote (protocolo: step_name, step_artifact, file, total,
    completed) que so loga quando a etapa interna muda -- a chamada ao
    pipeline fica muda por minutos em audio longo sem isso, e logar a cada
    "completed" individual inundaria o log (segmentation roda em centenas de
    janelas deslizantes)."""

    def __init__(self):
        self._current: str | None = None

    def __call__(self, step_name, step_artifact, file=None, total=None, completed=None):
        if step_name != self._current:
            self._current = step_name
            logger.info("Diarizacao: etapa '%s'...", step_name)


def _get_pipeline():
    global _pipeline
    if _pipeline is None:
        from pyannote.audio import Pipeline
        _pipeline = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1",
            token=config.HF_TOKEN,
        )
    return _pipeline


def diarize(audio_path: Path) -> list[tuple[float, float, str]]:
    """Devolve uma lista de (start, end, speaker_label) em segundos, uma por
    trecho de fala continua de um locutor. Lista vazia se a diarizacao
    estiver desligada ou falhar -- nunca levanta excecao, e uma etapa
    best-effort que nao pode derrubar o pipeline inteiro."""
    if not config.DUB_ENABLE_DIARIZATION or not config.HF_TOKEN:
        return []

    try:
        import soundfile as sf
        import torch

        # Nao passamos o path direto pro pipeline: a decodificacao de audio
        # embutida do pyannote 4.x depende do torchcodec, que por sua vez
        # tenta carregar uma lib (libtorchcodec_image.so) ligada a CUDA
        # (libnvrtc.so.13) -- inexistente neste worker, que e CPU-only de
        # proposito. soundfile (sem nenhuma dependencia CUDA) le o .wav e
        # passamos como tensor bruto, contornando o torchcodec por completo.
        data, sample_rate = sf.read(str(audio_path))
        waveform = torch.tensor(data, dtype=torch.float32).unsqueeze(0)

        pipeline = _get_pipeline()
        logger.info(
            "Diarizacao: iniciando (audio de %.0fs, pode levar varios minutos em CPU)...",
            waveform.shape[-1] / sample_rate,
        )
        output = pipeline(
            {"waveform": waveform, "sample_rate": sample_rate}, hook=_LoggingHook()
        )
        logger.info("Diarizacao: concluida.")
    except Exception:
        logger.exception("Diarizacao falhou para %s -- seguindo com voz unica.", audio_path)
        return []

    # exclusive_speaker_diarization (nao speaker_diarization) resolve fala
    # sobreposta atribuindo um unico locutor por trecho -- mais facil de
    # mapear 1-pra-1 com os segmentos do Whisper, que tambem nao se sobrepoe.
    return [
        (turn.start, turn.end, speaker)
        for turn, _, speaker in output.exclusive_speaker_diarization.itertracks(yield_label=True)
    ]


def assign_speakers(segments: list[Segment], turns: list[tuple[float, float, str]]) -> int:
    """Preenche seg.speaker com o locutor de maior sobreposicao de tempo,
    para cada segmento. Devolve quantos locutores distintos foram
    atribuidos (0 se turns estiver vazio, ou se nenhum segmento sobrepor
    algum turno detectado)."""
    if not turns:
        return 0

    speakers_seen: set[str] = set()
    for seg in segments:
        best_overlap = 0.0
        best_speaker = ""
        for start, end, speaker in turns:
            overlap = min(seg.end, end) - max(seg.start, start)
            if overlap > best_overlap:
                best_overlap = overlap
                best_speaker = speaker
        seg.speaker = best_speaker
        if best_speaker:
            speakers_seen.add(best_speaker)
    return len(speakers_seen)
