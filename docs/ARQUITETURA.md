# youcreate — Arquitetura

Este documento explica **como o sistema é organizado e por quê**: os três
serviços, as decisões de design do motor de processamento, e as lições
aprendidas ao longo do caminho. Para o passo a passo de uma requisição real
percorrendo o sistema, veja [`FLUXO.md`](FLUXO.md). É um complemento ao
`CLAUDE.md` (guia para o Claude Code operar neste repositório) e ao
`README.md` (guia de instalação/uso).

## Visão geral: três serviços independentes

```
┌─────────────┐        Redis         ┌─────────────┐
│   webapp    │ ───────────────────► │   worker    │
│  (FastAPI)  │  (fila + pub/sub)    │  (motor)    │
└─────────────┘ ◄─────────────────── └─────────────┘
      │                                     │
      └──────────► storage/outputs/ ◄───────┘
                 (volume compartilhado)
```

- **`webapp/`** — recebe a URL do YouTube pelo navegador, valida, e só
  **enfileira** o job no Redis. Nunca baixa nem processa vídeo.
- **`worker/`** — consome jobs da fila e roda o motor de verdade (download,
  transcrição, tradução, dublagem, render). Pode ter várias réplicas
  processando em paralelo.
- **`redis`** — a fila de jobs e o canal de progresso (pub/sub) entre os
  dois. Imagem oficial (`redis:7-alpine`), sem código próprio no repo.

Os dois serviços **não compartilham nenhum arquivo de código**. Isso foi uma
decisão explícita: cada pasta (`webapp/`, `worker/`) precisa poder ser
copiada e implantada sozinha, sem depender de nada fora de si mesma. Na
prática isso significa:

- Cada um tem seu próprio `Dockerfile`, com o **contexto de build sendo a
  própria pasta** — a imagem do `webapp` fisicamente não consegue enxergar
  arquivos do `worker` (e vice-versa), nem em tempo de build nem de
  execução.
- Cada um tem seu próprio `requirements.txt`. O `webapp` é enxuto (FastAPI,
  redis, yt-dlp, pydantic); o `worker` carrega as libs pesadas
  (faster-whisper, google-genai, boto3) e é o único que precisa do ffmpeg
  instalado no sistema.
- Cada um tem seu próprio `.env`/`.env.example` — segredos (Gemini, AWS)
  ficam só em `worker/.env`, já que o `webapp` nunca precisa deles.
- O protocolo de comunicação (chaves e formato de dados no Redis) é
  implementado **duas vezes**, uma em cada serviço:
  `webapp/queue_client.py` (lado produtor: cria job, lê status, assina o
  canal de progresso) e `worker/queue_client.py` (lado consumidor: retira da
  fila, atualiza status, publica progresso). Isso é um trade-off consciente:
  ganha-se independência real de deploy, paga-se com o risco de as duas
  cópias desalinharem se o formato do protocolo mudar um dia (mudar num
  lugar sem lembrar de mudar no outro quebra tudo silenciosamente).
- O mesmo vale para `logging_setup.py` (utilitário pequeno, sem risco real
  de desalinhamento) e para a consulta de metadados do YouTube: o `webapp`
  tem sua própria implementação standalone (`youtube_probe.py`, usando
  `yt-dlp` diretamente) em vez de chamar o motor do `worker` — é uma
  reimplementação deliberada, não uma cópia por preguiça.
- O `docker-compose.yml`, na raiz, é o único arquivo que conhece os dois
  serviços ao mesmo tempo — isso é orquestração (como um manifesto de
  deploy), não compartilhamento de código.

A única coisa de fato compartilhada entre os dois é **dado, não código**:
`storage/outputs/` é um volume Docker nomeado montado nos dois containers —
o worker escreve o `.mp4`/`.srt` finais ali, o webapp serve o download de
lá. Isso é uma troca de dados normal entre produtor e consumidor (o
equivalente a dois microsserviços compartilhando um bucket), não uma
violação do princípio de independência de código. `storage/work/` (arquivos
temporários de um job em processamento) **não** é compartilhado — vive só
dentro do container do worker que está processando aquele job, e é apagado
ao final.

## Por que Redis, e por que dá pra escalar

Antes desta divisão, o site chamava o motor **na mesma thread/processo**
(via `api/jobs.py`, em memória) — não dava para escalar o processamento sem
escalar o site junto, e travar num vídeo pesado podia afetar o processo que
servia HTTP.

Com o Redis no meio:

- **Fila** (`youcreate:queue`, uma lista Redis): o `webapp` só dá `RPUSH`; o
  `worker` fica em loop dando `BLPOP` (bloqueante, com timeout). `BLPOP` é
  atômico entre múltiplos clientes — se houver 3 réplicas de worker todas
  esperando na mesma fila, cada job enfileirado vai para exatamente uma
  delas, nunca duplicado.
- **Status** (`youcreate:job:<id>`, um hash Redis): guarda o estado atual do
  job (status, pct, step, mensagem, nomes dos arquivos de resultado). TTL de
  24h (`JOB_TTL_SECONDS`) para não acumular lixo no Redis para sempre — os
  resultados de verdade (o `.mp4`/`.srt`) vivem em disco, não no Redis.
- **Progresso em tempo real** (`youcreate:job:<id>:events`, um canal
  pub/sub): o worker publica um evento a cada etapa do pipeline; o endpoint
  SSE do `webapp` está inscrito nesse canal e repassa para o navegador via
  `EventSource`.

Isso permite `docker compose up -d --scale worker=N` — múltiplos jobs sendo
processados ao mesmo tempo, cada um por um worker diferente, sem tocar no
código do site.

### Um detalhe de implementação que já mordeu uma vez

`redis-py` levanta `redis.exceptions.TimeoutError` (erro de **socket**, não
o `nil` esperado do Redis) se o `socket_timeout` da conexão for menor ou
igual ao timeout de um comando bloqueante como `BLPOP`. As duas cópias de
`queue_client.py` configuram `socket_timeout=30` no cliente por causa disso
(o `dequeue()` usa timeout de 5s; o SSE do `webapp` também faz
`pubsub.get_message(timeout=5.0, ...)` em loop, mesma classe de risco). Sem
isso, o worker derruba com uma exceção não tratada assim que a fila fica
vazia por alguns segundos — foi exatamente o que aconteceu na primeira
tentativa desta arquitetura.

## Princípios do motor (`worker/engine/`)

Três decisões estruturais guiam o motor:

1. **Motor isolado da web** — nenhum arquivo em `engine/` importa FastAPI ou
   Redis. É Python puro, chamável tanto pelo `worker/cli.py` (terminal, sem
   fila) quanto pelo `worker/worker.py` (consumindo da fila).
2. **Progresso por callback** — o motor não sabe o que é HTTP nem Redis. Ele
   só chama `on_progress(step_id, label, pct, message)` a cada etapa; quem
   chamou decide o que fazer com isso (o CLI imprime no terminal, o worker
   escreve no Redis).
3. **Etapas plugáveis** — tradução e TTS são "providers" atrás de uma
   interface (`Protocol`), trocáveis via variável de ambiente sem tocar no
   orquestrador.

### Modelos de dados (`engine/models.py`)

Duas dataclasses simples carregam tudo que passa pelo pipeline:

```python
@dataclass
class Segment:
    start: float           # segundos
    end: float              # segundos
    text: str               # texto original (EN)
    translation: str = ""   # PT-BR (preenchido na etapa de traducao)

@dataclass
class PipelineResult:
    segments: list[Segment] = field(default_factory=list)
    srt_path: Path | None = None       # legenda PT-BR
    dub_audio_path: Path | None = None # trilha dublada
    video_out: Path | None = None      # mp4 final (legenda + dublagem)
```

### `engine/steps/download.py` — `probe()` e `download_video()`

Única porta de entrada do sistema: uma URL do YouTube (`youtube.com`,
`youtu.be`, `m.youtube.com`, `music.youtube.com`, sempre http/https). Usa
`yt-dlp`:

- `probe(url)` consulta duração/título **sem baixar** — usado pelo `webapp`
  (via sua própria cópia, `youtube_probe.py`) para montar a barra de corte
  antes de disparar o job.
- `download_video(url, out_dir, start, duration)` baixa de verdade. Quando
  `duration` é passado, usa `download_ranges` do yt-dlp para trazer **só o
  trecho pedido** — o vídeo inteiro nunca passa pela máquina quando só um
  recorte será usado.

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
trechos de silêncio em vez de "alucinar" texto neles. Resultado: uma lista
de `Segment` com timestamps e texto em inglês.

### `engine/providers/translate_gemini.py` — `translate_batch()`

O ponto mais interessante da tradução: em vez de chamar o Gemini uma vez por
frase, monta **um prompt único** com todas as falas numeradas
(`"0: Hello there\n1: How are you..."`) e pede para o modelo devolver um
**array JSON na mesma ordem**, forçado via `response_schema=list[str]`. Isso
garante que a tradução do índice 47 corresponde exatamente à fala 47, sem
risco de desalinhamento, e custa uma única chamada de API por vídeo,
independente de quantas falas existam.

`engine/steps/translate.py` é o encaixe: extrai os textos dos `Segment`,
chama `translate_batch()`, escreve o resultado de volta em
`seg.translation`. A seleção de provider olha `config.TRANSLATE_PROVIDER`
(hoje só `"gemini"`) — trocar por outro é implementar a mesma interface
(`translate_base.py`) e adicionar um `if` na fábrica.

### `engine/steps/subtitle.py` — `build_srt()`

Monta o `.srt` no formato padrão (índice sequencial, timestamp
`HH:MM:SS,mmm --> HH:MM:SS,mmm`, texto, linha em branco), sem dependências
externas.

### `engine/steps/dub.py` — `synthesize_dub()`

O problema central da dublagem: **fala em PT-BR costuma ser mais longa que
em inglês**, então o áudio dublado de uma frase pode não caber no intervalo
de tempo que a frase original ocupava.

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

### `PipelineError` — não perder o que já deu certo

Se qualquer etapa falhar, o pipeline não perde o que já foi construído — a
exceção original é embrulhada numa `PipelineError` carregando o
`PipelineResult` parcial. Assim, se a legenda já tinha sido gerada e só a
dublagem falhou depois, quem chamou (`worker.py`, `cli.py`) ainda consegue
recuperar o `.srt` pronto em vez de perdê-lo.

## O incidente de segurança (Amazon Polly)

Ao adicionar o Amazon Polly como provider de TTS, o primeiro teste chamou
`boto3.client("polly")` sem credenciais explícitas — isso caiu na cadeia
padrão de credenciais do boto3, que usou, sem intenção, um perfil AWS já
configurado na máquina (`~/.aws/credentials`, perfil `default`), que
pertencia à conta da empresa do operador, não a uma conta pessoal.

Correção aplicada em `engine/providers/tts_polly.py`: o construtor de
`PollyTTS` **exige** `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/
`AWS_DEFAULT_REGION` explícitos vindos do `.env` do worker, e nunca chama
`boto3.client("polly")` sem esses parâmetros. Foi cogitado um esquema
alternativo de "perfil nomeado" (`AWS_PROFILE=youcreate`, criado via
`aws configure --profile youcreate`) como opção mais segura que colar chaves
em texto puro — mas essa abordagem também teve um bug (um `AWS_PROFILE=`
vazio no `.env` ainda define a variável de ambiente, e o boto3 tenta
resolver um perfil literalmente chamado `""`, levantando `ProfileNotFound`
mesmo com chaves explícitas presentes). O operador optou por manter só
chaves literais, então o suporte a `AWS_PROFILE` foi removido.

**Regra para qualquer credencial de nuvem futura neste projeto**: sempre ler
explicitamente do `.env` do serviço que precisa dela, nunca da cadeia de
descoberta "ambiente" do SDK (perfis da CLI, variáveis de shell, IAM role da
máquina).
