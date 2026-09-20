"""Etapa (5b): legenda no estilo Shorts -- poucas palavras por vez, grandes,
queimadas no video vertical.

Separado de subtitle.py de proposito: aquele gera o .srt/.vtt convencional
(arquivos que o operador baixa e que alimentam o player da tela de
resultado). Aqui o alvo e outro -- o texto queimado na imagem, que precisa
de controle de fonte, contorno e posicao. O unico formato que o ffmpeg
aceita com esse controle e o ASS (via libass).

Sobre o tempo de cada trecho: o Whisper cronometra o audio ORIGINAL (em
ingles), entao os timestamps por palavra dele nao servem aqui -- a legenda
exibida e a traducao PT-BR, com outras palavras e em outra quantidade. O
que se aproveita e o intervalo do segmento (esse sim preciso); dentro dele
o tempo e repartido entre os trechos proporcionalmente ao numero de
caracteres, que aproxima melhor o tempo de fala do que contar palavras
(palavra longa demora mais que palavra curta).
"""
from pathlib import Path

from engine.models import Segment

# Resolucao de referencia do ASS. Precisa bater com a resolucao REAL do
# video de saida: a libass escala X e Y de forma independente entre PlayRes
# e o quadro, entao declarar 1080x1920 sobre um video 1920x1080 nao so
# encolhe a fonte como DEFORMA o contorno (esticado na horizontal,
# achatado na vertical). Caso observado no modo de reenquadramento "none".
PLAY_RES_X = 1080
PLAY_RES_Y = 1920

# Os tamanhos padrao (fonte, margem) sao expressos para um quadro de
# PLAY_RES_Y de altura e reescalados proporcionalmente quando a saida tem
# outra altura, pra legenda ocupar sempre a mesma fracao da tela.
_REFERENCE_HEIGHT = PLAY_RES_Y


def _ass_timestamp(seconds: float) -> str:
    """Converte segundos para o formato ASS H:MM:SS.cc (centesimos)."""
    if seconds < 0:
        seconds = 0.0
    total_cs = round(seconds * 100)
    hours, rem_cs = divmod(total_cs, 360_000)
    minutes, rem_cs = divmod(rem_cs, 6_000)
    secs, cs = divmod(rem_cs, 100)
    return f"{hours:d}:{minutes:02d}:{secs:02d}.{cs:02d}"


def _escape_text(text: str) -> str:
    """Neutraliza o que a libass interpretaria como marcacao.

    Chaves delimitam blocos de override ({\\b1} etc) e quebras de linha
    reais encerrariam a linha do Dialogue no meio -- viram \\N, a quebra
    explicita do ASS.
    """
    text = text.replace("{", "(").replace("}", ")")
    return " ".join(text.split())


def _split_into_chunks(text: str, max_words: int) -> list[str]:
    """Quebra o texto em grupos de ate max_words palavras."""
    words = text.split()
    if not words:
        return []
    return [
        " ".join(words[i:i + max_words])
        for i in range(0, len(words), max_words)
    ]


def _timed_chunks(seg: Segment, max_words: int) -> list[tuple[float, float, str]]:
    """Reparte o intervalo do segmento entre seus trechos de texto."""
    text = (seg.translation or seg.text or "").strip()
    chunks = _split_into_chunks(_escape_text(text), max_words)
    if not chunks:
        return []

    span = max(seg.end - seg.start, 0.0)
    if span <= 0:
        return []

    # Peso por caracteres: um trecho com o dobro de texto fica o dobro do
    # tempo na tela. +1 evita peso zero num trecho de um caractere so.
    weights = [len(c) + 1 for c in chunks]
    total_weight = sum(weights)

    timed: list[tuple[float, float, str]] = []
    cursor = seg.start
    for chunk, weight in zip(chunks, weights):
        share = span * weight / total_weight
        end = min(cursor + share, seg.end)
        timed.append((cursor, end, chunk))
        cursor = end
    # Corrige arredondamento acumulado: o ultimo trecho termina exatamente
    # no fim do segmento.
    if timed:
        start, _, chunk = timed[-1]
        timed[-1] = (start, seg.end, chunk)
    return timed


def _header(opts: dict) -> str:
    play_w, play_h = opts.get("play_res", (PLAY_RES_X, PLAY_RES_Y))
    scale = play_h / _REFERENCE_HEIGHT

    font_name = opts.get("font_name", "DejaVu Sans")
    font_size = max(1, round(int(opts.get("font_size", 92)) * scale))
    outline = max(1, round(int(opts.get("outline", 6)) * scale))
    shadow = max(0, round(int(opts.get("shadow", 2)) * scale))
    # Distancia do texto ate a base do quadro. O Shorts sobrepoe titulo,
    # canal e botoes na faixa de baixo; ~600px de margem num quadro de
    # 1920 deixa a legenda logo abaixo do centro, fora dessa faixa.
    margin_v = max(0, round(int(opts.get("margin_v", 600)) * scale))
    margin_h = max(0, round(int(opts.get("margin_h", 80)) * scale))

    # Cores no formato ASS &HAABBGGRR (alfa primeiro, depois BGR -- nao RGB).
    primary = opts.get("primary_colour", "&H00FFFFFF")   # branco
    outline_colour = opts.get("outline_colour", "&H00000000")  # preto

    return "\n".join([
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {play_w}",
        f"PlayResY: {play_h}",
        # WrapStyle 0 = quebra automatica em linhas equilibradas. Necessario
        # mesmo com trechos curtos: 3 palavras longas em portugues ("Nunca
        # vou conseguir...") passam da largura util e, sem quebra, a libass
        # deixa o texto vazar pra fora do quadro em vez de quebrar.
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        # Bold=-1 e "ligado" no ASS (nao 1). Alignment=2 ancora embaixo ao
        # centro, medido por MarginV.
        f"Style: Short,{font_name},{font_size},{primary},&H000000FF,"
        f"{outline_colour},&H80000000,-1,0,0,0,100,100,0,0,1,"
        f"{outline},{shadow},2,{margin_h},{margin_h},{margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text",
    ])


def build_ass(segments: list[Segment], out_path: Path, opts: dict | None = None) -> Path:
    """Gera o .ass da legenda queimada no formato Shorts.

    opts aceita play_res ((largura, altura) reais do video de saida),
    font_name, font_size, outline, shadow, margin_v, margin_h, max_words
    (palavras por trecho na tela) e uppercase. Tamanhos sao dados para um
    quadro de 1920 de altura e reescalados conforme play_res.
    """
    opts = opts or {}
    max_words = max(int(opts.get("max_words", 3)), 1)
    uppercase = bool(opts.get("uppercase", False))

    lines = [_header(opts)]
    for seg in segments:
        for start, end, chunk in _timed_chunks(seg, max_words):
            if end <= start:
                continue
            text = chunk.upper() if uppercase else chunk
            lines.append(
                f"Dialogue: 0,{_ass_timestamp(start)},{_ass_timestamp(end)},"
                f"Short,,0,0,0,,{text}"
            )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path
