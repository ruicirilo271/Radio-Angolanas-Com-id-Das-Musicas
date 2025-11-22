# 🔹 Imagem base leve com Python 3.11
FROM python:3.11-slim

# 🔹 Instala FFmpeg (se a tua app precisar)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

# 🔹 Diretório da app
WORKDIR /app

# 🔹 Copia requirements e instala dependências
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 🔹 Copia todo o código da aplicação
COPY . .

# 🔹 Porta de execução no Render
ENV PORT=5700
EXPOSE 5700

# 🔹 Arranque do servidor (sem gevent)
CMD ["gunicorn", "app:app", "-b", "0.0.0.0:5700", "--workers", "2", "--threads", "4", "--timeout", "120"]
