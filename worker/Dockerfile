# Worker: roda o motor de verdade (download, transcricao, traducao,
# dublagem, render) -- precisa do ffmpeg no sistema e das libs pesadas
# (faster-whisper, google-genai, boto3).
# Build context = esta propria pasta (worker/) -- imagem 100% autocontida,
# inclui o motor inteiro (engine/).
FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "worker.py"]
