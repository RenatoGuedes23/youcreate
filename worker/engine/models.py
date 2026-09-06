"""Dataclasses do motor: segmentos de fala e resultado do pipeline."""
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Segment:
    start: float          # segundos
    end: float            # segundos
    text: str             # texto original (EN)
    translation: str = "" # PT-BR (preenchido na etapa de traducao)


@dataclass
class PipelineResult:
    segments: list[Segment] = field(default_factory=list)
    srt_path: Path | None = None       # legenda PT-BR
    vtt_path: Path | None = None       # mesma legenda em WebVTT (faixa <track> do player)
    dub_audio_path: Path | None = None # trilha dublada
    video_out: Path | None = None      # mp4 final (legenda + dublagem)
