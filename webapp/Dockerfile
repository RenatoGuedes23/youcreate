# Site (FastAPI): so recebe pedidos e enfileira -- sem ffmpeg, sem as libs
# pesadas de processamento (essas ficam so no worker, servico separado).
# Build context = esta propria pasta (webapp/) -- imagem 100% autocontida.
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
