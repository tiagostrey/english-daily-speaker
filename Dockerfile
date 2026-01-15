# Usa Python 3.13 Slim (leve e moderno)
FROM python:3.13-slim

# Define a pasta de trabalho
WORKDIR /app

# Instala o FFmpeg (para áudio)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copia os requisitos e instala
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia todo o código (incluindo o .env e arquivos de áudio)
COPY . .

# Comando para iniciar
CMD ["python", "main.py"]
