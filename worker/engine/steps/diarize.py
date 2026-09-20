"""Etapa opcional: diarizacao de locutor (quem fala quando) via pyannote.audio.

Roda sobre o mesmo .wav ja extraido para a transcricao -- nao baixa nada
novo, so mais uma passada sobre o mesmo arquivo. Usada so para dub.py poder
atribuir uma voz diferente por locutor detectado; se estiver desligada
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


def diarize(audio_path: Path, speaker_count: int = 0) -> list[tuple[float, float, str]]:
    """Devolve uma lista de (start, end, speaker_label) em segundos, uma por
    trecho de fala continua de um locutor. Lista vazia se a diarizacao
    estiver desligada ou falhar -- nunca levanta excecao, e uma etapa
    best-effort que nao pode derrubar o pipeline inteiro.

    speaker_count > 0 e o numero de pessoas informado pelo operador na Tela
    2, repassado ao pyannote como TETO (max_speakers), nao como numero
    exato. A diferenca nao e cosmetica: num_speakers=N obriga o modelo a
    devolver N grupos mesmo que so exista uma pessoa falando, e ele entao
    parte a MESMA voz em N grupos artificiais (usando variacao de tom,
    respiracao, musica ao fundo). Como dub.py da uma voz diferente do pool
    a cada grupo, o mesmo narrador sairia dublado com N vozes trocando no
    meio do video -- inclusive trocando de genero. Com max_speakers o
    numero vira um limite superior: o modelo pode concluir que ha so um
    locutor, e o palpite errado do operador deixa de ser catastrofico.
    speaker_count == 1 nem chega aqui -- o pipeline pula a etapa inteira,
    ver pipeline.py."""
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
        kwargs = {"hook": _LoggingHook()}
        if speaker_count > 1:
            kwargs["min_speakers"] = 1
            kwargs["max_speakers"] = speaker_count
            logger.info("Diarizacao: procurando entre 1 e %d locutores.", speaker_count)
        output = pipeline(
            {"waveform": waveform, "sample_rate": sample_rate}, **kwargs
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


# Fracao minima das falas que um locutor precisa concentrar pra valer uma
# voz propria. Abaixo disso quase sempre e artefato -- uma respiracao, um
# trecho de musica, meia palavra que o modelo agrupou a parte. Dar uma voz
# diferente do pool a um caco desses troca a voz do video por dois segundos
# e soa como defeito, nao como segundo locutor.
_MIN_SPEAKER_SHARE = 0.10


def assign_speakers(segments: list[Segment], turns: list[tuple[float, float, str]]) -> int:
    """Preenche seg.speaker com o locutor de maior sobreposicao de tempo,
    para cada segmento. Devolve quantos locutores distintos foram
    atribuidos (0 se turns estiver vazio, ou se nenhum segmento sobrepor
    algum turno detectado).

    Locutores com participacao irrisoria (< _MIN_SPEAKER_SHARE das falas)
    sao absorvidos pelo locutor dominante antes de devolver: e melhor uma
    voz a menos que uma troca de voz que o espectador le como erro.
    """
    if not turns:
        return 0

    for seg in segments:
        best_overlap = 0.0
        best_speaker = ""
        for start, end, speaker in turns:
            overlap = min(seg.end, end) - max(seg.start, start)
            if overlap > best_overlap:
                best_overlap = overlap
                best_speaker = speaker
        seg.speaker = best_speaker

    atribuidos = [seg for seg in segments if seg.speaker]
    if not atribuidos:
        return 0

    contagem: dict[str, int] = {}
    for seg in atribuidos:
        contagem[seg.speaker] = contagem.get(seg.speaker, 0) + 1

    dominante = max(contagem, key=lambda k: contagem[k])
    # O piso de 2 importa em clipe curto: com 8 falas, 10% da 0.8 e nenhum
    # locutor ficaria abaixo disso -- um locutor de UMA fala passaria e
    # ganharia voz propria por dois segundos.
    minimo = max(2.0, len(atribuidos) * _MIN_SPEAKER_SHARE)
    residuais = {k for k, n in contagem.items() if n < minimo and k != dominante}
    if residuais:
        logger.info(
            "Diarizacao: %d locutor(es) com participacao irrisoria absorvidos "
            "pelo dominante (de %d para %d vozes).",
            len(residuais), len(contagem), len(contagem) - len(residuais),
        )
        for seg in atribuidos:
            if seg.speaker in residuais:
                seg.speaker = dominante

    return len({seg.speaker for seg in atribuidos})
