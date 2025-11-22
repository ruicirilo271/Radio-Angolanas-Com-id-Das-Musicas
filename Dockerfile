# 🔹 Imagem base leve com Python 3.11
FROM python:3.11-slim

# 🔹 Instala FFmpeg real (estável) e dependências do sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# 🔹 Diretório de trabalho
WORKDIR /app

# 🔹 Copia requirements e instala dependências
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 🔹 Copia toda a aplicação
COPY . .

# 🔹 Define e expõe a porta usada pelo Render
ENV PORT=5700
EXPOSE 5700

# 🔹 Inicia a app com Gunicorn sem gevent
CMD ["gunicorn", "app:app", "-b", "0.0.0.0:5700", "--workers", "2", "--threads", "4", "--timeout", "120"]
