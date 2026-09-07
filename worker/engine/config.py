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

WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small")
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "auto")
WHISPER_COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE", "default")

TRANSLATE_PROVIDER = os.environ.get("TRANSLATE_PROVIDER", "gemini")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
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

BURN_SUBS = os.environ.get("BURN_SUBS", "true").lower() == "true"
