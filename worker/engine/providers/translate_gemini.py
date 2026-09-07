"""Provider de traducao via Gemini (google-genai)."""
import json
import logging
import time

from google import genai
from google.genai import errors, types

from engine import config

logger = logging.getLogger(__name__)

# O Gemini responde 503 "high demand" com alguma frequencia quando o modelo
# esta sob carga alta (visto na pratica logo apos o Google descontinuar um
# modelo antigo e empurrar todo mundo pro mesmo modelo novo de uma vez) --
# e um erro do lado deles, transitorio, nao um bug de configuracao. Poucas
# tentativas com backoff curto bastam; erros 4xx (chave invalida, prompt
# rejeitado, etc.) nao sao re-tentados, pois tentar de novo nao muda nada.
_MAX_RETRIES = 3
_BACKOFF_SECONDS = (2, 5, 10)


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

        response = self._generate_with_retry(prompt)

        translations = json.loads(response.text)
        if not isinstance(translations, list) or len(translations) != len(texts):
            raise RuntimeError(
                "Resposta de traducao do Gemini invalida: esperava uma lista "
                f"com {len(texts)} itens, recebeu {translations!r}."
            )
        return translations

    def _generate_with_retry(self, prompt: str):
        for attempt in range(_MAX_RETRIES + 1):
            try:
                return self._client.models.generate_content(
                    model=config.GEMINI_MODEL,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=list[str],
                    ),
                )
            except errors.ServerError as exc:
                if exc.code != 503 or attempt == _MAX_RETRIES:
                    raise
                wait = _BACKOFF_SECONDS[attempt]
                logger.warning(
                    "Gemini respondeu 503 (alta demanda), tentativa %d/%d, "
                    "aguardando %ds antes de tentar de novo.",
                    attempt + 1, _MAX_RETRIES, wait,
                )
                time.sleep(wait)
