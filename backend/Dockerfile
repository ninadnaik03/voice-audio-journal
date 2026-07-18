FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VOICE_MODEL_DIR=/app/model_cache/ecapa

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements-auth.txt .
RUN pip install --no-cache-dir -r requirements-auth.txt
COPY auth_server.py .

EXPOSE 7860
CMD ["sh", "-c", "uvicorn auth_server:app --host 0.0.0.0 --port ${PORT:-7860}"]
