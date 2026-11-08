# Build context: the repository root (see docker-compose.yml)
FROM python:3.12-slim

# ffprobe (deep language scan) and the DejaVu font (badges on posters)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/tidyfin ./tidyfin
COPY frontend ./static

ENV TIDYFIN_DATA_DIR=/data \
    TIDYFIN_STATIC_DIR=/app/static \
    PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8484

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8484/api/health', timeout=4).status==200 else 1)"

CMD ["python", "-m", "tidyfin"]
