"""Interface abstrata de provider de TTS (dublagem)."""
from typing import Protocol


class TTS(Protocol):
    def synthesize(
        self,
        text: str,
        voice: str,
        model: str | None = None,
        speed: float | None = None,
    ) -> bytes: ...  # bytes de um .wav mono

    def supports_speed(self, model: str | None = None) -> bool:
        """Se False, dub.py encaixa a fala no tempo com atempo em vez de
        pedir a fala mais rapida/lenta ao proprio modelo. Nem todo modelo de
        TTS honra o parametro 'speed' (ver tts_openrouter.py)."""
        ...
