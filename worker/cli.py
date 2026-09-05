"""Roda o motor pelo terminal, sem subir a web.

Uso:
  python cli.py https://www.youtube.com/watch?v=...
  python cli.py https://www.youtube.com/watch?v=... --start 30 --duration 60
"""
import argparse
import shutil
import sys
import uuid
from pathlib import Path

from engine import config
from engine.ffmpeg_utils import is_ffmpeg_available
from engine.pipeline import PipelineError, run
from logging_setup import setup_logging


def _print_progress(step_id: str, label: str, pct: int, message: str) -> None:
    print(f"[{pct:3d}%] {label}: {message}")


def main() -> int:
    setup_logging(Path("youcreate.log"))

    parser = argparse.ArgumentParser(description="youcreate - localizacao de video do YouTube para PT-BR")
    parser.add_argument("url", help="URL do video no YouTube")
    parser.add_argument("--no-dub", action="store_true", help="Nao gerar dublagem")
    parser.add_argument("--no-subs", action="store_true", help="Nao gerar legenda")
    parser.add_argument("--start", type=float, default=0.0, help="Inicio do recorte em segundos (opcional)")
    parser.add_argument("--duration", type=float, default=None, help="Duracao do recorte em segundos (opcional; padrao: video inteiro)")
    args = parser.parse_args()

    if not is_ffmpeg_available():
        print(
            "Erro: ffmpeg/ffprobe nao encontrados no PATH. "
            "Veja a secao de instalacao do ffmpeg no README.",
            file=sys.stderr,
        )
        return 1

    work_dir = config.WORK_DIR / f"cli_{uuid.uuid4().hex[:8]}"

    try:
        result = run(
            on_progress=_print_progress,
            make_subs=not args.no_subs,
            make_dub=not args.no_dub,
            work_dir=work_dir,
            source_url=args.url,
            url_clip_start=args.start,
            url_clip_duration=args.duration,
        )
    except PipelineError as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        if exc.partial_result.srt_path:
            print(f"(Legenda parcial ja gerada em: {exc.partial_result.srt_path})", file=sys.stderr)
        return 1
    finally:
        # O video baixado e temporario -- so os resultados finais em
        # OUTPUTS_DIR precisam sobreviver.
        shutil.rmtree(work_dir, ignore_errors=True)

    if result.srt_path:
        print(f"Legenda gerada em: {result.srt_path}")
    if result.video_out:
        print(f"Video final gerado em: {result.video_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
