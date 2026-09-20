"""Orquestrador do motor: encadeia as etapas e emite progresso.

Nao conhece HTTP nem jobs -- apenas o callback on_progress (e, opcionalmente,
should_cancel, para permitir interromper entre etapas).
"""
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from engine import config
from engine.models import PipelineResult
from engine.steps import audio, download, subtitle, transcribe, translate

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str, str, int, str], None]


def _noop(step_id: str, label: str, pct: int, message: str) -> None:
    pass


def _same_language(source: str | None, target: str | None) -> bool:
    """Diz se origem e destino sao o mesmo idioma (ex: "pt-BR" == "pt").

    Compara so a parte base do codigo, porque os dois lados vem de fontes
    diferentes (o seletor do site manda "pt", o .env pode ter "pt-BR").
    Origem vazia significa deteccao automatica pelo Whisper: nesse caso nao
    da pra afirmar que sao iguais ANTES de transcrever, entao devolve False
    e o pipeline segue traduzindo -- o caminho seguro.
    """
    def base(code: str | None) -> str:
        return (code or "").strip().lower().replace("_", "-").split("-")[0]

    src, tgt = base(source), base(target)
    return bool(src) and src == tgt


class PipelineError(RuntimeError):
    """Erro do pipeline que preserva os artefatos parciais ja gerados.

    Se a legenda ja tiver sido gerada quando a dublagem falhar, por exemplo,
    partial_result.srt_path continua disponivel mesmo com a excecao levantada.
    """

    def __init__(self, message: str, partial_result: PipelineResult):
        super().__init__(message)
        self.partial_result = partial_result


class PipelineCancelled(RuntimeError):
    """Levantada quando should_cancel() diz sim entre duas etapas.

    So cancela ENTRE etapas (nunca no meio de uma chamada de ffmpeg/Whisper/
    tradutor/TTS em andamento) -- e uma escolha deliberada de simplicidade:
    interromper um subprocesso de ffmpeg ou uma chamada de API a meio caminho
    exigiria infraestrutura de cancelamento bem mais complexa.
    """

    def __init__(self, partial_result: PipelineResult):
        super().__init__("Cancelado pelo operador.")
        self.partial_result = partial_result


def run(
    video_path: Path | None = None,
    on_progress: ProgressCallback = _noop,
    make_subs: bool = True,
    make_dub: bool = True,
    work_dir: Path | None = None,
    source_url: str | None = None,
    url_clip_start: float = 0.0,
    url_clip_duration: float | None = None,
    source_lang: str | None = None,
    target_lang: str | None = None,
    max_height: int | None = None,
    reframe_mode: str | None = None,
    speaker_count: int = 0,
    should_cancel: Callable[[], bool] | None = None,
) -> PipelineResult:
    if video_path is None and source_url is None:
        raise ValueError("Informe video_path ou source_url.")

    work_dir = work_dir or config.WORK_DIR
    result = PipelineResult()

    # Video ja no idioma de destino: nao ha o que traduzir nem o que dublar.
    # O trabalho vira so transcrever (pra ter a legenda), reenquadrar e
    # legendar -- sai sem nenhuma chamada ao OpenRouter. Dublar por cima
    # de uma narracao que ja esta no idioma certo so pioraria o video, e
    # mandar o texto pro tradutor dispararia a trava anti-eco dele (uma
    # traducao correta de PT pra PT devolve o proprio texto).
    skip_translation = _same_language(source_lang, target_lang or config.TARGET_LANG)

    def _check_cancelled() -> None:
        if should_cancel is not None and should_cancel():
            raise PipelineCancelled(result)

    try:
        _check_cancelled()
        if source_url is not None:
            # Transcricao/traducao/dublagem so precisam do audio; so o render
            # (bem mais tarde) precisa do video. Baixar os dois em paralelo
            # (thread por download -- ambos I/O-bound, dominados por rede)
            # deixa o tempo combinado igual ao maior dos dois em vez da soma,
            # ja que o audio sozinho baixa muito mais rapido que o video.
            on_progress("download", "Baixando video", 2, "Baixando audio e video em paralelo...")

            def _prepare_audio() -> Path:
                audio_src = download.download_audio(
                    source_url, work_dir / "source",
                    start=url_clip_start, duration=url_clip_duration,
                )
                return audio.extract_audio(audio_src, work_dir)

            def _prepare_video() -> Path:
                return download.download_video(
                    source_url, work_dir / "source",
                    start=url_clip_start, duration=url_clip_duration,
                    max_height=max_height,
                )

            with ThreadPoolExecutor(max_workers=2) as executor:
                audio_future = executor.submit(_prepare_audio)
                video_future = executor.submit(_prepare_video)
                audio_path = audio_future.result()
                video_path = video_future.result()

            _check_cancelled()
            on_progress("audio", "Video e audio prontos", 5, "Download concluido.")
        else:
            _check_cancelled()
            on_progress("audio", "Extraindo audio", 5, "Extraindo audio do video...")
            audio_path = audio.extract_audio(video_path, work_dir)

        _check_cancelled()
        on_progress("transcribe", "Transcrevendo", 20, "Transcrevendo audio original...")
        # speaker_count == 1: o operador informou na Tela 2 que so ha uma
        # pessoa falando, entao nao ha nada pra diarizar -- pular a etapa
        # economiza varios minutos de CPU (o pyannote e a etapa mais lenta
        # do pipeline depois da separacao de fontes) sem mudar o resultado,
        # ja que com um locutor so todas as falas usariam DUB_VOICE mesmo.
        if make_dub and not skip_translation and speaker_count != 1:
            # Diarizacao (quem fala quando) so importa pra dublagem
            # multi-voz -- roda em paralelo com a transcricao, ja que as
            # duas processam o mesmo audio_path de forma independente uma
            # da outra (nenhuma usa o resultado da outra como entrada).
            from engine.steps import diarize

            with ThreadPoolExecutor(max_workers=2) as executor:
                transcribe_future = executor.submit(
                    transcribe.transcribe, audio_path, language=source_lang or None,
                )
                diarize_future = executor.submit(
                    diarize.diarize, audio_path, speaker_count,
                )
                segments = transcribe_future.result()
                turns = diarize_future.result()

            detectados = diarize.assign_speakers(segments, turns)
            if detectados > 1:
                logger.info("Diarizacao detectou %d locutores distintos.", detectados)
        else:
            if speaker_count == 1:
                logger.info(
                    "Operador informou 1 locutor: diarizacao pulada (voz unica)."
                )
            segments = transcribe.transcribe(audio_path, language=source_lang or None)
        result.segments = segments

        _check_cancelled()
        if skip_translation:
            # As etapas seguintes (legenda, render) leem seg.translation; com
            # o video ja no idioma certo, ela e o proprio texto transcrito.
            on_progress("translate", "Mesmo idioma", 45,
                        "Video ja esta no idioma de destino: traducao dispensada.")
            for seg in segments:
                seg.translation = seg.text
            logger.info("Origem e destino sao o mesmo idioma: pulando traducao e dublagem.")
        else:
            on_progress("translate", "Traduzindo", 45, "Traduzindo falas para PT-BR...")
            translate.translate_segments(segments)

        if make_subs:
            _check_cancelled()
            on_progress("subtitle", "Gerando legenda", 60, "Gerando arquivo .srt...")
            srt_path = config.OUTPUTS_DIR / f"{video_path.stem}.pt-BR.srt"
            result.srt_path = subtitle.build_srt(segments, srt_path, translated=True)
            vtt_path = config.OUTPUTS_DIR / f"{video_path.stem}.pt-BR.vtt"
            result.vtt_path = subtitle.build_vtt(segments, vtt_path, translated=True)

        # make_dub aqui significa "produzir o video final". Quando origem e
        # destino coincidem o video sai igual, so que mantendo o audio
        # original em vez de uma trilha dublada.
        if make_dub:
            from engine.steps import captions, reframe, render

            music_audio = None
            if skip_translation:
                on_progress("dub", "Sem dublagem", 75,
                            "Mantendo o audio original do video.")
            else:
                from engine.steps import dub

                _check_cancelled()
                on_progress("dub", "Dublando", 72, "Gerando dublagem PT-BR...")
                result.dub_audio_path = dub.synthesize_dub(segments, work_dir)

                # synthesize_dub reescreve seg.start/seg.end com o tempo real
                # da fala dublada (ver o comentario la). O .ass queimado e
                # construido depois daqui e ja pega o tempo novo, mas o .srt
                # e o .vtt foram gerados ANTES da dublagem -- sem reescrever,
                # o arquivo que o operador baixa fica adiantado em relacao a
                # voz do MP4.
                if make_subs and result.srt_path:
                    subtitle.build_srt(segments, result.srt_path, translated=True)
                    subtitle.build_vtt(segments, result.vtt_path, translated=True)

                if config.DUB_KEEP_MUSIC:
                    # Etapa propria no progresso porque e lenta (~45s por
                    # minuto de audio): sem isso a tela ficaria parada na
                    # dublagem sem explicar o porque.
                    _check_cancelled()
                    on_progress("separate", "Separando a trilha", 82,
                                "Removendo a voz original e preservando a musica...")
                    from engine.steps import separate

                    music_audio = separate.extract_music(audio_path, work_dir)

            _check_cancelled()
            on_progress("render", "Renderizando", 90, "Montando video final...")

            # Legenda queimada estilo Shorts. Fica no work_dir (e intermediaria,
            # some com o job) -- o que o operador baixa continua sendo o .srt.
            # So faz sentido quando ha legenda pedida E ela vai pra imagem.
            active_reframe = reframe_mode or config.REFRAME_MODE

            ass_path = None
            if make_subs and config.BURN_SUBS:
                # play_res precisa bater com a resolucao real da saida: a
                # libass escala PlayResX e PlayResY ate o quadro de forma
                # independente, entao declarar errado deforma o contorno do
                # texto. Como todo Short sai 1080x1920, e sempre esse.
                ass_path = captions.build_ass(
                    segments,
                    work_dir / "captions.ass",
                    opts={
                        "play_res": (reframe.SHORT_W, reframe.SHORT_H),
                        "max_words": config.CAPTION_MAX_WORDS,
                        "uppercase": config.CAPTION_UPPERCASE,
                        "font_name": config.CAPTION_FONT,
                        "font_size": config.CAPTION_FONT_SIZE,
                        "margin_v": config.CAPTION_MARGIN_V,
                    },
                )

            video_out = config.OUTPUTS_DIR / f"{video_path.stem}.pt-BR.mp4"
            result.video_out = render.build_final(
                video_path,
                result.srt_path,
                result.dub_audio_path,
                video_out,
                opts={
                    "burn_subs": config.BURN_SUBS,
                    "music_audio": music_audio,
                    "music_db": config.DUB_MUSIC_DB,
                    "reframe_mode": active_reframe,
                    "ass_path": ass_path,
                },
            )
    except PipelineCancelled:
        logger.info("Pipeline cancelado para %s", video_path)
        raise
    except Exception as exc:
        logger.exception("Falha no pipeline para %s", video_path)
        raise PipelineError(str(exc), result) from exc

    on_progress("done", "Concluido", 100, "Processamento concluido.")
    logger.info("Pipeline concluido para %s", video_path)
    return result
