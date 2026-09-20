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

# TTS_PROVIDER: openrouter (unico provider hoje). O Amazon Polly foi
# implementado e removido -- as vozes do OpenRouter soaram melhor nos testes
# comparativos e dispensam a conta AWS (uma credencial a menos no projeto).
TTS_PROVIDER = os.environ.get("TTS_PROVIDER", "openrouter")
# Modelo + voz padrao da dublagem. Formato do OpenRouter: "provider/modelo"
# e o id da voz listado em supported_voices na API de modelos. Trocar de
# modelo/voz e so mexer aqui -- nao ha codigo especifico por voz.
DUB_MODEL = os.environ.get("DUB_MODEL") or "hexgrad/kokoro-82m"
DUB_VOICE = os.environ.get("DUB_VOICE") or "pm_alex"
# Vozes adicionais pra videos com mais de um locutor (ver diarizacao
# abaixo): lista separada por virgula, cada item "modelo|voz". DUB_MODEL/
# DUB_VOICE e sempre a primeira do pool; estas entram na ordem em que novos
# locutores aparecem no video. Podem ser de modelos diferentes entre si.
# `or` em vez do default de os.environ.get: a variavel vazia no .env EXISTE,
# entao get() devolveria "" e o pool ficaria so com a voz padrao -- mesma
# armadilha que o AWS_PROFILE vazio ja causou neste projeto.
DUB_VOICE_POOL = os.environ.get("DUB_VOICE_POOL") or (
    "hexgrad/kokoro-82m|pm_santa,"
    "hexgrad/kokoro-82m|pf_dora,"
    "google/gemini-3.1-flash-tts-preview|Algieba,"
    "google/gemini-3.1-flash-tts-preview|Charon,"
    "google/gemini-3.1-flash-tts-preview|Orus,"
    "google/gemini-3.1-flash-tts-preview|Puck,"
    "x-ai/grok-voice-tts-1.0|rex,"
    "x-ai/grok-voice-tts-1.0|leo,"
    "x-ai/grok-voice-tts-1.0|sal"
)
DUB_MAX_SPEEDUP = float(os.environ.get("DUB_MAX_SPEEDUP", "1.3"))
# Quanto uma fala pode ser ADIADA pra nao tocar por cima da anterior (seg).
# Falas que estouram o slot sao empurradas pra frente em vez de sobrepor
# (dub.py::_stagger_starts); o atraso se dissolve na primeira pausa do
# video. Acima deste teto preferimos a sobreposicao, porque a dublagem
# sairia de sincronia com a imagem de forma perceptivel.
DUB_MAX_DRIFT = float(os.environ.get("DUB_MAX_DRIFT", "1.5"))
# Preserva a musica/ambiencia do video original sob a dublagem. A trilha
# passa antes por separacao de fontes (engine/steps/separate.py) pra tirar a
# voz original -- o caminho antigo, so abaixar a faixa inteira, deixava a
# voz em ingles audivel por baixo e soou mal na pratica.
DUB_KEEP_MUSIC = os.environ.get("DUB_KEEP_MUSIC", "false").lower() == "true"
# Volume da trilha separada sob a dublagem. Parametro de gosto: a musica ja
# vinha mixada pra caber sob o narrador original, e a voz sintetizada
# costuma sair mais alta -- por isso uma reducao suave, nao os -18 dB que o
# ducking antigo precisava pra enterrar a voz que sobrava.
DUB_MUSIC_DB = float(os.environ.get("DUB_MUSIC_DB", "-6"))
# Processos paralelos do demucs. Medido neste projeto: 4 e o ponto otimo
# (45s/min de audio); 12 piora pra 114s porque os processos competem pelas
# mesmas threads de torch.
DUB_SEPARATION_JOBS = int(os.environ.get("DUB_SEPARATION_JOBS", "4"))
# Diarizacao de locutor (engine/steps/diarize.py) -- opcional: sem HF_TOKEN,
# o pipeline nao tenta diarizar e cai no comportamento historico (DUB_VOICE
# unica pra todo mundo). Com token, cada locutor detectado no video ganha
# uma voz diferente do pool (ver dub.py e DUB_VOICE_POOL). Token de leitura gerado em
# https://huggingface.co/settings/tokens, depois de aceitar os termos em
# https://huggingface.co/pyannote/speaker-diarization-3.1 e .../segmentation-3.0.
HF_TOKEN = os.environ.get("HF_TOKEN", "")
DUB_ENABLE_DIARIZATION = os.environ.get("DUB_ENABLE_DIARIZATION", "true").lower() == "true"

BURN_SUBS = os.environ.get("BURN_SUBS", "true").lower() == "true"

# --- Formato Shorts (vertical) ---
# Limite do YouTube Shorts: vertical/quadrado com ate 3 minutos (era 60s ate
# outubro de 2024). Acima disso o video vira um upload comum, fora do feed de
# Shorts. Atencao a uma regra separada: Short com MAIS de 60s que use musica
# nao-livre de direitos leva claim de Content ID e e bloqueado -- o que
# importa aqui porque DUB_KEEP_MUSIC preserva a trilha do video original.
SHORT_MAX_SECONDS = int(os.environ.get("SHORT_MAX_SECONDS", "180"))

# Reenquadramento padrao quando o job nao especifica (ver engine/steps/reframe.py):
# crop (corte central) | blur (fundo desfocado) | none (mantem 16:9).
REFRAME_MODE = os.environ.get("REFRAME_MODE", "crop")

# Legenda queimada estilo Shorts (engine/steps/captions.py). Poucas palavras
# por vez, fonte grande. DejaVu Sans e a unica familia instalada na imagem do
# worker; trocar exige instalar a fonte no Dockerfile tambem.
CAPTION_MAX_WORDS = int(os.environ.get("CAPTION_MAX_WORDS", "3"))
CAPTION_UPPERCASE = os.environ.get("CAPTION_UPPERCASE", "false").lower() == "true"
CAPTION_FONT = os.environ.get("CAPTION_FONT", "DejaVu Sans")
CAPTION_FONT_SIZE = int(os.environ.get("CAPTION_FONT_SIZE", "92"))
CAPTION_MARGIN_V = int(os.environ.get("CAPTION_MARGIN_V", "600"))
