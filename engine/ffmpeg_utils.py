"""Wrapper compartilhado para chamadas ao ffmpeg com erro claro em PT-BR."""
import shutil
import subprocess


def is_ffmpeg_available() -> bool:
    """Verifica se ffmpeg e ffprobe estao instalados e no PATH."""
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def run_ffmpeg(args: list[str]) -> None:
    """Executa ffmpeg (ou ffprobe) com os argumentos dados.

    args deve incluir o nome do binario (ex: ["ffmpeg", "-y", ...]).
    Levanta RuntimeError com o stderr do processo em caso de falha.
    """
    try:
        subprocess.run(args, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        cmd = " ".join(args)
        raise RuntimeError(
            f"Falha ao executar '{cmd}':\n{exc.stderr}"
        ) from exc
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Comando '{args[0]}' nao encontrado. O ffmpeg esta instalado e no PATH?"
        ) from exc


def probe_duration(path) -> float:
    """Devolve a duracao (em segundos) do arquivo de midia via ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "csv=p=0",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Falha ao obter duracao de '{path}':\n{exc.stderr}") from exc
    except FileNotFoundError as exc:
        raise RuntimeError(
            "Comando 'ffprobe' nao encontrado. O ffmpeg esta instalado e no PATH?"
        ) from exc
    return float(result.stdout.strip())
