"""Lista de idiomas suportados -- fonte unica usada pela faixa de idiomas da
Tela 1 e pelos seletores de idioma da Tela 2 (via GET /api/languages), para
nunca ficar hardcoded no template/JS (ver especificacao da Tela 1).

Codigos ISO 639-1, os mesmos que o faster-whisper espera para transcricao
(engine/steps/transcribe.py, worker/) -- todos aqui sao idiomas que o
Whisper de fato reconhece como "idioma do video".
"""

LANGUAGES = [
    {"code": "pt", "name": "Português"},
    {"code": "en", "name": "English"},
    {"code": "es", "name": "Español"},
    {"code": "fr", "name": "Français"},
    {"code": "de", "name": "Deutsch"},
    {"code": "it", "name": "Italiano"},
    {"code": "ja", "name": "日本語"},
    {"code": "nl", "name": "Nederlands"},
    {"code": "pl", "name": "Polski"},
    {"code": "ko", "name": "한국어"},
    {"code": "tr", "name": "Türkçe"},
    {"code": "sv", "name": "Svenska"},
    {"code": "zh", "name": "中文"},
    {"code": "no", "name": "Norsk"},
    {"code": "fi", "name": "Suomi"},
    {"code": "da", "name": "Dansk"},
    {"code": "cs", "name": "Čeština"},
    {"code": "ro", "name": "Română"},
    {"code": "hu", "name": "Magyar"},
    {"code": "el", "name": "Ελληνικά"},
    {"code": "uk", "name": "Українська"},
    {"code": "ru", "name": "Русский"},
    {"code": "id", "name": "Bahasa Indonesia"},
    {"code": "vi", "name": "Tiếng Việt"},
]

# Traducao de saida (prompt do Gemini + voz do Polly) so esta pronta para
# PT-BR hoje -- ver CLAUDE.md "Locked decisions". Os demais idiomas aparecem
# na Tela 2 como opcao de "Traduzir para", mas desabilitados ("em breve").
TARGET_ENABLED = {"pt"}


def languages_payload() -> list[dict]:
    return [
        {**lang, "enabled_as_target": lang["code"] in TARGET_ENABLED}
        for lang in LANGUAGES
    ]
