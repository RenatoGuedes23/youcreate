"""Provider de traducao via OpenRouter (gateway para varios modelos de LLM).

Ao contrario do AmazonTranslate (boto3, um texto por chamada), aqui a
chamada e um POST HTTP simples no endpoint "chat completions", compativel
com o formato da OpenAI -- e assim que o OpenRouter expoe qualquer modelo
por tras da mesma API, entao nao ha necessidade de um SDK proprio. O
modelo de fato usado (OPENROUTER_MODEL, ex: "deepseek/deepseek-chat",
"google/gemini-3.8-flash") e configuravel via .env, sem tocar em codigo --
o objetivo inicial e comparar custo/qualidade entre modelos.

Traduz tudo numa unica chamada (todas as falas numeradas no prompt,
resposta em JSON), preservando a ordem sem risco de desalinhamento.
Pedimos um objeto JSON com a chave "translations" (nao um array solto na
raiz), porque "response_format: json_object" -- o jeito mais amplamente
suportado entre modelos variados de pedir JSON -- geralmente exige um
objeto no topo, nao um array.
"""
import json
import logging
import time

import requests

from engine import config

logger = logging.getLogger(__name__)

_API_URL = "https://openrouter.ai/api/v1/chat/completions"
_MAX_RETRIES = 3
_BACKOFF_SECONDS = (2, 5, 10)


class OpenRouterTranslator:
    def __init__(self):
        if not config.OPENROUTER_API_KEY:
            raise RuntimeError(
                "OPENROUTER_API_KEY nao configurada no .env deste projeto."
            )
        if not config.OPENROUTER_MODEL:
            raise RuntimeError(
                "OPENROUTER_MODEL nao configurado no .env deste projeto "
                "(ex: 'deepseek/deepseek-chat', 'google/gemini-3.8-flash')."
            )

    def translate_batch(self, texts: list[str]) -> list[str]:
        """Traduz todas as falas em uma unica chamada, preservando a ordem."""
        if not texts:
            return []

        numbered = "\n".join(f"{i}: {t}" for i, t in enumerate(texts))
        prompt = (
            f"{config.TRANSLATE_STYLE}\n\n"
            f"Traduza cada linha numerada abaixo para {config.TARGET_LANG}. "
            f"Devolva um objeto JSON no formato "
            f'{{"translations": ["...", "..."]}}, com exatamente '
            f"{len(texts)} itens no array, na mesma ordem das linhas, sem "
            f"os numeros, contendo apenas a traducao de cada linha.\n\n{numbered}"
        )

        content = self._call_with_retry(prompt)

        try:
            data = json.loads(content)
            translations = data["translations"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise RuntimeError(
                f"Resposta do OpenRouter (modelo {config.OPENROUTER_MODEL}) nao "
                f"veio no formato esperado: {content!r}"
            ) from exc

        if not isinstance(translations, list) or len(translations) != len(texts):
            raise RuntimeError(
                "Resposta de traducao do OpenRouter invalida: esperava uma "
                f"lista com {len(texts)} itens, recebeu {translations!r}."
            )
        return translations

    def _call_with_retry(self, prompt: str) -> str:
        headers = {
            "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": config.OPENROUTER_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
        }

        for attempt in range(_MAX_RETRIES + 1):
            response = requests.post(_API_URL, headers=headers, json=payload, timeout=120)
            if response.status_code == 200:
                return response.json()["choices"][0]["message"]["content"]

            retryable = response.status_code == 429 or response.status_code >= 500
            if not retryable or attempt == _MAX_RETRIES:
                raise RuntimeError(
                    f"Falha ao chamar o OpenRouter (modelo {config.OPENROUTER_MODEL}): "
                    f"{response.status_code} {response.text}"
                )
            wait = _BACKOFF_SECONDS[attempt]
            logger.warning(
                "OpenRouter respondeu %d, tentativa %d/%d, aguardando %ds antes de tentar de novo.",
                response.status_code, attempt + 1, _MAX_RETRIES, wait,
            )
            time.sleep(wait)

        raise AssertionError("inalcancavel")  # o loop sempre retorna ou levanta antes de sair
