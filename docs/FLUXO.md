# youcreate — Fluxo de uma requisição

Este documento faz o trace de ponta a ponta de uma requisição real, do link
colado no navegador até o download do resultado, atravessando os três
serviços (`webapp` → Redis → `worker` → Redis → `webapp`). Para o "porquê"
por trás de cada peça, veja [`ARQUITETURA.md`](ARQUITETURA.md).

## Fluxo completo (caso web, cinco telas)

1. Usuário abre `http://localhost:8000` (Tela 1) → `webapp/main.py`'s
   `index()` lê e devolve `webapp/web/index.html` puro (o mesmo arquivo
   serve `/`, `/configurar` e `/v/{job_id}` — a troca entre telas é só
   roteamento client-side via `history.pushState`/`popstate`).
2. Usuário cola a URL do YouTube e clica em "Processar" → JS chama
   `GET /api/probe?url=...` → `webapp/main.py` chama
   `youtube_probe.probe(url)` (implementação própria do webapp, via
   `yt-dlp` + cookies de sessão + Deno para resolver o desafio JS do
   YouTube — ver "O YouTube tem duas camadas de bloqueio..." em
   [`ARQUITETURA.md`](ARQUITETURA.md) —, **sem** baixar nada) → devolve
   `{duration, title}`. JS navega para `/configurar` (Tela 2).
3. Na Tela 2, o usuário escolhe idioma de origem, idioma de destino (só
   `pt` é selecionável hoje — os demais aparecem como "em breve"), se quer
   legenda (`include_subtitles`) e, opcionalmente, estreita a barra de
   corte (`start`/`clip_duration`; se deixar como está, processa o vídeo
   inteiro).
4. Clica em "Gerar vídeo" → JS faz `POST /api/jobs` com corpo JSON
   `{url, source_lang, target_lang, include_subtitles, start?,
   clip_duration?, video_title, video_duration}` (`video_title`/
   `video_duration` vêm do `GET /api/probe` do passo 2, só para a Tela 3/4
   não precisarem re-consultar o YouTube).
5. No `webapp`, `create_job()` chama `queue_client.create_job(...)`:
   - Gera um `job_id` (UUID).
   - Grava um hash `youcreate:job:<id>` no Redis com o estado inicial
     (`status: "queued"`, `pct: 0`, a URL, os idiomas, a escolha de legenda
     e o recorte pedido).
   - Dá `RPUSH youcreate:queue <id>`.
   - Devolve `{"id": "..."}` **imediatamente** — o navegador não fica
     esperando o processamento, que ainda nem começou.
6. JS navega para `/v/{job_id}` (Tela 3) e abre
   `EventSource('/api/jobs/{id}/events')`. (Se o usuário abrir esse link
   direto num navegador novo, ou der refresh no meio do processamento, a
   Tela 3 se reconstrói do zero com uma única chamada a
   `GET /api/jobs/{id}` — não depende de nenhum estado JS deixado pelas
   telas anteriores.)
7. Em algum container `worker` (pode haver várias réplicas competindo pela
   mesma fila), o loop principal de `worker.py` está bloqueado em
   `queue_client.dequeue(timeout=5)` (`BLPOP` no Redis). O `RPUSH` do passo
   5 libera exatamente **um** desses workers, que pega o `job_id`.
8. Esse worker chama `_process(job_id)`:
   1. Lê o job do Redis (`queue_client.get_job`) para saber a URL, os
      idiomas, a escolha de legenda e o recorte.
   2. Marca `status: "running"` no Redis.
   3. Chama `engine.pipeline.run(..., should_cancel=lambda: queue_client.is_cancelled(job_id))`,
      passando `work_dir=storage/work/<job_id>/` (isolado, evita colisão
      entre jobs simultâneos rodando em workers diferentes). Antes de cada
      etapa abaixo, o pipeline chama `should_cancel()` — se o usuário tiver
      clicado em "Cancelar" (Tela 3, ver seção própria abaixo), a próxima
      etapa nem começa:
      1. `download.py` `download_video()` → baixa do YouTube via yt-dlp
         (cookies + Deno, só o trecho pedido se houver `duration`) →
         progresso 2%
      2. `audio.py` `extract_audio()` → ffmpeg extrai `.wav` 16kHz mono →
         5%
      3. `transcribe.py` `transcribe()` → faster-whisper (local, CPU), no
         idioma indicado por `source_lang` → lista de `Segment` com
         timestamps → 20%
      4. `translate.py` → provider ativo (`config.TRANSLATE_PROVIDER`:
         `translate_aws.py`/Amazon Translate por padrão, uma chamada por
         fala; ou `translate_openrouter.py`, todas as falas numa chamada só
         a um LLM escolhido via `OPENROUTER_MODEL`) preenche
         `seg.translation` → 45%
      5. Se `include_subtitles`: `subtitle.py` `build_srt()` **e**
         `build_vtt()` → escrevem `storage/outputs/<nome>.pt-BR.srt` e
         `.vtt` (o `.vtt` alimenta o `<track>` do player da Tela 4) → 60%
      6. `dub.py` `synthesize_dub()`: para cada segmento, chama
         `tts_polly.py` (síntese real via AWS), ajusta a duração, monta a
         trilha completa → 75%
      7. `render.py` `build_final()`: ffmpeg junta vídeo original + trilha
         dublada + legenda queimada (se `include_subtitles`) →
         `storage/outputs/<nome>.pt-BR.mp4` → 90%
   - A cada etapa acima, `on_progress()` dispara dentro de `worker.py`, que
     faz duas coisas: `queue_client.update_job(...)` (atualiza o hash no
     Redis, para quem perguntar via `GET /api/jobs/{id}`) e
     `queue_client.publish_event(...)` (publica no canal
     `youcreate:job:<id>:events`, para quem estiver "ouvindo" em tempo real
     via SSE).
9. No `webapp`, o gerador de `event_stream()` (dentro de
   `GET /api/jobs/{id}/events`) está inscrito nesse canal pub/sub
   (`queue_client.subscribe`). Cada `publish_event()` do passo 8 chega até
   ele, que formata `data: {...}\n\n` e manda para o navegador via HTTP
   chunked. O JS da Tela 3 recebe cada evento e atualiza o percentual e o
   rótulo da etapa atual em tempo real — **sem interpolar** nada entre um
   evento e o próximo (decisão deliberada de simplicidade: o `pct` que o
   motor já emite por etapa já lê bem como progresso). (Como rede de
   segurança contra uma mensagem final perdida por causa de timing entre
   inscrever-se no canal e checar o status, o gerador também reconfere o
   status a cada 5s sem mensagem nova; o JS mostra um aviso se ficar 45s
   sem nenhuma atualização, mas continua esperando — o job segue rodando no
   worker independente da conexão SSE cair.)
10. Pipeline termina de um dos três jeitos possíveis:
    - **Sucesso**: `status: "done"`, mais os nomes/URLs assinadas dos
      arquivos de resultado (`result_video`/`result_srt`/`result_vtt`) →
      Tela 4.
    - **Erro**: `PipelineError` capturada → `worker.py` roda
      `_classify_error(mensagem)` (string matching heurístico sobre o erro,
      normalmente do `yt-dlp`) para preencher `error_code`
      (`video_privado`/`video_indisponivel`/`restrito_regiao`/`sem_audio`/
      `falha_interna`) → `status: "error"` → Tela 5.
    - **Cancelado**: `PipelineCancelled` capturada (ver passo 8.3) →
      `status: "cancelled"` (código distinto de `"error"` — a Tela 5 mostra
      um texto diferente) → Tela 5.

    Em qualquer um dos três casos, `worker.py` atualiza o hash no Redis e
    publica um evento final `{done: true, status: ...}`; o gerador do SSE
    manda esse evento e encerra a conexão. No `finally`, `worker.py` apaga
    `storage/work/<job_id>/` inteiro — o vídeo baixado e todo artefato
    intermediário (áudio extraído, clipes de dublagem) são temporários; só
    o que está em `storage/outputs/` precisa sobreviver.
11. JS vê `data.done` → chama `GET /api/jobs/{id}` de novo para pegar o
    estado final completo e decide a tela: `done` → Tela 4 (player nativo
    com `<track>` apontando pro `.vtt`, cards de download do `.mp4` e do
    `.srt` — o card do `.srt` some inteiro se `include_subtitles` era
    falso); `error`/`cancelled` → Tela 5, escolhendo o texto pelo
    `error_code` (ou pelo pseudo-código `cancelado` no caso de
    cancelamento).
12. Usuário clica em "Baixar MP4"/"Baixar SRT" → os links já vêm prontos em
    `GET /api/jobs/{id}` como `video_url`/`srt_url`/`vtt_url`, cada um
    `/api/download/{filename}?exp=...&sig=...` — uma URL assinada com
    HMAC-SHA256 e validade de 1h (ver "URLs de download assinadas" em
    [`ARQUITETURA.md`](ARQUITETURA.md)). `webapp/main.py`'s `download()`
    valida a assinatura e a expiração antes de servir o arquivo direto de
    `storage/outputs/` — o **mesmo** volume Docker que o worker escreveu no
    passo 8, montado nos dois containers.
13. Quando o usuário sai da Tela 4 (ou 5, se sobrou um `.srt` parcial) —
    fecha a aba, navega para outro site, volta pro início — o evento
    `pagehide` do navegador dispara
    `navigator.sendBeacon('/api/jobs/{id}/discard')`, que apaga os
    arquivos de `storage/outputs/` daquele job. Não há expiração fixa por
    tempo para os arquivos (só os *metadados* no Redis têm TTL de 24h) —
    decisão explícita do operador.

## E se o usuário clicar em "Cancelar" na Tela 3?

`POST /api/jobs/{id}/cancel` → `webapp/queue_client.py`'s
`request_cancel()` seta `cancel_requested: "1"` no hash Redis do job — só
isso, nenhuma outra sinalização direta ao worker. O worker que estiver
processando aquele job só vai notar na próxima checagem de
`should_cancel()` **entre** duas etapas do pipeline (ver passo 8.3 acima);
não há como interromper uma chamada de ffmpeg/Whisper/tradutor/Polly já em
andamento. Na prática isso significa que "Cancelar" pode levar alguns
segundos a um ou dois minutos para ter efeito, dependendo de qual etapa
está rodando no momento do clique — é um trade-off deliberado de
simplicidade, não um bug.

## Caso CLI (sem fila, sem webapp)

`worker/cli.py` faz basicamente o mesmo passo 8 sozinho, sem os passos
1-7 e 9-12: chama `engine.pipeline.run()` direto, imprime o progresso no
terminal em vez de publicar no Redis, e limpa seu próprio `work_dir` ao
final. É o motor rodando "nu", exatamente como um worker roda por dentro —
só que sem fila, sem Redis, sem rede: útil para depurar o pipeline
isoladamente sem precisar subir a stack inteira.

## E se dois vídeos forem enviados ao mesmo tempo?

Com `docker compose up -d --scale worker=N`, há N workers competindo pela
mesma fila (`BLPOP` é atômico — cada job vai para exatamente um deles).
Dois jobs enviados em sequência pelo `webapp` são processados em paralelo
por dois workers diferentes, cada um com seu próprio `work_dir` isolado
(nomeado pelo `job_id`) e sua própria chamada de pipeline — não há estado
compartilhado entre eles além do Redis (coordenação) e do volume de
`storage/outputs/` (onde cada um escreve arquivos com nomes distintos).
