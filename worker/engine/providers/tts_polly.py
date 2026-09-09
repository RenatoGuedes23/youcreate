"""Provider de TTS via Amazon Polly.

Por seguranca, NAO usa a cadeia padrao de credenciais do boto3 (que cairia
para o perfil "default" em ~/.aws/credentials, variaveis de ambiente do shell
ou IAM role da maquina). As credenciais vem exclusivamente de
AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY + AWS_DEFAULT_REGION no .env deste
projeto, para nunca usar por engano um perfil/credencial ja configurado na
maquina para outra conta (ex: da empresa).
"""
import io
import wave
from xml.sax.saxutils import escape as _xml_escape

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from engine import config


class PollyTTS:
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
            "polly",
            aws_access_key_id=config.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=config.AWS_SECRET_ACCESS_KEY,
            region_name=config.AWS_REGION,
        )

    def synthesize(
        self, text: str, voice: str, engine: str | None = None, rate_percent: int | None = None
    ) -> bytes:
        """Sintetiza text na voice dada; devolve bytes de um .wav (PCM 16kHz mono).

        engine sobrescreve config.POLLY_ENGINE so nesta chamada -- necessario
        pra multi-voz (dub.py): nem toda voz suporta o mesmo engine (ex:
        Ricardo so tem "standard", nao "neural"), entao cada voz do pool
        pode precisar de um engine diferente do configurado globalmente.

        rate_percent (ex: 80 = 80% da velocidade normal) usa SSML
        <prosody rate="X%">, suportado pelos engines standard/neural/
        generative (rate e volume sim, pitch nao -- ver docs da AWS). Usado
        por dub.py pra desacelerar uma fala em vez de preencher o slot com
        silencio, quando o narrador original falou aquele trecho mais devagar
        que o normal (ver comentario em dub.py::_fit_segment_clip).
        """
        try:
            if rate_percent is not None:
                ssml = f'<speak><prosody rate="{rate_percent}%">{_xml_escape(text)}</prosody></speak>'
                response = self._client.synthesize_speech(
                    Text=ssml,
                    TextType="ssml",
                    VoiceId=voice,
                    OutputFormat="pcm",
                    SampleRate="16000",  # PCM so aceita 8000 ou 16000 no Polly
                    Engine=engine or config.POLLY_ENGINE,
                )
            else:
                response = self._client.synthesize_speech(
                    Text=text,
                    VoiceId=voice,
                    OutputFormat="pcm",
                    SampleRate="16000",  # PCM so aceita 8000 ou 16000 no Polly
                    Engine=engine or config.POLLY_ENGINE,
                )
        except (BotoCoreError, ClientError) as exc:
            raise RuntimeError(f"Falha ao chamar o Amazon Polly: {exc}") from exc

        pcm_bytes = response["AudioStream"].read()
        return _pcm_to_wav(pcm_bytes, sample_rate=16000)


def _pcm_to_wav(pcm_bytes: bytes, sample_rate: int) -> bytes:
    """Envolve PCM cru (formato de saida do Polly) em um .wav valido."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()
