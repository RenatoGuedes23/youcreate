# youcreate — Fluxo de uma requisição

Este documento faz o trace de ponta a ponta de uma requisição real, do link
colado no navegador até o download do resultado, atravessando os três
serviços (`webapp` → Redis → `worker` → Redis → `webapp`). Para o "porquê"
por trás de cada peça, veja [`ARQUITETURA.md`](ARQUITETURA.md).

## Fluxo completo (caso web)

1. Usuário abre `http://localhost:8000` → `webapp/main.py`'s `index()` lê e
   devolve `webapp/web/index.html` puro.
2. Usuário cola a URL do YouTube e clica em "Carregar" → JS chama
   `GET /api/probe?url=...` → `webapp/main.py` chama
   `youtube_probe.probe(url)` (implementação própria do webapp, via
   `yt-dlp`, **sem** baixar nada) → devolve `{duration, title}`.
3. JS monta a barra de corte com a faixa inteira pré-selecionada. Se o
   usuário arrastar as bordas para estreitar o trecho, isso vira
   `start`/`clip_duration` no envio; se deixar como está, nenhum dos dois é
   enviado (processa o vídeo inteiro).
4. Clica em "Traduzir e legendar" → JS faz
   `POST /api/jobs` com corpo JSON `{url, start?, clip_duration?}`.
5. No `webapp`, `create_job()` chama `queue_client.create_job(...)`:
   - Gera um `job_id` (UUID).
   - Grava um hash `youcreate:job:<id>` no Redis com o estado inicial
     (`status: "queued"`, `pct: 0`, a URL e o recorte pedido).
   - Dá `RPUSH youcreate:queue <id>`.
   - Devolve `{"id": "..."}` **imediatamente** — o navegador não fica
     esperando o processamento, que ainda nem começou.
6. JS recebe o ID e abre `EventSource('/api/jobs/{id}/events')`.
7. Em algum container `worker` (pode haver várias réplicas competindo pela
   mesma fila), o loop principal de `worker.py` está bloqueado em
   `queue_client.dequeue(timeout=5)` (`BLPOP` no Redis). O `RPUSH` do passo
   5 libera exatamente **um** desses workers, que pega o `job_id`.
8. Esse worker chama `_process(job_id)`:
   1. Lê o job do Redis (`queue_client.get_job`) para saber a URL e o
      recorte.
   2. Marca `status: "running"` no Redis.
   3. Chama `engine.pipeline.run(...)`, passando
      `work_dir=storage/work/<job_id>/` (isolado, evita colisão entre jobs
      simultâneos rodando em workers diferentes):
      1. `download.py` `download_video()` → baixa do YouTube via yt-dlp (só
         o trecho pedido, se houver `duration`) → progresso 2%
      2. `audio.py` `extract_audio()` → ffmpeg extrai `.wav` 16kHz mono →
         5%
      3. `transcribe.py` `transcribe()` → faster-whisper (local, CPU) →
         lista de `Segment` em inglês com timestamps → 20%
      4. `translate.py` → `translate_gemini.py` manda todas as falas numa
         chamada só ao Gemini, pede JSON ordenado, preenche
         `seg.translation` → 45%
      5. `subtitle.py` `build_srt()` → escreve
         `storage/outputs/<nome>.pt-BR.srt` → 60%
      6. `dub.py` `synthesize_dub()`: para cada segmento, chama
         `tts_polly.py` (síntese real via AWS), ajusta a duração, monta a
         trilha completa → 75%
      7. `render.py` `build_final()`: ffmpeg junta vídeo original + trilha
         dublada + legenda queimada → `storage/outputs/<nome>.pt-BR.mp4` →
         90%
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
   chunked. O JS recebe cada evento e atualiza a barra visualmente, em
   tempo real. (Como rede de segurança contra uma mensagem final perdida por
   causa de timing entre inscrever-se no canal e checar o status, o gerador
   também reconfere o status a cada 5s sem mensagem nova.)
10. Pipeline termina (sucesso ou `PipelineError`) → `worker.py` atualiza o
    hash no Redis (`status: "done"` ou `"error"`, mais os nomes dos arquivos
    de resultado) e publica um evento final `{done: true, status: ...}` →
    o gerador do SSE manda esse evento e encerra a conexão. No `finally`,
    `worker.py` apaga `storage/work/<job_id>/` inteiro — o vídeo baixado e
    todo artefato intermediário (áudio extraído, clipes de dublagem) são
    temporários; só o que está em `storage/outputs/` precisa sobreviver.
11. JS vê `data.done` → se `status === "done"`, chama `GET /api/jobs/{id}`
    para pegar os nomes dos arquivos finais e monta os links
    `/api/download/{video}` e `/api/download/{srt}`.
12. Usuário clica em "Baixar" → `webapp/main.py`'s `download()` serve o
    arquivo direto de `storage/outputs/` — o **mesmo** volume Docker que o
    worker escreveu no passo 8, montado nos dois containers.

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
