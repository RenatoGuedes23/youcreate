"""Modelos de request/response da API."""
from pydantic import BaseModel


class JobCreateRequest(BaseModel):
    url: str
    start: float = 0.0
    clip_duration: float | None = None
    source_lang: str = "en"
    target_lang: str = "pt"
    include_subtitles: bool = True
    video_title: str = ""
    video_duration: float = 0.0
    video_quality: str = ""  # "" = melhor disponivel; ou "480"/"720"/"1080"


class JobCreated(BaseModel):
    id: str


class ProbeResult(BaseModel):
    duration: float
    title: str


class LanguageOption(BaseModel):
    code: str
    name: str
    enabled_as_target: bool


class JobStatus(BaseModel):
    id: str
    status: str
    pct: int
    step: str
    message: str
    error_code: str = ""
    created_at: float = 0.0
    source_url: str = ""
    source_lang: str = ""
    video_title: str = ""
    video_duration: float = 0.0
    target_lang: str = ""
    include_subtitles: bool = True
    video_quality: str = ""
    video_name: str = ""
    srt_name: str = ""
    video_url: str = ""
    srt_url: str = ""
    vtt_url: str = ""
