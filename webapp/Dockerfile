# Site (FastAPI): so recebe pedidos e enfileira -- sem ffmpeg, sem as libs
# pesadas de processamento (essas ficam so no worker, servico separado).
# Build context = esta propria pasta (webapp/) -- imagem 100% autocontida.
FROM python:3.11-slim

# CA extra opcional (nao versionada, ver .gitignore): se a maquina que builda
# a imagem estiver atras de um proxy TLS corporativo (Zscaler, etc.) que
# reassina certificados, coloque o CA bundle dele em certs/*.crt -- sem isso,
# pip install falha com CERTIFICATE_VERIFY_FAILED mesmo com o host tendo
# acesso normal. Pasta vazia (so o .gitkeep) = build normal, sem efeito.
COPY certs/ /usr/local/share/ca-certificates/extra/
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl unzip \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*
# pip/requests/yt-dlp usam o bundle interno do pacote certifi por padrao, que
# 'update-ca-certificates' nao alcanca -- essas variaveis forcam o uso do
# bundle do sistema (que ja inclui o CA extra acima, se houver).
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    PIP_CERT=/etc/ssl/certs/ca-certificates.crt

# Deno: yt-dlp (usado por youtube_probe.py) precisa de um runtime JS pra
# resolver o desafio "n" que o YouTube passou a exigir antes de liberar a
# lista de formatos de video -- sem isso, mesmo com cookies validos, a
# extracao falha com "No video formats found!". Deno e o runtime preferido
# por padrao pelo yt-dlp (Node so e aceito a partir da v22, mais trabalhoso
# de obter no apt do Debian slim; Deno e um binario estatico unico).
# yt-dlp-ejs (no requirements.txt) traz o script de resolucao do desafio
# embutido no pacote, sem precisar buscar nada da internet em tempo de
# execucao (--remote-components).
RUN curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# yt-dlp (e qualquer lib que use certifi.where() diretamente) ignora
# SSL_CERT_FILE/REQUESTS_CA_BUNDLE e confia so no bundle interno do pacote
# certifi -- anexamos o(s) CA(s) extra(s) tambem nesse arquivo. No-op se
# certs/ estiver vazia (so o .gitkeep).
RUN CERTIFI_PATH="$(python3 -c 'import certifi; print(certifi.where())')" \
    && for f in /usr/local/share/ca-certificates/extra/*.crt; do \
         [ -e "$f" ] && cat "$f" >> "$CERTIFI_PATH"; \
       done; true

COPY . .

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
