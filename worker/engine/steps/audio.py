"""Etapa (2): extracao de audio do video via ffmpeg."""
from pathlib import Path

from engine.ffmpeg_utils import run_ffmpeg


def extract_audio(video_path: Path, work_dir: Path) -> Path:
    """Extrai o audio de video_path como .wav 16kHz mono em work_dir."""
    work_dir.mkdir(parents=True, exist_ok=True)
    out_path = work_dir / f"{video_path.stem}.wav"
    run_ffmpeg(
        [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-vn",
            "-ac", "1",
            "-ar", "16000",
            "-acodec", "pcm_s16le",
            str(out_path),
        ]
    )
    return out_path
