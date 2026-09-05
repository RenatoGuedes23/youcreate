"""Modelos de request/response da API."""
from pydantic import BaseModel


class JobCreated(BaseModel):
    id: str


class JobStatus(BaseModel):
    id: str
    status: str
    pct: int
    step: str
    message: str
    video: str = ""
    srt: str = ""
