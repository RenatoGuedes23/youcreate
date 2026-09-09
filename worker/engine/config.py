"""Configuracao do motor via variaveis de ambiente, com defaults sensatos."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

STORAGE_DIR = Path(os.environ.get("STORAGE_DIR", BASE_DIR / "storage"))
OUTPUTS_DIR = Path(os.environ.get("OUTPUTS_DIR", STORAGE_DIR / "outputs"))
WORK_DIR = Path(os.environ.get("WORK_DIR", STORAGE_DIR / "work"))

for _dir in (OUTPUTS_DIR, WORK_DIR):
    _dir.mkdir(parents=True, exist_ok=True)

TARGET_LANG = os.environ.get("TARGET_LANG", "pt")

# Cookies opcionais (Netscape cookies.txt) de uma sessao logada no YouTube --
# usadas quando o YouTube exige confirmar "nao sou um robo" antes de servir
# video/metadados. ATENCAO: contem cookies de sessao da conta Google (SID/
# SAPISID/etc.) -- decisao explicita do operador manter versionado neste
# repo (nao esta no .gitignore); trate como credencial sensivel mesmo assim.
YOUTUBE_COOKIES_FILE = os.environ.get("YOUTUBE_COOKIES_FILE") or str(BASE_DIR / "cookies.txt")

WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "medium")
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "auto")
WHISPER_COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE", "default")

# TRANSLATE_PROVIDER: openrouter (unico provider hoje). Gemini foi removido
# (esgotava a cota do free tier com poucos videos/dia) e o Amazon Translate
# tambem foi removido depois (traducao literal, sem contexto -- ver
# docs/ARQUITETURA.md pros dois casos).
TRANSLATE_PROVIDER = os.environ.get("TRANSLATE_PROVIDER", "openrouter")
# OpenRouter: gateway unico pra varios modelos de LLM -- o modelo em si e
# trocavel via OPENROUTER_MODEL (formato "provider/modelo", ex:
# "deepseek/deepseek-chat"), sem precisar mudar codigo, so o .env. Usado
# para comparar custo/qualidade entre modelos antes de fixar um definitivo.
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "")
# Falas por chamada de traducao. Uma chamada unica com o video inteiro (ex:
# 1400 falas) demorava demais e batia no timeout com frequencia -- ver
# translate_openrouter.py::translate_batch. Lotes menores respondem mais
# rapido e mais confiavel, ao custo de mais chamadas HTTP (mais overhead de
# rede/tokens repetidos nas instrucoes do prompt).
OPENROUTER_BATCH_SIZE = int(os.environ.get("OPENROUTER_BATCH_SIZE", "250"))
TRANSLATE_STYLE = os.environ.get(
    "TRANSLATE_STYLE",
    "Traduza de forma natural para o portugues do Brasil, mantendo nomes "
    "proprios e adaptando girias.",
)

TTS_PROVIDER = os.environ.get("TTS_PROVIDER", "polly")
POLLY_ENGINE = os.environ.get("POLLY_ENGINE", "standard")  # standard|neural (nem toda regiao suporta neural)
# Lidas explicitamente (nao via cadeia padrao do boto3) para nunca usar por
# engano um perfil/credencial ja configurado na maquina (ex: da empresa).
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
AWS_REGION = os.environ.get("AWS_DEFAULT_REGION", "")
DUB_VOICE = os.environ.get("DUB_VOICE", "Camila")
DUB_MAX_SPEEDUP = float(os.environ.get("DUB_MAX_SPEEDUP", "1.3"))
DUB_KEEP_MUSIC = os.environ.get("DUB_KEEP_MUSIC", "false").lower() == "true"

# Diarizacao de locutor (engine/steps/diarize.py) -- opcional: sem HF_TOKEN,
# o pipeline nao tenta diarizar e cai no comportamento historico (DUB_VOICE
# unica pra todo mundo). Com token, cada locutor detectado no video ganha
# uma voz Polly diferente (ver dub.py). Token de leitura basica gerado em
# https://huggingface.co/settings/tokens, depois de aceitar os termos em
# https://huggingface.co/pyannote/speaker-diarization-3.1 e .../segmentation-3.0.
HF_TOKEN = os.environ.get("HF_TOKEN", "")
DUB_ENABLE_DIARIZATION = os.environ.get("DUB_ENABLE_DIARIZATION", "true").lower() == "true"

BURN_SUBS = os.environ.get("BURN_SUBS", "true").lower() == "true"
