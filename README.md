# youcreate

Recebe um vídeo `.mp4` em inglês e devolve o mesmo vídeo localizado em
português do Brasil, com **legenda `.srt`** e **dublagem (voz PT-BR)**. Sem
marca d'água. Uso local, single-user — você roda na sua própria máquina.

## Pré-requisitos

- **Python 3.11+**
- **ffmpeg** instalado e no PATH do sistema

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

## Instalação

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

## Configuração (`.env`)

Edite o `.env` gerado a partir do `.env.example`. As chaves obrigatórias:

### `GEMINI_API_KEY` (tradução)

1. Acesse https://aistudio.google.com
2. Faça login com uma conta Google
3. Vá em "Get API key" e gere uma chave
4. Cole em `GEMINI_API_KEY=` no `.env`

O tier gratuito do Gemini é suficiente para uso pessoal.

### Credenciais da AWS (dublagem — Amazon Polly)

1. Crie (ou use) um usuário IAM com a permissão `polly:SynthesizeSpeech`, e
   gere um Access Key ID + Secret Access Key para ele
2. No `.env`, defina:
   ```
   AWS_ACCESS_KEY_ID=...
   AWS_SECRET_ACCESS_KEY=...
   AWS_DEFAULT_REGION=us-east-1
   ```
3. `DUB_VOICE` deve ser o `VoiceId` do Polly (ex: `Camila`, voz feminina
   PT-BR). `POLLY_ENGINE` é `standard` ou `neural` — nem toda região da AWS
   suporta o motor neural, confira em "Feature and Region Compatibility" no
   console do Polly.

**Importante:** o youcreate **nunca** usa o perfil `default` nem credenciais
"ambiente" da máquina (variáveis do shell, `~/.aws/credentials`, IAM role) —
só as chaves definidas explicitamente no `.env` deste projeto. Isso evita
usar por engano uma conta AWS de outro projeto/empresa que porventura já
esteja configurada na mesma máquina.

Sem essas credenciais, a legenda ainda funciona normalmente — só a dublagem
depende delas.

As demais variáveis do `.env.example` já vêm com defaults sensatos
(`SOURCE_LANG`, `WHISPER_MODEL`, `DUB_VOICE`, `BURN_SUBS`, etc.) — ajuste
conforme necessário.

## Uso

### Linha de comando

```bash
python cli.py caminho/do/video.mp4              # legenda + dublagem
python cli.py caminho/do/video.mp4 --no-dub     # só legenda
python cli.py caminho/do/video.mp4 --no-subs    # só dublagem
```

Os arquivos gerados vão para `storage/outputs/`.

### Interface web

```bash
uvicorn api.main:app --reload
```

Abra http://localhost:8000, selecione um `.mp4` e clique em "Traduzir e
legendar". Uma barra de progresso acompanha o processamento em tempo real; ao
concluir, aparecem os links para baixar o vídeo dublado e a legenda.

Se o vídeo enviado passar de 1 minuto, uma barra de recorte aparece abaixo do
upload para você escolher qual trecho de até 1 minuto será localizado (o
recorte acontece no servidor antes do processamento pesado começar).

## Logs

- CLI: `youcreate.log` na raiz do projeto
- Web: `storage/youcreate.log`

Ambos registram o andamento de cada etapa e o traceback completo em caso de
erro — útil para depurar sem depender só da mensagem resumida mostrada na
tela.

## Antes de publicar o vídeo

- **Marque "conteúdo alterado ou sintético"** ao subir no YouTube — é
  exigência da plataforma para vídeos com voz gerada por IA.
- **Confirme que você tem direito de uso comercial** do conteúdo-fonte antes
  de dublar/legendar e publicar.

## Fora de escopo (v1)

Recriação de frames por IA, lip sync, login/multiusuário, cobrança, deploy em
cloud e fila distribuída não fazem parte desta versão — são evoluções
previstas, não builds quebrados. Veja `CLAUDE.md` para as decisões de
arquitetura completas.

## Saiba mais

Para uma explicação detalhada de como cada fase foi construída e como uma
requisição percorre o sistema do upload ao download, veja
[`docs/ARQUITETURA-E-FLUXO.md`](docs/ARQUITETURA-E-FLUXO.md).
