"""Etapa (6b): reenquadramento para 9:16 (formato Shorts).

Este modulo nao executa ffmpeg -- ele so MONTA o trecho de filtro que o
render vai encadear. O motivo e qualidade: reenquadrar num passe e
legendar noutro recodificaria o video duas vezes (perda geracional, e o
dobro do tempo de CPU). Render faz tudo num passe so.
"""

SHORT_W = 1080
SHORT_H = 1920

MODE_CROP = "crop"
MODE_BLUR = "blur"

# O modo "none" (manter 16:9, sem reenquadrar) existiu e foi removido: o
# produto so entrega Short, e um 16:9 nao entra no feed de Shorts. Todo
# video que sai daqui e 1080x1920.
VALID_MODES = (MODE_CROP, MODE_BLUR)


def recommended_max_height(mode: str) -> int | None:
    """Altura de download recomendada para o modo de reenquadramento.

    Usado pela qualidade "Automatico". O corte central descarta as laterais
    do 16:9: de um 1920x1080 sobra so 608x1080, que subiria pra 1080x1920
    em upscale (imagem mole). Pedindo 2160p sobra 1215x2160, que DESCE pra
    1080x1920 -- nitido. Ja no modo desfocado o video cabe inteiro na
    largura (1080x608 no centro), entao 1080p basta e 4K so gastaria banda.

    None significa "melhor disponivel, sem limite".
    """
    if mode == MODE_CROP:
        return 2160
    return 1080  # MODE_BLUR


def build_filter(mode: str, in_label: str = "0:v", out_label: str = "vout") -> list[str]:
    """Devolve as partes do filter_complex que levam in_label a out_label."""
    if mode not in VALID_MODES:
        raise ValueError(
            f"Modo de reenquadramento invalido: {mode!r}. "
            f"Esperado um de {VALID_MODES}."
        )

    if mode == MODE_CROP:
        # Recorta o maior retangulo 9:16 centralizado que cabe na imagem.
        # Os min() cobrem o caso de a fonte ja ser vertical (ih*9/16 passaria
        # da largura disponivel) -- sem eles o ffmpeg falharia com "Invalid
        # too big or non positive size for width".
        return [
            f"[{in_label}]"
            "crop=w='min(iw,ih*9/16)':h='min(ih,iw*16/9)'"
            ":x='(iw-ow)/2':y='(ih-oh)/2',"
            f"scale={SHORT_W}:{SHORT_H}:flags=lanczos,setsar=1"
            f"[{out_label}]"
        ]

    # MODE_BLUR: video inteiro no centro, sobre uma copia ampliada e borrada
    # preenchendo cima e baixo. O fundo e reduzido a 1/4 ANTES de borrar e
    # ampliado depois -- desfoque em resolucao baixa custa uma fracao do
    # tempo e o resultado e visualmente identico, ja que o borrao destroi
    # justamente o detalhe que a resolucao carregaria.
    bg_w, bg_h = SHORT_W // 4, SHORT_H // 4
    return [
        f"[{in_label}]split=2[bgsrc][fgsrc]",
        f"[bgsrc]scale={bg_w}:{bg_h}:force_original_aspect_ratio=increase,"
        f"crop={bg_w}:{bg_h},gblur=sigma=12,"
        f"scale={SHORT_W}:{SHORT_H}[bg]",
        f"[fgsrc]scale={SHORT_W}:{SHORT_H}:force_original_aspect_ratio=decrease[fg]",
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[{out_label}]",
    ]
