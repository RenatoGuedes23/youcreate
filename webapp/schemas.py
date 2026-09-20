"""Modelos de request/response da API."""
from pydantic import BaseModel, field_validator

# Teto do YouTube Shorts: vertical/quadrado com ate 3 minutos. Acima disso o
# upload sai do feed de Shorts e vira um video comum -- como o produto todo
# existe pra gerar Shorts, o backend recusa em vez de gastar processamento
# num clipe que nao serve. (O frontend ja impede antes, isto e a rede de
# seguranca pra chamadas diretas na API.)
SHORT_MAX_SECONDS = 180.0

REFRAME_MODES = ("", "crop", "blur")


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
