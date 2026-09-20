# youcreate

Recebe a **URL de um vídeo do YouTube** em inglês e devolve o mesmo vídeo
localizado em português do Brasil, com **legenda `.srt`** e **dublagem (voz
PT-BR)**. Sem marca d'água.

O vídeo é baixado via `yt-dlp` para uma pasta temporária, processado, e
apagado assim que o job termina (sucesso ou erro) — nada fica armazenado
além dos resultados finais (`.mp4` dublado + `.srt`) em `storage/outputs/`.

## Arquitetura em 3 serviços

- **`webapp/`** — o site (FastAPI): recebe o link, enfileira o job, serve a
  página e o download. Não processa vídeo.
- **`worker/`** — o motor: consome jobs da fila e faz o trabalho pesado
  (download, transcrição, tradução, dublagem, render). Pode rodar várias
  réplicas em paralelo.
- **`redis`** — fila de jobs e progresso em tempo real entre os dois.

Os dois serviços são pastas 100% independentes — sem nenhum arquivo
compartilhado entre eles, só se falam pelo Redis. Veja
[`docs/ARQUITETURA.md`](docs/ARQUITETURA.md) para os detalhes de design.

## Rodando com Docker Compose (recomendado)

### Pré-requisitos

- Docker + Docker Compose

### Configuração

```bash
cp webapp/.env.example webapp/.env
cp worker/.env.example worker/.env
```

Edite `worker/.env` com suas chaves (veja "Obtendo as chaves" abaixo).
`webapp/.env` já funciona com os valores padrão.

### Subir

```bash
docker compose up -d --build
```

Abra **http://localhost:8000**.

### Escalar o processamento

```bash
docker compose up -d --scale worker=3
```

Cada worker processa um job por vez; com N réplicas, N jobs rodam em
paralelo.

### Outros comandos úteis

```bash
docker compose logs -f web       # logs do site
docker compose logs -f worker    # logs do worker (todas as réplicas)
docker compose down              # para tudo
```

### `docker compose up --build` falhando com `CERTIFICATE_VERIFY_FAILED`?

Se sua máquina estiver atrás de um proxy corporativo que inspeciona HTTPS
(Zscaler, Netskope, antivírus de empresa, etc.), o `pip install` dentro do
build vai falhar com esse erro — mesmo a máquina tendo internet normal,
porque o certificado desse proxy não é reconhecido dentro do container.

Solução: extraia o certificado confiável da sua máquina e coloque em
`webapp/certs/` e `worker/certs/` (pastas ignoradas pelo git, cada uma já
tem um `.gitkeep`):

```bash
cp /etc/ssl/certs/ca-certificates.crt webapp/certs/host-ca-bundle.crt
cp /etc/ssl/certs/ca-certificates.crt worker/certs/host-ca-bundle.crt
docker compose up -d --build
```

Isso não afeta quem builda numa rede sem esse tipo de proxy (a VM na nuvem,
por exemplo) — a pasta fica vazia e o build segue normal.

## Rodando sem Docker (desenvolvimento local)

Precisa de **Python 3.11+**, **ffmpeg** no PATH, e um **Redis** rodando
localmente (`docker run -d -p 6379:6379 redis:7-alpine` é o jeito mais
rápido, mesmo sem usar o Compose para o resto).

### Instalando o ffmpeg

**Windows**
```
winget install Gyan.FFmpeg
```
(ou baixe em https://www.gyan.dev/ffmpeg/builds/ e adicione a pasta `bin` ao PATH)

**macOS**
```
brew install ffmpeg
```

**Linux (Debian/Ubuntu)**
```
sudo apt install ffmpeg
```

Confirme a instalação com `ffmpeg -version` e `ffprobe -version`.

### Instalação

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r webapp/requirements.txt -r worker/requirements.txt
cp webapp/.env.example webapp/.env
cp worker/.env.example worker/.env
```

### Uso

```bash
# Site (precisa do Redis rodando)
cd webapp && uvicorn main:app --reload

# Worker, em outro terminal
cd worker && python worker.py

# Ou o motor direto, sem fila nem site:
cd worker && python cli.py "https://www.youtube.com/watch?v=..."
cd worker && python cli.py "https://www.youtube.com/watch?v=..." --no-dub
cd worker && python cli.py "https://www.youtube.com/watch?v=..." --start 30 --duration 60
```

## Obtendo as chaves (`worker/.env`)

### Dublagem e tradução usam a mesma chave

O youcreate usa **uma única credencial**: a `OPENROUTER_API_KEY`. Ela cobre
tanto a tradução (`/chat/completions`) quanto a dublagem
(`/audio/speech`). O Amazon Polly foi o provider de dublagem original e foi
**removido** — as vozes do OpenRouter soaram melhor em teste comparativo e
dispensam a conta AWS.

A voz padrão é `pm_alex`, do `hexgrad/kokoro-82m` — escolhido por ser o único modelo testado que honra o parâmetro `speed`, o que deixa a fala dublada encaixada no tempo do vídeo. Para
trocar, ajuste `DUB_MODEL` e `DUB_VOICE` no `worker/.env`; os ids de voz de
cada modelo estão em `supported_voices` na API de modelos do OpenRouter:

```bash
curl -s -H "Authorization: Bearer $OPENROUTER_API_KEY" \
  "https://openrouter.ai/api/v1/models?output_modalities=speech" \
  | jq -r '.data[] | "\(.id): \(.supported_voices // [] | join(", "))"'
```

`DUB_VOICE_POOL` lista as vozes extras usadas quando o vídeo tem mais de um
locutor (formato `modelo|voz`, separado por vírgula, pode misturar
modelos). Dois avisos práticos: nem todo modelo aceita `response_format:
mp3` (o Gemini só aceita `pcm`) e nem todo modelo honra o parâmetro
`speed` — quando não honra, o encaixe temporal da fala é feito com
`atempo`, que soa pior. O código trata os dois casos automaticamente.

### `OPENROUTER_API_KEY` (tradução — obrigatório, único provider)

`TRANSLATE_PROVIDER=openrouter` é o único provider de tradução hoje —
Gemini e Amazon Translate foram os dois primeiros usados no projeto, e os
dois foram **removidos** (ver `docs/ARQUITETURA.md` para o histórico
completo de por quê). Sem essa chave, a tradução não funciona.

1. Crie uma conta em https://openrouter.ai e gere uma chave em
   https://openrouter.ai/keys
2. Cole em `OPENROUTER_API_KEY=` no `worker/.env`
3. Defina `OPENROUTER_MODEL=` com o modelo desejado (formato
   `provider/modelo`, ex: `deepseek/deepseek-v4-flash`) — ver
   https://openrouter.ai/models para a lista completa e preços por token.

As demais variáveis do `worker/.env.example` já vêm com defaults sensatos
(`SOURCE_LANG`, `WHISPER_MODEL`, `DUB_VOICE`, `BURN_SUBS`, etc.) — ajuste
conforme necessário.

## Usando o site

Cole a URL de um vídeo do YouTube e clique em "Carregar". O download
acontece no worker (não no navegador nem no site) e é apagado
automaticamente assim que o processamento terminar.

Depois de carregado, uma barra permite escolher opcionalmente um trecho do
vídeo (arrastando as bordas) — sem limite de duração; deixá-la como está
processa o vídeo inteiro. Clique em "Traduzir e legendar" e acompanhe o
progresso em tempo real; ao concluir, aparecem os links para baixar o vídeo
dublado e a legenda.

## Logs

- Worker: `docker compose logs -f worker`, ou `worker/storage/youcreate-worker.log` / `worker/youcreate.log` (CLI) sem Docker.
- Site: `docker compose logs -f web`, ou `webapp/storage/youcreate-web.log` sem Docker.

Registram o andamento de cada etapa e o traceback completo em caso de erro —
útil para depurar sem depender só da mensagem resumida mostrada na tela.

## Antes de publicar o vídeo

- **Marque "conteúdo alterado ou sintético"** ao subir no YouTube — é
  exigência da plataforma para vídeos com voz gerada por IA.
- **Confirme que você tem direito de uso comercial** do conteúdo-fonte antes
  de dublar/legendar e publicar.

## Fora de escopo

Recriação de frames por IA, lip sync, login/multiusuário e cobrança não
fazem parte deste projeto. Veja `CLAUDE.md` para as decisões de arquitetura
completas.

## Saiba mais

- [`docs/ARQUITETURA.md`](docs/ARQUITETURA.md) — como o sistema é organizado
  e por quê (os três serviços, o motor de processamento, decisões de
  design).
- [`docs/FLUXO.md`](docs/FLUXO.md) — o passo a passo de uma requisição real,
  do link colado no navegador até o download do resultado.
