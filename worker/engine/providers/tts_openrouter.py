"""Provider de TTS via OpenRouter (endpoint /audio/speech).

Uma ponte generica pro endpoint de fala do OpenRouter, do mesmo jeito que
translate_openrouter.py e pro /chat/completions: qualquer modelo de TTS que
o OpenRouter carregue pode ser usado so trocando DUB_MODEL/DUB_VOICE no
.env, sem mexer em codigo.

Tres diferencas entre modelos que o codigo precisa conhecer, todas medidas
nesta conta (nao estao documentadas juntas em lugar nenhum):

1. FORMATO. O Gemini TTS recusa response_format="mp3" com HTTP 400 e so
   aceita "pcm" -- PCM cru 24 kHz 16 bits mono, sem cabecalho, que e
   embrulhado em .wav aqui. Os demais aceitam mp3. Por isso _OUTPUT_FORMAT
   e por modelo, nao global.

2. VELOCIDADE. O parametro "speed" existe na API mas NAO e honrado por
   todos: medido com a mesma fala, o Gemini devolveu 5.40s / 5.68s / 5.80s
   para speed ausente / 1.3 / 0.8 (ou seja, ignora -- a variacao e do
   proprio modelo, que nao e deterministico), o Grok acelera mas nao
   desacelera, e so o Kokoro responde nos dois sentidos. dub.py consulta
   supports_speed() pra decidir entre pedir a fala mais rapida ao modelo
   (melhor) ou comprimir com atempo depois (pior, mas e o que resta).

3. SAMPLE RATE. Tudo e normalizado pra 24 kHz mono aqui, porque dub.py
   soma os clipes numa trilha unica e mistura taxas quebraria o mix.
"""
import logging
import subprocess
import tempfile
import time
import wave
from pathlib import Path

import requests

from engine import config

logger = logging.getLogger(__name__)

_ENDPOINT = "https://openrouter.ai/api/v1/audio/speech"
_TIMEOUT = 120
_MAX_RETRIES = 3
_BACKOFF_BASE = 2.0
# Mesma licao do translate_openrouter: repetir so o que pode dar certo numa
# segunda tentativa. Uma chave invalida (401) ou um modelo inexistente (404)
# falham igual para sempre; 429 e 5xx sao transitorios.
_RETRY_STATUS = {429, 500, 502, 503, 504}

# Taxa de saida unica da dublagem (dub.py monta uma trilha so).
_SAMPLE_RATE = 24000

# Modelos que so aceitam PCM cru. Ver item (1) do docstring.
_PCM_ONLY_MODELS = {"google/gemini-3.1-flash-tts-preview"}

# Modelos cujo parametro "speed" foi verificado como realmente honrado.
# Conservador de proposito: fora desta lista, dub.py usa atempo, que e pior
# mas previsivel -- pior que isso seria pedir speed, o modelo ignorar, e a
# fala ficar fora do tempo sem ninguem perceber.
_SPEED_CAPABLE_MODELS = {"hexgrad/kokoro-82m"}


class OpenRouterTTS:
    def __init__(self):
        if not config.OPENROUTER_API_KEY:
            raise RuntimeError(
                "OPENROUTER_API_KEY nao configurada no .env deste projeto -- "
                "e a mesma chave usada pela traducao."
            )
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        })

    def supports_speed(self, model: str | None = None) -> bool:
        """Diz se vale pedir a fala mais rapida/lenta ao proprio modelo."""
        return (model or config.DUB_MODEL) in _SPEED_CAPABLE_MODELS

    def synthesize(
        self,
        text: str,
        voice: str,
        model: str | None = None,
        speed: float | None = None,
    ) -> bytes:
        """Sintetiza text na voice dada; devolve bytes de um .wav 24 kHz mono.

        speed e um multiplicador (1.3 = 30% mais rapido). So e enviado se o
        modelo estiver em _SPEED_CAPABLE_MODELS -- mandar pra um modelo que
        ignora o parametro gastaria uma chamada pra nada.
        """
        model = model or config.DUB_MODEL
        fmt = "pcm" if model in _PCM_ONLY_MODELS else "mp3"

        payload = {
            "model": model,
            "input": text,
            "voice": voice,
            "response_format": fmt,
        }
        if speed is not None and self.supports_speed(model):
            payload["speed"] = speed

        audio = self._post_with_retry(payload, model, voice)
        return _pcm_to_wav(audio) if fmt == "pcm" else _mp3_to_wav(audio)

    def _post_with_retry(self, payload: dict, model: str, voice: str) -> bytes:
        last_error = ""
        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = self._session.post(_ENDPOINT, json=payload, timeout=_TIMEOUT)
            except requests.RequestException as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt >= _MAX_RETRIES:
                    break
                time.sleep(_BACKOFF_BASE ** attempt)
                continue

            if resp.status_code == 200:
                return resp.content

            last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
            if resp.status_code not in _RETRY_STATUS or attempt >= _MAX_RETRIES:
                break
            logger.warning(
                "OpenRouter TTS (%s/%s) devolveu %d; tentativa %d/%d.",
                model, voice, resp.status_code, attempt + 1, _MAX_RETRIES,
            )
            time.sleep(_BACKOFF_BASE ** attempt)

        raise RuntimeError(
            f"Falha ao sintetizar voz no OpenRouter ({model}/{voice}): {last_error}"
        )


def _pcm_to_wav(pcm_bytes: bytes) -> bytes:
    """Embrulha o PCM cru (24 kHz, 16 bits, mono) do Gemini TTS em .wav."""
    buf = Path(tempfile.mkdtemp()) / "out.wav"
    with wave.open(str(buf), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(_SAMPLE_RATE)
        wf.writeframes(pcm_bytes)
    data = buf.read_bytes()
    buf.unlink()
    buf.parent.rmdir()
    return data


def _mp3_to_wav(mp3_bytes: bytes) -> bytes:
    """Decodifica mp3 para .wav PCM 16 bits mono a _SAMPLE_RATE.

    Passa por arquivo temporario em vez de pipe de proposito: .wav escrito em
    stdout nao e seekable, o ffmpeg nao consegue voltar pra preencher os
    tamanhos no cabecalho, e o modulo `wave` (usado por dub.py::_wav_info
    pra medir a duracao) leria a duracao errada.
    """
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "tts.mp3"
        dst = Path(tmp) / "tts.wav"
        src.write_bytes(mp3_bytes)
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(src), "-ar", str(_SAMPLE_RATE),
                 "-ac", "1", "-c:a", "pcm_s16le", str(dst)],
                check=True, capture_output=True, text=True,
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"Falha ao converter o audio do OpenRouter para .wav:\n{exc.stderr}"
            ) from exc
        return dst.read_bytes()
