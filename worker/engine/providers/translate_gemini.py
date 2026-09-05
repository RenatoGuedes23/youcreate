"""Provider de traducao via Gemini (google-genai)."""
import json

from google import genai
from google.genai import types

from engine import config


class GeminiTranslator:
    def __init__(self):
        if not config.GEMINI_API_KEY:
            raise RuntimeError(
                "GEMINI_API_KEY nao configurada. Defina a variavel de ambiente "
                "com sua chave da API Gemini."
            )
        self._client = genai.Client(api_key=config.GEMINI_API_KEY)

    def translate_batch(self, texts: list[str]) -> list[str]:
        """Traduz todas as falas em uma unica chamada, preservando a ordem."""
        if not texts:
            return []

        numbered = "\n".join(f"{i}: {t}" for i, t in enumerate(texts))
        prompt = (
            f"{config.TRANSLATE_STYLE}\n\n"
            f"Traduza cada linha numerada abaixo para {config.TARGET_LANG}. "
            f"Devolva um array JSON de strings na mesma ordem e com o mesmo "
            f"numero de itens ({len(texts)}), sem os numeros, contendo apenas "
            f"a traducao de cada linha.\n\n{numbered}"
        )

        response = self._client.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=list[str],
            ),
        )

        translations = json.loads(response.text)
        if not isinstance(translations, list) or len(translations) != len(texts):
            raise RuntimeError(
                "Resposta de traducao do Gemini invalida: esperava uma lista "
                f"com {len(texts)} itens, recebeu {translations!r}."
            )
        return translations
