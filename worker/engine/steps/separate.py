"""Etapa (5c): separacao de fontes -- tira a voz original da trilha.

Por que existe: manter a musica do video original e o que faz o short
"parecer o mesmo video", mas o caminho barato pra isso (abaixar a faixa
original inteira e mixar sob a dublagem -- o "ducking" do render) preserva
tambem a VOZ original baixinha, e na pratica isso soou mal. A unica forma
de ficar so com a musica e separar as fontes de verdade.

Usa o Demucs (modelo htdemucs, Meta) em modo de dois stems: ele devolve
`vocals` e `no_vocals`, e so o segundo interessa aqui.

Custo medido neste projeto: ~45s de CPU por minuto de audio com --jobs 4
em 12 nucleos. Mais jobs PIORA (12 jobs levou 114s pro mesmo trecho): cada
processo abre suas proprias threads de torch e elas competem entre si.

O modelo (~80MB) e baixado na primeira execucao para
/root/.cache/huggingface, que ja e volume persistente no docker-compose
(o mesmo do faster-whisper) -- entao so baixa uma vez.
"""
import logging
import subprocess
import sys
from pathlib import Path

from engine import config

logger = logging.getLogger(__name__)

_MODEL = "htdemucs"


def extract_music(audio_path: Path, work_dir: Path) -> Path | None:
    """Devolve a trilha sem a voz original, ou None se nao der pra separar.

    None e uma degradacao deliberada, nao um erro: sem a separacao o render
    usa so a dublagem (short sem musica de fundo). E pior que o ideal, mas
    melhor que a alternativa -- voltar ao ducking devolveria exatamente o
    problema de audio que motivou este modulo.
    """
    out_dir = work_dir / "separated"
    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, "-m", "demucs",
        "--two-stems=vocals",
        "-n", _MODEL,
        "--jobs", str(config.DUB_SEPARATION_JOBS),
        "-o", str(out_dir),
        str(audio_path),
    ]
    logger.info("Separando voz da musica (demucs %s)...", _MODEL)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.warning(
            "Separacao de audio falhou (codigo %d); o video sai so com a "
            "dublagem, sem a trilha original.\n%s",
            result.returncode, result.stderr[-1000:],
        )
        return None

    music = out_dir / _MODEL / audio_path.stem / "no_vocals.wav"
    if not music.exists():
        logger.warning(
            "Demucs terminou sem erro mas nao gerou %s; seguindo sem a "
            "trilha original.", music,
        )
        return None

    logger.info("Trilha sem voz pronta: %s", music.name)
    return music
