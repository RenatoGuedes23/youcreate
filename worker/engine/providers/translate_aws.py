"""Provider de traducao via Amazon Translate.

Mesma politica de seguranca do Polly (tts_polly.py): credenciais lidas
apenas de AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY/AWS_DEFAULT_REGION no
.env deste projeto, nunca da cadeia padrao do boto3.

Diferenca de comportamento vs. os providers baseados em LLM (OpenRouter):
o Amazon Translate nao e um LLM com prompt -- e um servico de traducao
literal, e a API so aceita UM texto por chamada (nao existe "traduza esta
lista preservando a ordem" numa unica chamada). Por isso translate_batch()
faz uma chamada de API por fala, em sequencia (nao em paralelo, para nao
estourar o limite de transacoes por segundo da conta) -- perde-se o
controle de tom/estilo via prompt (TRANSLATE_STYLE nao se aplica aqui),
ganha-se um limite de uso mais previsivel e sem cota diaria apertada.
"""
import logging
import time

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from engine import config

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_BACKOFF_SECONDS = (1, 3, 6)


class AmazonTranslate:
    def __init__(self):
        if not config.AWS_ACCESS_KEY_ID or not config.AWS_SECRET_ACCESS_KEY:
            raise RuntimeError(
                "Credenciais da AWS nao configuradas no .env deste projeto. "
                "Defina AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY e "
                "AWS_DEFAULT_REGION -- o youcreate nunca usa credenciais "
                "globais/compartilhadas da maquina (ex: ~/.aws/credentials)."
            )
        if not config.AWS_REGION:
            raise RuntimeError("AWS_DEFAULT_REGION nao configurada no .env.")

        self._client = boto3.client(
            "translate",
            aws_access_key_id=config.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=config.AWS_SECRET_ACCESS_KEY,
            region_name=config.AWS_REGION,
        )

    def translate_batch(self, texts: list[str]) -> list[str]:
        """Traduz cada fala com uma chamada separada -- ver docstring do modulo."""
        return [self._translate_one(text) for text in texts]

    def _translate_one(self, text: str) -> str:
        if not text.strip():
            return ""

        for attempt in range(_MAX_RETRIES + 1):
            try:
                response = self._client.translate_text(
                    Text=text,
                    SourceLanguageCode="auto",
                    TargetLanguageCode=config.TARGET_LANG,
                )
                return response["TranslatedText"]
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code != "TooManyRequestsException" or attempt == _MAX_RETRIES:
                    raise RuntimeError(f"Falha ao chamar o Amazon Translate: {exc}") from exc
                wait = _BACKOFF_SECONDS[attempt]
                logger.warning(
                    "Amazon Translate respondeu TooManyRequestsException, "
                    "tentativa %d/%d, aguardando %ds antes de tentar de novo.",
                    attempt + 1, _MAX_RETRIES, wait,
                )
                time.sleep(wait)
            except BotoCoreError as exc:
                raise RuntimeError(f"Falha ao chamar o Amazon Translate: {exc}") from exc

        raise AssertionError("inalcancavel")  # o loop sempre retorna ou levanta antes de sair
