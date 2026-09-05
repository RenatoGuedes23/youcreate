"""Roda o motor pelo terminal, sem subir a web.

Uso: python cli.py caminho/do/video.mp4 [--no-dub] [--no-subs]
"""
import argparse
import sys
from pathlib import Path

from engine.ffmpeg_utils import is_ffmpeg_available
from engine.pipeline import PipelineError, run
from logging_config import setup_logging


def _print_progress(step_id: str, label: str, pct: int, message: str) -> None:
    print(f"[{pct:3d}%] {label}: {message}")


def main() -> int:
    setup_logging(Path("youcreate.log"))

    parser = argparse.ArgumentParser(description="youcreate - localizacao de video para PT-BR")
    parser.add_argument("video", type=Path, help="Caminho do arquivo .mp4 de entrada")
    parser.add_argument("--no-dub", action="store_true", help="Nao gerar dublagem")
    parser.add_argument("--no-subs", action="store_true", help="Nao gerar legenda")
    args = parser.parse_args()

    if not args.video.exists():
        print(f"Erro: arquivo nao encontrado: {args.video}", file=sys.stderr)
        return 1

    if not is_ffmpeg_available():
        print(
            "Erro: ffmpeg/ffprobe nao encontrados no PATH. "
            "Veja a secao de instalacao do ffmpeg no README.",
            file=sys.stderr,
        )
        return 1

    try:
        result = run(
            args.video,
            on_progress=_print_progress,
            make_subs=not args.no_subs,
            make_dub=not args.no_dub,
        )
    except PipelineError as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        if exc.partial_result.srt_path:
            print(f"(Legenda parcial ja gerada em: {exc.partial_result.srt_path})", file=sys.stderr)
        return 1

    if result.srt_path:
        print(f"Legenda gerada em: {result.srt_path}")
    if result.video_out:
        print(f"Video final gerado em: {result.video_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
