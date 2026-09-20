"""Modelos de request/response da API."""
from pydantic import BaseModel, field_validator

# Teto do YouTube Shorts: vertical/quadrado com ate 3 minutos. Acima disso o
# upload sai do feed de Shorts e vira um video comum -- como o produto todo
# existe pra gerar Shorts, o backend recusa em vez de gastar processamento
# num clipe que nao serve. (O frontend ja impede antes, isto e a rede de
# seguranca pra chamadas diretas na API.)
SHORT_MAX_SECONDS = 180.0

REFRAME_MODES = ("", "crop", "blur")

# Teto do seletor "quantas pessoas falam" da Tela 2. Nao e limite tecnico do
# pyannote -- e que acima disso o pool de vozes comeca a se repetir e a
# escolha deixa de ajudar.
MAX_SPEAKERS = 6


class JobCreateRequest(BaseModel):
    url: str
    start: float = 0.0
    clip_duration: float | None = None
    source_lang: str = "en"
    target_lang: str = "pt"
    include_subtitles: bool = True
    video_title: str = ""
    video_duration: float = 0.0
    video_quality: str = ""  # "" = automatico (derivado do reenquadramento)
    # 0 = automatico (a diarizacao descobre sozinha). >=1 diz quantas pessoas
    # falam no video: com 1 a diarizacao nem roda (economiza minutos de CPU),
    # com N>1 o pyannote recebe o numero e erra menos.
    speaker_count: int = 0
    reframe_mode: str = ""   # "" = padrao do worker; ou crop|blur|none

    @field_validator("clip_duration")
    @classmethod
    def _cap_to_short(cls, value: float | None) -> float | None:
        if value is not None and value > SHORT_MAX_SECONDS:
            raise ValueError(
                f"Um Short vai ate {int(SHORT_MAX_SECONDS)} segundos "
                f"({int(SHORT_MAX_SECONDS // 60)} minutos)."
            )
        return value

    @field_validator("speaker_count")
    @classmethod
    def _sane_speaker_count(cls, value: int) -> int:
        if value < 0 or value > MAX_SPEAKERS:
            raise ValueError(
                f"Numero de pessoas deve ser 0 (automatico) ou ate {MAX_SPEAKERS}."
            )
        return value

    @field_validator("reframe_mode")
    @classmethod
    def _known_reframe_mode(cls, value: str) -> str:
        if value not in REFRAME_MODES:
            raise ValueError(f"Reenquadramento invalido: {value!r}.")
        return value


class JobCreated(BaseModel):
    id: str


class ProbeResult(BaseModel):
    duration: float
    title: str


class LanguageOption(BaseModel):
    code: str
    name: str
    enabled_as_target: bool
    enabled_as_source: bool = False


class JobStatus(BaseModel):
    id: str
    status: str
    pct: int
    step: str
    message: str
    error_code: str = ""
    created_at: float = 0.0
    source_url: str = ""
    source_lang: str = ""
    video_title: str = ""
    video_duration: float = 0.0
    target_lang: str = ""
    include_subtitles: bool = True
    video_quality: str = ""
    reframe_mode: str = ""
    speaker_count: int = 0
    # O corte precisa voltar pro cliente: sem ele, o "tentar de novo" da
    # tela de erro reenviaria o job sem recorte e processaria o video
    # inteiro -- furando o teto de Shorts, que so e checado quando
    # clip_duration vem preenchido.
    clip_start: float = 0.0
    clip_duration: float | None = None
    video_name: str = ""
    srt_name: str = ""
    video_url: str = ""
    srt_url: str = ""
    vtt_url: str = ""
