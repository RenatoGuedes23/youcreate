"""Interface abstrata de provider de traducao."""
from typing import Protocol


class Translator(Protocol):
    def translate_batch(self, texts: list[str]) -> list[str]: ...
