"""Interface abstrata de provider de TTS (dublagem)."""
from typing import Protocol


class TTS(Protocol):
    def synthesize(self, text: str, voice: str) -> bytes: ...  # WAV/MP3 bytes
