"""Modelos de request/response da API."""
from pydantic import BaseModel


class JobCreateRequest(BaseModel):
    url: str
    start: float = 0.0
    clip_duration: float | None = None


class JobCreated(BaseModel):
    id: str


class ProbeResult(BaseModel):
    duration: float
    title: str


class JobStatus(BaseModel):
    id: str
    status: str
    pct: int
    step: str
    message: str
    video: str = ""
    srt: str = ""
