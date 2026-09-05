# youclone — Documento de Arquitetura e Especificação de Build

> **Como usar este arquivo:** entregue-o ao Claude Code (idealmente renomeado
> para `CLAUDE.md` na raiz do repositório, que ele lê automaticamente). Ele
> contém contexto, decisões travadas e um plano de build em fases. Construa **na
> ordem das fases**, validando o critério de aceite de cada uma antes de seguir.

---

## 1. Objetivo

Aplicação que recebe um vídeo `.mp4` em inglês e devolve o mesmo vídeo
**localizado em português do Brasil**, com **legenda `.srt`** e **dublagem
(voz PT-BR)**. Sem marca d'água, com uso comercial livre (o operador possui
licença dos criadores dos vídeos originais).

O produto começa de uso **pessoal e local** (um único operador, na própria
máquina) e deve ser **estruturado para, no futuro, virar um SaaS multiusuário**
sem reescrever o núcleo. Não construa as camadas de produto agora — apenas deixe
as costuras (ver §12).

## 2. Decisões travadas (não reabrir)

| Decisão | Valor |
|---|---|
| Saída do vídeo final | **Legenda + dublagem**, ambas |
| Escopo v1 | **Single-user, local** (produto multiusuário é fase futura) |
| Ambiente v1 | **PC do operador** (Windows/Mac/Linux), sem cloud |
| Linguagem | **Python** (motor e backend) |
| Backend | **FastAPI** |
| Progresso ao vivo | **SSE** (Server-Sent Events) |
| Frontend | **HTML/JS** simples (evoluível para React sem tocar no backend) |
| Transcrição | **faster-whisper** (local, gratuito) |
| Tradução | **Gemini** via `google-genai` (free tier serve para dev); **provedor plugável** |
| TTS (dublagem) | **Google Cloud TTS** como padrão; **provedor plugável** (Azure / ElevenLabs) |
| Áudio/vídeo | **ffmpeg** (dependência de sistema) |

## 3. Regras de negócio e conformidade (implementar como comportamento padrão)

- **Sem marca d'água** em nenhuma saída.
- **NÃO** implementar "recriação de imagem" (regenerar frames com IA). Isso
  degrada o vídeo e só servia para driblar detecção; com licença, o vídeo
  original é superior. Pode existir como flag opcional desligada por padrão, mas
  não é prioridade e **não** entra nas fases v1.
- **NÃO** implementar lip sync. O conteúdo-alvo é narrado (rosto não é o foco);
  lip sync exige modelo de vídeo pesado e é desnecessário.
- A obtenção do `.mp4` é responsabilidade do operador. Ofereça um passo opcional
  de download via `yt-dlp`, mas o fluxo principal assume um arquivo local já
  presente.
- O README final deve lembrar o operador de **marcar "conteúdo alterado ou
  sintético"** ao subir no YouTube (exigência da plataforma para áudio/voz por
  IA) e de **confirmar direito de uso comercial** do conteúdo-fonte.

## 4. Princípios de arquitetura

1. **Motor isolado da web.** Todo o processamento vive em `engine/` e roda como
   biblioteca Python pura, sem nenhuma dependência de FastAPI. O backend apenas
   **embrulha** o motor. Isso permite usar o mesmo núcleo por CLI, por API ou por
   worker de fila no futuro.
2. **Etapas plugáveis.** Cada etapa do pipeline é um módulo com interface
   estável. Trocar provedor (tradução, TTS) ou reordenar etapas não deve exigir
   mexer no orquestrador.
3. **Progresso por callback.** O pipeline emite progresso por um callback
   `on_progress(step_id, label, pct, message)`. Quem consome (CLI imprime, web
   faz streaming por SSE) decide o que fazer. O motor não conhece HTTP.
4. **Local-first, costurado para escalar.** Use implementações locais simples
   (fila em memória, storage em disco), mas atrás de interfaces que possam ser
   trocadas por Redis/worker e S3/MinIO depois (ver §12).

## 5. Stack e dependências

**Python 3.11+**

Pip (`requirements.txt`):
- `fastapi`, `uvicorn[standard]`, `python-multipart` — backend
- `faster-whisper` — transcrição local
- `google-genai` — tradução (Gemini)
- `google-cloud-texttospeech` — TTS padrão (dublagem)
- `yt-dlp` — download opcional
- `pydantic` — modelos/validação

Sistema (não-pip): **ffmpeg** (documentar instalação por SO no README).

Chaves/segredos por variável de ambiente (nunca no código):
- `GEMINI_API_KEY`
- `GOOGLE_APPLICATION_CREDENTIALS` (para Google Cloud TTS) ou chave equivalente
  do provedor de TTS escolhido.

## 6. Estrutura de diretórios

```
youclone/
├── engine/                 # MOTOR — Python puro, sem web
│   ├── __init__.py
│   ├── config.py           # configuração via env
│   ├── models.py           # dataclasses (Segment, PipelineResult)
│   ├── pipeline.py         # orquestra as etapas + emite progresso
│   ├── providers/          # implementações plugáveis
│   │   ├── translate_gemini.py
│   │   ├── tts_google.py
│   │   ├── tts_base.py     # interface abstrata de TTS
│   │   └── translate_base.py
│   └── steps/
│       ├── download.py     # (1) yt-dlp — opcional
│       ├── audio.py        # (2) extrair áudio (ffmpeg)
│       ├── transcribe.py   # (3) faster-whisper (com timestamps)
│       ├── translate.py    # (4) chama provider de tradução
│       ├── subtitle.py     # (5) gerar .srt
│       ├── dub.py          # (6) TTS + encaixe temporal
│       └── render.py       # (7) remontar com ffmpeg (sub + áudio dublado)
├── api/                    # BACKEND (FastAPI)
│   ├── __init__.py
│   ├── main.py             # endpoints
│   ├── jobs.py             # store de jobs (memória) — interface p/ trocar
│   └── schemas.py          # modelos de request/response
├── web/
│   └── index.html          # frontend de teste (upload + barra + downloads)
├── cli.py                  # rodar o motor pelo terminal, sem web
├── storage/                # uploads / outputs / work (gitignored)
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

## 7. Especificação do motor (`engine/`)

### 7.1 Modelos (`models.py`)

```python
@dataclass
class Segment:
    start: float          # segundos
    end: float            # segundos
    text: str             # texto original (EN)
    translation: str = "" # PT-BR (preenchido na etapa de tradução)

@dataclass
class PipelineResult:
    segments: list[Segment]
    srt_path: Path | None = None      # legenda PT-BR
    dub_audio_path: Path | None = None # trilha dublada
    video_out: Path | None = None      # mp4 final (legenda + dublagem)
```

### 7.2 Etapas (`steps/`) — interfaces

- `download.download(url: str, out_dir: Path) -> Path` — opcional, via yt-dlp.
- `audio.extract_audio(video_path: Path, work_dir: Path) -> Path` — `.wav`
  16 kHz mono via ffmpeg.
- `transcribe.transcribe(audio_path: Path) -> list[Segment]` — faster-whisper,
  `vad_filter=True`, idioma origem de `config.SOURCE_LANG`.
- `translate.translate_segments(segments: list[Segment]) -> list[Segment]` —
  delega ao provider ativo; traduz **em lote** (uma chamada com todas as falas
  numeradas, resposta em JSON indexado para garantir ordem). Preenche
  `seg.translation`.
- `subtitle.build_srt(segments, out_path, translated=True) -> Path` — gera `.srt`
  padrão (índice, `HH:MM:SS,mmm --> ...`, texto, linha em branco).
- `dub.synthesize_dub(segments, work_dir) -> Path` — ver §7.4.
- `render.build_final(video, srt, dub_audio, out_path, opts) -> Path` — ver §7.5.

### 7.3 Providers plugáveis (`providers/`)

Interface de tradução (`translate_base.py`):
```python
class Translator(Protocol):
    def translate_batch(self, texts: list[str]) -> list[str]: ...
```
Interface de TTS (`tts_base.py`):
```python
class TTS(Protocol):
    def synthesize(self, text: str, voice: str) -> bytes: ...  # WAV/MP3 bytes
```
Seleção do provider por `config.TRANSLATE_PROVIDER` / `config.TTS_PROVIDER`
(factory simples que devolve a implementação certa). Padrões: Gemini e Google TTS.

### 7.4 Dublagem — o ponto técnico mais delicado (`dub.py`)

O problema central: **a fala em PT-BR costuma ser mais longa que em EN**, então o
áudio dublado pode "estourar" o tempo do trecho. Resolver assim:

1. Para cada `Segment`, gerar áudio TTS do `seg.translation`.
2. Calcular a duração-alvo `dur = seg.end - seg.start`.
3. **Encaixar no tempo:**
   - Se o clipe TTS > `dur`: acelerar com ffmpeg `atempo` (limite ~1.3x para não
     ficar robótico; se passar disso, permitir leve invasão do próximo gap).
   - Se o clipe TTS < `dur`: preencher com silêncio no fim.
4. **Montar a trilha completa:** criar uma faixa silenciosa do tamanho total do
   vídeo e posicionar cada clipe no seu `seg.start` (via `adelay`/`amix` ou
   concatenação com gaps de silêncio calculados). Resultado: um único `.wav` do
   comprimento do vídeo, com cada fala no lugar certo.
5. Devolver o caminho dessa trilha dublada.

Config relevante: `DUB_VOICE` (voz PT-BR do provedor), `DUB_MAX_SPEEDUP` (ex. 1.3),
`DUB_KEEP_MUSIC` (bool — ver render).

### 7.5 Remontagem final (`render.py`)

`build_final` deve produzir **um mp4** combinando:
- vídeo original (imagem intacta),
- **áudio dublado** substituindo a voz original;
  - se `DUB_KEEP_MUSIC=True`, **mixar** a trilha dublada com o áudio original
    rebaixado (ducking, ex. -18 dB) para manter música/ambiência de fundo;
  - se `False`, substituir o áudio inteiro pela trilha dublada.
- **legenda** queimada (hardsub via filtro `subtitles=`) OU anexada como faixa
  soft — expor via opção `BURN_SUBS` (padrão: queimar, para garantir exibição no
  feed do YouTube/Shorts).

Funções auxiliares já úteis hoje: `burn_subtitles(video, srt, out)` e uma para
mixagem de áudio. Todas via `subprocess` chamando ffmpeg, com `check=True`.

### 7.6 Orquestrador (`pipeline.py`)

```python
def run(video_path: Path,
        on_progress: Callable[[str,str,int,str], None] = noop,
        make_subs: bool = True,
        make_dub: bool = True) -> PipelineResult:
    # ordem: audio → transcribe → translate → subtitle → dub → render(final)
    # emitir on_progress em cada etapa com pct crescente e mensagem clara em PT
```
O orquestrador **não** conhece HTTP nem jobs — só o callback.

## 8. Backend (`api/`)

Endpoints:
- `POST /api/jobs` (multipart, campo `file`): salva o upload em
  `storage/uploads/`, cria um Job, dispara o processamento em background,
  retorna `{ "id": <job_id> }`.
- `GET /api/jobs/{id}`: estado atual `{ status, pct, step, message, video, srt }`.
- `GET /api/jobs/{id}/events`: **SSE**. Faz streaming dos eventos de progresso
  (`data: {step,pct,message}\n\n`) e um evento final `{done:true,status}`.
- `GET /api/download/{filename}`: serve arquivos de `storage/outputs/`.
- `GET /`: serve `web/index.html`.

Job model (`jobs.py`), v1 em memória, **atrás de interface trocável**:
```python
@dataclass
class Job:
    id: str; video_path: Path
    status: str = "queued"   # queued|running|done|error
    pct: int = 0; step: str = ""; message: str = ""
    result_video: str = ""; result_srt: str = ""
    events: Queue = ...      # fila de progresso p/ o SSE
def create(video_path) -> Job   # cria + dispara thread/worker
def get(job_id) -> Job | None
```
O runner injeta um `on_progress` que faz `job.events.put({...})` e atualiza os
campos. Ao terminar, coloca `None` na fila para fechar o SSE.

## 9. Frontend (`web/index.html`)

Página única, tema escuro, mobile-friendly: seletor de arquivo `.mp4`, botão
"Traduzir e legendar", barra de progresso alimentada por `EventSource` no
endpoint SSE, e, ao concluir, links para baixar `.mp4` e `.srt`. Sem framework em
v1. Deve ser trivial de trocar por React depois (o backend não muda).

## 10. CLI (`cli.py`)

`python cli.py caminho/do/video.mp4 [--no-dub] [--no-subs]` — roda o motor
imprimindo o progresso no terminal. Serve para testar o núcleo sem subir a web.

## 11. Configuração (`config.py` + `.env.example`)

Tudo por env, com defaults sensatos: pastas de storage; `SOURCE_LANG=en`,
`TARGET_LANG=pt`; `WHISPER_MODEL=small`, device/compute; `TRANSLATE_PROVIDER`,
`GEMINI_MODEL`, `TRANSLATE_STYLE` (prompt de estilo de tradução — natural, PT-BR,
mantém nomes próprios, adapta gírias); `TTS_PROVIDER`, `DUB_VOICE`,
`DUB_MAX_SPEEDUP`, `DUB_KEEP_MUSIC`, `BURN_SUBS`. Gerar `.env.example` com todas.

## 12. Costuras para o futuro (deixar prontas, NÃO implementar agora)

Implementar cada item abaixo como uma **interface/abstração fina** com a versão
local, de modo que a versão escalável seja um plug posterior:

- **Fila de jobs:** hoje thread + fila em memória; interface permite trocar por
  **Redis + worker (Celery/RQ)** sem tocar nos endpoints.
- **Storage:** hoje sistema de arquivos local via um pequeno módulo de paths;
  depois trocável por **S3/MinIO**.
- **Persistência de jobs:** v1 em memória; deixar ponto para **SQLite/Postgres**.
- **Auth/contas:** v1 sem login; manter endpoints organizados para inserir
  middleware de autenticação e associação job→usuário depois.
- **Multiusuário/cobrança:** fora de escopo; não criar, apenas não impedir.

## 13. Plano de build em fases (ordem de execução para o Claude Code)

Construir e validar **uma fase por vez**.

**Fase 0 — Scaffold**
Criar estrutura de diretórios, `requirements.txt`, `.env.example`, `.gitignore`,
`config.py`, `models.py`. Critério: `python -c "import engine"` sem erro.

**Fase 1 — Motor: legenda**
Implementar `audio` → `transcribe` → `translate` → `subtitle` e o `pipeline.run`
até o `.srt`. Implementar `cli.py`. Critério: rodar `python cli.py sample.mp4`
gera um `.srt` PT-BR sincronizado.

**Fase 2 — Motor: dublagem + render final**
Implementar `providers/tts_google`, `dub.synthesize_dub` (com encaixe temporal
da §7.4) e `render.build_final` (mix/substituição de áudio + hardsub). Critério:
o CLI gera um `.mp4` final legendado **e** dublado, com falas no tempo certo.

**Fase 3 — Backend + progresso**
Implementar `api/` (upload, status, SSE, download) e o job store em memória.
Critério: `uvicorn api.main:app` sobe; `POST /api/jobs` processa e o SSE
transmite a barra até 100%.

**Fase 4 — Frontend**
Implementar `web/index.html` (upload + barra via EventSource + downloads).
Critério: fluxo completo pelo navegador em `localhost:8000`.

**Fase 5 — Polimento**
README completo (instalação de ffmpeg por SO, obtenção de chaves, aviso de
disclosure de IA no YouTube), tratamento de erros amigável, logs. Critério:
alguém novo consegue instalar e rodar só pelo README.

## 14. Fora de escopo (v1)

Recriação de frames por IA; lip sync; login/multiusuário; cobrança; deploy em
cloud; fila distribuída. (Todos previstos como evolução — ver §12.)

## 15. Critérios de qualidade

- Motor 100% independente da web (nenhum `import fastapi` em `engine/`).
- Todas as chamadas a ffmpeg com `check=True` e captura de erro clara.
- Nenhum segredo hardcoded; tudo por env.
- Mensagens de progresso e erros em **português**, claras para o operador.
- Código comentado nos pontos não óbvios (encaixe temporal da dublagem, mix de
  áudio, formato SRT).
