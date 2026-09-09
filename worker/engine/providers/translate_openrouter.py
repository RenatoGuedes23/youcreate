"""Provider de traducao via OpenRouter (gateway para varios modelos de LLM).

Unico provider de traducao do projeto hoje -- Gemini (google-genai, cota
diaria de free tier esgotava rapido) e Amazon Translate (traducao literal,
sem entender contexto -- "long trunks" virava "baus longos" em vez de
"trombas compridas") foram implementados e removidos, nessa ordem. A
chamada e um POST HTTP simples no endpoint "chat completions", compativel
com o formato da OpenAI -- e assim que o OpenRouter expoe qualquer modelo
por tras da mesma API, entao nao ha necessidade de um SDK proprio. O
modelo de fato usado (OPENROUTER_MODEL, hoje "deepseek/deepseek-v4-flash")
e configuravel via .env, sem tocar em codigo.

Sem nenhum parametro "provider" explicito no payload, a OpenRouter usa seu
roteamento padrao entre os varios provedores que hospedam o mesmo modelo
(DeepInfra, OpenInference, Wafer, etc.): prioriza os estaveis (sem quedas
recentes) e, entre esses, pondera fortemente pelos mais baratos, com
fallback automatico se o preferido falhar -- decisao deliberada de nao
travar num provedor especifico (ver docs/ARQUITETURA.md), que eliminaria
esse fallback e criaria um ponto unico de falha.

Traduz tudo numa unica chamada (todas as falas numeradas no prompt,
resposta em JSON), preservando a ordem sem risco de desalinhamento.
Pedimos um objeto JSON com a chave "translations" (nao um array solto na
raiz), porque "response_format: json_object" -- o jeito mais amplamente
suportado entre modelos variados de pedir JSON -- geralmente exige um
objeto no topo, nao um array.

Deteccao de eco: em testes reais, modelos baratos (deepseek-v4-flash-0731
incluido) as vezes devolvem HTTP 200 com um JSON valido, mas simplesmente
ecoam o texto original em vez de traduzir -- falha silenciosa, sem erro
de rede pra pegar no retry de `_call_with_retry`. Provavel causa: o
"reasoning" desligado (ver comentario no payload abaixo, feito pra cortar
custo) tira do modelo o espaco de "pensar" antes de responder, e num lote
grande de linhas numeradas ele toma o atalho de copiar o input em vez de
falhar. `translate_batch` mede a fracao de falas identicas ao original
apos cada chamada e, se passar de `_ECHO_THRESHOLD`, trata como falha e
tenta de novo (nova chamada, que tende a cair em outro provedor upstream
da OpenRouter).

Traducao com nocao de duracao: dublagem profissional (filme/série) nao
traduz "as cegas" -- um adaptador reescreve a fala ja pensando em quanto
tempo ela ocupa quando falada, pra caber no tempo de tela antes mesmo de
gravar. Fazemos o analogo aqui: cada linha do prompt vem anotada com a
duracao original e um orcamento de palavras (calculado a partir de um
ritmo natural de fala em PT-BR, ver _WORDS_PER_SECOND), pedindo pro
modelo ajustar a frase (reescrever, cortar redundancia) pra chegar perto
desse tamanho. Isso ataca a causa, nao so o sintoma, do descompasso que
`dub.py` tentava consertar sozinho por forca bruta (acelerar audio ou
preencher com silencio) -- um silencio de 3.5s dentro do slot de uma fala
foi o caso real observado que motivou essa mudanca.
"""
import json
import logging
import re
import time

import requests

from engine import config

logger = logging.getLogger(__name__)

_API_URL = "https://openrouter.ai/api/v1/chat/completions"
_MAX_RETRIES = 3
_BACKOFF_SECONDS = (2, 5, 10)

# Quantas vezes tentar a chamada inteira de novo quando o resultado "parece"
# eco (ver docstring do modulo). Separado de _MAX_RETRIES, que e so pro
# nivel HTTP (429/5xx) dentro de uma unica chamada.
_MAX_ECHO_ATTEMPTS = 3
# Fracao de falas identicas ao original acima da qual tratamos a resposta
# como eco em vez de traducao real. Nao e 100% porque algumas falas curtas
# (nomes proprios, numeros, "OK") legitimamente nao mudam ao traduzir --
# mas a maioria do lote vir identica indica que o modelo nao traduziu.
_ECHO_THRESHOLD = 0.5

# Ritmo de fala natural em PT-BR usado pra calcular o orcamento de palavras
# de cada linha (~150 palavras/minuto -- narracao/entrevista falada em ritmo
# normal, nem apressada nem arrastada). E so uma sugestao pro modelo, nao
# um limite rigido: o texto final ainda passa pelo encaixe temporal em
# dub.py (acelera ate DUB_MAX_SPEEDUP ou preenche com silencio), que
# continua existindo como rede de seguranca pros casos em que a sugestao
# nao for seguida a risca.
_WORDS_PER_SECOND = 2.5


def _word_budget(duration: float) -> int:
    return max(1, round(duration * _WORDS_PER_SECOND))

# Chaves alternativas que modelos usam na pratica em vez de "translations"
# (chave raiz do objeto) e "tgt"/"target"/etc. (quando cada item vem como um
# dict {src, tgt} em vez de uma string pura) -- pedimos um formato exato no
# prompt, mas nem todo modelo segue a risca, entao o parsing tenta essas
# variacoes antes de desistir.
_LIST_KEYS = ("translations", "translation", "results", "output")
_ITEM_TEXT_KEYS = ("tgt", "target", "translation", "text", "pt", "translated")


def _extract_translations(data: dict) -> list[str]:
    if not isinstance(data, dict):
        raise ValueError(f"esperava um objeto JSON, recebeu {data!r}")

    raw_list = next((data[key] for key in _LIST_KEYS if key in data), None)
    if raw_list is None:
        raise ValueError(f"nenhuma chave de lista conhecida em {list(data.keys())!r}")
    if not isinstance(raw_list, list):
        raise ValueError(f"esperava uma lista, recebeu {raw_list!r}")

    result = []
    for item in raw_list:
        if isinstance(item, str):
            text = item
        elif isinstance(item, dict):
            text = next((item[key] for key in _ITEM_TEXT_KEYS if key in item), None)
            if text is None:
                raise ValueError(f"item de traducao em formato desconhecido: {item!r}")
            text = str(text)
        else:
            raise ValueError(f"item de traducao em formato desconhecido: {item!r}")
        # Alguns modelos ecoam o prefixo "N: " (do formato numerado que
        # mandamos no prompt) de volta na propria traducao, quando usam um
        # formato {src, tgt} em vez do array de strings pedido -- remove
        # esse prefixo se aparecer, pra nao vazar "0:", "1:" na legenda.
        result.append(re.sub(r"^\d+:\s*", "", text))
    return result


def _echo_ratio(originals: list[str], translations: list[str]) -> float:
    """Fracao de falas devolvidas identicas ao original (ver _ECHO_THRESHOLD)."""
    if not originals:
        return 0.0
    matches = sum(
        1 for original, translated in zip(originals, translations)
        if original.strip().casefold() == translated.strip().casefold()
    )
    return matches / len(originals)


class OpenRouterTranslator:
    def __init__(self):
        if not config.OPENROUTER_API_KEY:
            raise RuntimeError(
                "OPENROUTER_API_KEY nao configurada no .env deste projeto."
            )
        if not config.OPENROUTER_MODEL:
            raise RuntimeError(
                "OPENROUTER_MODEL nao configurado no .env deste projeto "
                "(ex: 'deepseek/deepseek-v4-flash-0731', 'google/gemini-3.8-flash')."
            )

    def translate_batch(
        self, texts: list[str], durations: list[float] | None = None
    ) -> list[str]:
        """Traduz todas as falas em uma unica chamada, preservando a ordem."""
        if not texts:
            return []

        if durations is None:
            durations = [0.0] * len(texts)

        lines = []
        for i, (text, duration) in enumerate(zip(texts, durations)):
            if duration > 0:
                budget = _word_budget(duration)
                lines.append(f"{i} (~{budget} palavras, {duration:.1f}s de fala): {text}")
            else:
                lines.append(f"{i}: {text}")
        numbered = "\n".join(lines)

        prompt = (
            f"{config.TRANSLATE_STYLE}\n\n"
            f"Traduza cada linha numerada abaixo para {config.TARGET_LANG}. "
            f"Cada linha mostra entre parenteses a duracao da fala original "
            f"e uma sugestao de numero de palavras pra traducao caber nesse "
            f"tempo, num ritmo natural de fala. Ajuste a frase (reescreva, "
            f"corte redundancia, use sinonimos mais curtos) pra chegar perto "
            f"desse numero de palavras sempre que possivel, sem perder o "
            f"sentido -- evite tanto traducoes bem mais longas quanto bem "
            f"mais curtas que a sugestao. "
            f"Devolva um objeto JSON no formato "
            f'{{"translations": ["...", "..."]}}, com exatamente '
            f"{len(texts)} itens no array, na mesma ordem das linhas, sem "
            f"os numeros ou as anotacoes entre parenteses, contendo apenas "
            f"a traducao de cada linha.\n\n{numbered}"
        )

        last_error: Exception = RuntimeError("Falha desconhecida ao traduzir via OpenRouter.")
        for attempt in range(_MAX_ECHO_ATTEMPTS):
            content = self._call_with_retry(prompt)

            try:
                data = json.loads(content)
                translations = _extract_translations(data)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                last_error = RuntimeError(
                    f"Resposta do OpenRouter (modelo {config.OPENROUTER_MODEL}) nao "
                    f"veio no formato esperado: {content!r}"
                )
                logger.warning(
                    "OpenRouter (%s): resposta mal formatada na tentativa %d/%d "
                    "(%s), tentando de novo.",
                    config.OPENROUTER_MODEL, attempt + 1, _MAX_ECHO_ATTEMPTS, exc,
                )
                continue

            if not isinstance(translations, list) or len(translations) != len(texts):
                last_error = RuntimeError(
                    "Resposta de traducao do OpenRouter invalida: esperava uma "
                    f"lista com {len(texts)} itens, recebeu {translations!r}."
                )
                logger.warning(
                    "OpenRouter (%s): quantidade de itens incorreta na tentativa "
                    "%d/%d, tentando de novo.",
                    config.OPENROUTER_MODEL, attempt + 1, _MAX_ECHO_ATTEMPTS,
                )
                continue

            ratio = _echo_ratio(texts, translations)
            if ratio > _ECHO_THRESHOLD:
                last_error = RuntimeError(
                    f"OpenRouter (modelo {config.OPENROUTER_MODEL}) devolveu o "
                    f"texto original sem traduzir em {ratio:.0%} das falas, "
                    f"mesmo apos {_MAX_ECHO_ATTEMPTS} tentativas."
                )
                logger.warning(
                    "OpenRouter (%s): %.0f%% das falas vieram identicas ao "
                    "original (provavel eco em vez de traducao) na tentativa "
                    "%d/%d, tentando de novo.",
                    config.OPENROUTER_MODEL, ratio * 100, attempt + 1, _MAX_ECHO_ATTEMPTS,
                )
                continue

            return translations

        raise last_error

    def _call_with_retry(self, prompt: str) -> str:
        headers = {
            "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": config.OPENROUTER_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
            # Alguns modelos/provedores ligam "reasoning" (pensamento interno
            # antes da resposta) por padrao mesmo sem pedir -- medido na
            # pratica: ~7x mais tokens de saida (e de custo) numa traducao
            # simples, sem nenhum ganho de qualidade pra essa tarefa. Tradu-
            # cao nao precisa de raciocinio passo a passo, entao desligamos
            # explicitamente.
            "reasoning": {"enabled": False},
            # Pede o custo real (em USD) da chamada de volta na resposta --
            # usado so para log/transparencia de quanto cada job realmente
            # gastou (o preco "de tabela" do modelo pode nao bater com o
            # cobrado de fato, que depende de qual provedor upstream a
            # OpenRouter escolheu para atender aquela chamada especifica).
            "usage": {"include": True},
        }

        for attempt in range(_MAX_RETRIES + 1):
            response = requests.post(_API_URL, headers=headers, json=payload, timeout=120)
            if response.status_code == 200:
                data = response.json()
                usage = data.get("usage") or {}
                logger.info(
                    "OpenRouter (%s via %s): %s tokens (%s reasoning), custo real US$ %s",
                    config.OPENROUTER_MODEL, data.get("provider", "?"),
                    usage.get("total_tokens", "?"), usage.get("completion_tokens_details", {}).get("reasoning_tokens", "?"),
                    usage.get("cost", "?"),
                )
                return data["choices"][0]["message"]["content"]

            retryable = response.status_code == 429 or response.status_code >= 500
            if not retryable or attempt == _MAX_RETRIES:
                raise RuntimeError(
                    f"Falha ao chamar o OpenRouter (modelo {config.OPENROUTER_MODEL}): "
                    f"{response.status_code} {response.text}"
                )
            wait = _BACKOFF_SECONDS[attempt]
            logger.warning(
                "OpenRouter respondeu %d, tentativa %d/%d, aguardando %ds antes de tentar de novo.",
                response.status_code, attempt + 1, _MAX_RETRIES, wait,
            )
            time.sleep(wait)

        raise AssertionError("inalcancavel")  # o loop sempre retorna ou levanta antes de sair
