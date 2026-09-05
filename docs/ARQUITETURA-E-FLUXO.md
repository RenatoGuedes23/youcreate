# youcreate — Arquitetura e fluxo completo

Este documento explica, em detalhe, como o youcreate foi construído (fase por
fase) e como uma requisição real percorre o sistema, do upload do vídeo até o
download do resultado. É um complemento ao `CLAUDE.md` (guia para o Claude
Code operar neste repositório) e ao `README.md` (guia de instalação/uso) —
aqui o foco é **entender o funcionamento interno**.

## Princípios da arquitetura

Três decisões estruturais guiam todo o projeto:

1. **Motor isolado da web** (`engine/`) — nenhum arquivo em `engine/` importa
   FastAPI. O motor é Python puro, chamável tanto pelo `cli.py` quanto pela
   `api/`.
2. **Progresso por callback** — o motor não sabe o que é HTTP. Ele só chama
   `on_progress(step_id, label, pct, message)` a cada etapa; quem chamou
   decide o que fazer com isso (o CLI imprime no terminal, a API manda por
   SSE).
3. **Etapas plugáveis** — tradução e TTS são "providers" atrás de uma
   interface (`Protocol`), trocáveis via variável de ambiente sem tocar no
   orquestrador.

### Modelos de dados (`engine/models.py`)

Duas dataclasses simples carregam tudo que passa pelo pipeline:

```python
@dataclass
class Segment:
    start: float          # segundos
    end: float             # segundos
    text: str              # texto original (EN)
    translation: str = ""  # PT-BR (preenchido na etapa de traducao)

@dataclass
class PipelineResult:
    segments: list[Segment] = field(default_factory=list)
    srt_path: Path | None = None       # legenda PT-BR
    dub_audio_path: Path | None = None # trilha dublada
    video_out: Path | None = None      # mp4 final (legenda + dublagem)
```

---

## Fase 0 — Scaffold

Estrutura de pastas (`engine/`, `api/`, `web/`, `storage/`), `requirements.txt`,
`.gitignore`, `engine/config.py` (lê tudo de variáveis de ambiente via `.env`,
com defaults sensatos, e cria as pastas de `storage/` automaticamente) e
`engine/models.py`.

Critério de aceite: `python -c "import engine"` sem erro — confirma que o
motor não depende de nada da web.

---

## Fase 1 — Motor de legenda

Cadeia: **áudio → transcrição → tradução → legenda**.

### `engine/steps/audio.py` — `extract_audio()`

Chama o ffmpeg para extrair só o áudio do vídeo, convertendo para `.wav`
mono 16kHz (formato que o Whisper espera):

```
ffmpeg -i video.mp4 -vn -ac 1 -ar 16000 -acodec pcm_s16le audio.wav
```

### `engine/steps/transcribe.py` — `transcribe()`

Usa o **faster-whisper**, rodando 100% local na CPU — o áudio nunca sai da
máquina nesta etapa. O modelo é carregado uma vez e cacheado num módulo
global (`_model`), evitando recarregar do disco a cada chamada.
`vad_filter=True` ativa detecção de atividade de voz, para o Whisper ignorar
trechos de silêncio em vez de "alucinar" texto neles. Resultado: uma lista de
`Segment` com timestamps e texto em inglês.

### `engine/providers/translate_gemini.py` — `translate_batch()`

O ponto mais interessante da tradução: em vez de chamar o Gemini uma vez por
frase, monta **um prompt único** com todas as falas numeradas
(`"0: Hello there\n1: How are you..."`) e pede para o modelo devolver um
**array JSON na mesma ordem**, forçado via `response_schema=list[str]`. Isso
garante que a tradução do índice 47 corresponde exatamente à fala 47, sem
risco de desalinhamento, e custa uma única chamada de API por vídeo,
independente de quantas falas existam.

`engine/steps/translate.py` é o encaixe: extrai os textos dos `Segment`,
chama `translate_batch()`, escreve o resultado de volta em `seg.translation`.
A seleção de provider olha `config.TRANSLATE_PROVIDER` (hoje só `"gemini"``)
— trocar por outro é implementar a mesma interface (`translate_base.py`) e
adicionar um `if` na fábrica.

### `engine/steps/subtitle.py` — `build_srt()`

Monta o `.srt` no formato padrão (índice sequencial, timestamp
`HH:MM:SS,mmm --> HH:MM:SS,mmm`, texto, linha em branco), sem dependências
externas.

Critério de aceite: `python cli.py video.mp4 --no-dub` gera um `.srt` PT-BR
sincronizado.

---

## Fase 2 — Dublagem e renderização final

O problema central da dublagem: **fala em PT-BR costuma ser mais longa que
em inglês**, então o áudio dublado de uma frase pode não caber no intervalo
de tempo que a frase original ocupava.

### `engine/steps/dub.py` — `synthesize_dub()`

Para cada segmento (loop principal):

1. **Sintetiza** a tradução em áudio bruto via o provider de TTS ativo
   (`provider.synthesize`), sem se preocupar ainda com duração.
2. **Encaixa no tempo** (`_fit_segment_clip`): compara a duração do clipe
   gerado com `target_dur = seg.end - seg.start` (o tempo que a fala
   original ocupava).
   - Se o clipe ficou **mais longo**: acelera com o filtro `atempo` do
     ffmpeg, limitado a `DUB_MAX_SPEEDUP` (1.3x por padrão) — além disso
     soaria robótico, então prefere deixar vazar um pouco para o próximo
     trecho a distorcer a voz.
   - Se ficou **mais curto**: preenche o resto com silêncio (`apad`).
3. Guarda **onde** (`seg.start`) cada clipe ajustado deve tocar.

Depois de processar todos, `_mix_track` monta a trilha final: cria uma faixa
de silêncio do tamanho do vídeo inteiro (`anullsrc`), usa o filtro `adelay`
do ffmpeg para posicionar cada clipe no seu tempo absoluto (`seg.start` em
milissegundos), e soma tudo com `amix`. Cada fala é posicionada
**independentemente** pelo tempo absoluto, não por concatenação sequencial —
assim, se uma fala vazar um pouco para o próximo trecho (passo 2), isso não
desalinha as falas seguintes, só sobrepõe um pouco naquele ponto específico.

### `engine/providers/tts_polly.py` — Amazon Polly

Chama a API do Polly pedindo áudio em PCM cru (sem cabeçalho de arquivo),
porque é o único formato onde o Polly permite 16kHz (mp3/ogg aceitam outras
taxas, mas PCM só aceita 8000 ou 16000). Como o PCM vem "nu", o código monta
um cabeçalho `.wav` válido usando o módulo `wave` da biblioteca padrão do
Python (`_pcm_to_wav`), para ficar no mesmo formato que o resto do pipeline
espera.

**Credenciais**: só aceita `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` +
`AWS_DEFAULT_REGION` explícitos do `.env` deste projeto — nunca a cadeia
padrão de credenciais do boto3. Ver seção "O incidente de segurança" abaixo
para o porquê.

### `engine/steps/render.py` — `build_final()`

Junta tudo num `.mp4` final. Duas opções configuráveis:

- **`keep_music`**: se `True`, mixa a trilha dublada com o áudio original
  abaixado (-18dB, "ducking") para manter música/ambiente de fundo. Se
  `False`, substitui o áudio inteiro.
- **`burn_subs`**: se `True`, "queima" a legenda nos pixels do vídeo (filtro
  `subtitles=`, exige recodificar com `libx264` — mais lento, mas garante
  exibição em qualquer player/feed). Se `False`, anexa como faixa de legenda
  separada (`mov_text`, sem recodificar vídeo).

Detalhe importante: `apad=whole_dur={video_duration}` garante que o áudio
final sempre tenha exatamente a duração do vídeo original, mesmo que a
trilha dublada termine antes da última fala — sem isso, o vídeo continuaria
mudo no final.

Critério de aceite: o CLI gera um `.mp4` legendado **e** dublado, com falas
no tempo certo.

---

## Fase 3 — Backend (FastAPI)

### `api/jobs.py` — store de jobs em memória

`create()` gera um `Job` com um UUID, guarda num dicionário em memória
(`_jobs`), e dispara `_run_job` numa **thread separada** (`daemon=True`) —
isso permite o `POST /api/jobs` responder instantaneamente com o ID, sem o
navegador ficar esperando os minutos de processamento.

Dentro da thread, o callback `on_progress` faz duas coisas a cada chamada do
pipeline: atualiza os campos do `Job` (para quem perguntar "qual o status
agora?") e coloca um evento numa `Queue` (para quem estiver "ouvindo" em
tempo real via SSE). Ao terminar — sucesso ou erro — sempre coloca `None` na
fila como sinal de "acabou, pode fechar a conexão".

### `api/main.py` — os 4 endpoints

- **`POST /api/jobs`**: recebe o arquivo, salva com nome único (`uuid + nome
  original`, evita colisão e path traversal), opcionalmente recorta
  (`trim_video`, se vieram `start`/`clip_duration`), entrega para
  `jobs.create()`. Devolve só o ID.
- **`GET /api/jobs/{id}`**: snapshot do estado atual do job.
- **`GET /api/jobs/{id}/events`**: o endpoint SSE. Fica num loop lendo da
  mesma `Queue` que `_run_job` alimenta; a cada item, formata como
  `data: {...}\n\n` (formato que o `EventSource` do navegador entende
  nativamente). Ao receber o `None` (fim), manda um último evento
  `{done: true, status: ...}` e encerra o loop, fechando a conexão HTTP.
- **`GET /api/download/{filename}`**: serve o arquivo final de
  `storage/outputs/`.

Critério de aceite: `uvicorn api.main:app` sobe; `POST /api/jobs` processa e
o SSE transmite a barra até 100%.

---

## Fase 4 — Frontend (`web/index.html`)

Uma página só, sem framework, tema escuro, mobile-friendly.

### Detecção de duração e recorte

Ao escolher um arquivo, o JS cria um `<video>` invisível apontando para o
arquivo local (`URL.createObjectURL`) — isso lê os metadados **sem enviar
nada ao servidor**. Se a duração passar de 60s, mostra a barra de recorte.
`setClipStart()` faz três coisas ao mesmo tempo: recalcula a posição/largura
visual da faixa azul, atualiza os textos ("00:30 – 01:30"), e faz
`previewVideo.currentTime = clipStart` — o vídeo de preview pula para o
frame exato daquele ponto, dando feedback visual imediato de onde o recorte
vai começar. O arraste usa Pointer Events para mover a faixa com o mouse.

### Envio e escuta

No clique do botão: monta um `FormData` com o arquivo (+ `start`/
`clip_duration` se houver recorte), faz o `POST`, pega o `jobId` de volta, e
abre um `EventSource` apontando para o endpoint SSE. `source.onmessage` roda
a cada evento — atualiza a barra até receber `{done: true}`, então decide
entre mostrar os links de download ou a mensagem de erro.

Critério de aceite: fluxo completo pelo navegador em `localhost:8000`,
incluindo o recorte para vídeos maiores que 1 minuto.

---

## Fase 5 — Polimento

- **`logging_config.py`**: configura logging em console + arquivo, usado
  tanto pelo `cli.py` (`youcreate.log` na raiz) quanto pela API
  (`storage/youcreate.log`).
- **`PipelineError`** (`engine/pipeline.py`): se qualquer etapa falhar, o
  pipeline não perde o que já foi construído — embrulha a exceção original
  numa `PipelineError` carregando o `PipelineResult` parcial. Assim, se a
  legenda já tinha sido gerada e só a dublagem falhou depois, quem chamou
  ainda consegue recuperar o `.srt` pronto em vez de perdê-lo.
- **Checagem de ffmpeg no startup** (`api/main.py`): avisa no log se o
  ffmpeg não estiver instalado, em vez de falhar de forma confusa no meio do
  primeiro job.
- **README.md completo**: instalação do ffmpeg por SO, obtenção de chaves
  (Gemini, AWS/Polly), aviso de disclosure de IA no YouTube.

---

## O incidente de segurança (Amazon Polly)

Ao adicionar o Amazon Polly como provider de TTS, o primeiro teste chamou
`boto3.client("polly")` sem credenciais explícitas — isso caiu na cadeia
padrão de credenciais do boto3, que usou, sem intenção, um perfil AWS já
configurado na máquina (`~/.aws/credentials`, perfil `default`), que
pertencia à conta da empresa do operador, não a uma conta pessoal.

Correção aplicada em `engine/providers/tts_polly.py`: o construtor de
`PollyTTS` **exige** `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/
`AWS_DEFAULT_REGION` explícitos vindos do `.env` deste projeto, e nunca
chama `boto3.client("polly")` sem esses parâmetros. Foi cogitado um esquema
alternativo de "perfil nomeado" (`AWS_PROFILE=youcreate`, criado via
`aws configure --profile youcreate`) como opção mais segura que colar chaves
em texto puro — mas essa abordagem também teve um bug (um `AWS_PROFILE=`
vazio no `.env` ainda define a variável de ambiente, e o boto3 tenta
resolver um perfil literalmente chamado `""`, levantando `ProfileNotFound`
mesmo com chaves explícitas presentes). O operador optou por manter só
chaves literais, então o suporte a `AWS_PROFILE` foi removido.

**Regra para qualquer credencial de nuvem futura neste projeto**: sempre ler
explicitamente do `.env` deste projeto, nunca da cadeia de descoberta
"ambiente" do SDK (perfis da CLI, variáveis de shell, IAM role da máquina).

---

## Fluxo completo: do upload ao download (caso web)

Trace de ponta a ponta de uma requisição real:

1. Usuário abre `http://localhost:8000` → `api/main.py` `index()` lê e
   devolve o `web/index.html` puro.
2. Usuário seleciona um `.mp4` → JS lê a duração localmente via `<video>`.
   Se > 60s, mostra a barra de recorte; usuário arrasta até escolher o
   trecho.
3. Clica em "Traduzir e legendar" → JS monta `FormData` com o arquivo (+
   `start`/`clip_duration` se aplicável) e faz `POST /api/jobs`.
4. No servidor, `create_job()` recebe o arquivo, salva em
   `storage/uploads/<uuid>_nome.mp4`. Se veio recorte, chama
   `engine/steps/trim.py` `trim_video()` — um
   `ffmpeg -ss <start> -t <duração> -c copy`, que corta sem recodificar
   (rápido, mas alinhado ao keyframe mais próximo). O arquivo resultante
   (`_trim.mp4`) é o que segue adiante — o vídeo original completo nunca
   entra no pipeline pesado.
5. `jobs.create(video_path)` cria um `Job` com UUID próprio e dispara
   `_run_job` numa thread separada. O endpoint responde **imediatamente**
   com `{"id": "..."}` — o processamento continua em segundo plano.
6. JS recebe o ID e abre `EventSource('/api/jobs/{id}/events')`.
7. Na thread de fundo, `_run_job` chama `engine.pipeline.run()`, passando
   `work_dir=storage/work/<job_id>/` (isolado, evita colisão entre jobs
   simultâneos):
   1. `audio.py` `extract_audio()` → ffmpeg extrai `.wav` 16kHz mono →
      progresso 5%
   2. `transcribe.py` `transcribe()` → faster-whisper (local, CPU) → lista
      de `Segment` em inglês com timestamps → 20%
   3. `translate.py` → `translate_gemini.py` manda todas as falas numa
      chamada só ao Gemini, pede JSON ordenado, preenche `seg.translation`
      → 45%
   4. `subtitle.py` `build_srt()` → escreve
      `storage/outputs/<nome>.pt-BR.srt` → 60%
   5. `dub.py` `synthesize_dub()`: para cada segmento, chama `tts_polly.py`
      (síntese real via AWS), ajusta a duração, monta a trilha completa
      → 75%
   6. `render.py` `build_final()`: ffmpeg junta vídeo original + trilha
      dublada + legenda queimada → `storage/outputs/<nome>.pt-BR.mp4` → 90%
   - A cada etapa acima, `on_progress()` dispara e cai no callback de
     `_run_job`, que atualiza o `Job` e empurra um evento para a `Queue`.
8. O gerador de `event_stream()` em `api/main.py` está bloqueado em
   `job.events.get()` — cada `put()` do passo 7 libera ele, que formata
   `data: {...}\n\n` e manda para o navegador via HTTP chunked. O JS recebe
   cada um e atualiza a barra visualmente, em tempo real.
9. Pipeline termina (sucesso ou `PipelineError`) → `_run_job` atualiza
   `job.status` e faz `job.events.put(None)` → o gerador manda o evento
   final `{done: true, status: "done"|"error"}` e encerra a conexão.
10. JS vê `data.done` → se `status === "done"`, chama `GET /api/jobs/{id}`
    para pegar os nomes dos arquivos finais e monta os links
    `/api/download/{video}` e `/api/download/{srt}`.
11. Usuário clica em "Baixar" → `download()` serve o arquivo direto de
    `storage/outputs/`.

O CLI (`cli.py`) faz basicamente o mesmo passo 7 sozinho, sem os passos 1-6
e 8-11 — chama `engine.pipeline.run()` direto e imprime o progresso no
terminal em vez de mandar por SSE, que é exatamente o ponto da separação
motor/web.
