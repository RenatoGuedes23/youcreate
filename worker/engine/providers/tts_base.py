"""Interface abstrata de provider de TTS (dublagem)."""
from typing import Protocol


class TTS(Protocol):
    def synthesize(
        self, text: str, voice: str, engine: str | None = None, rate_percent: int | None = None
    ) -> bytes: ...  # WAV/MP3 bytes
