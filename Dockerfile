# API image. Built for the server's CPU (arm64 for t4g instances; see deploy/README.md).
# Only code and NON-secret config are baked in. Secrets and credentials are never copied here.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 FASTEMBED_CACHE_PATH=/srv/data/models
WORKDIR /srv

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY config.yaml .

# Run as a normal user. creds/ and data/ are mounted volumes (never part of the image).
RUN useradd --create-home --uid 10001 app && mkdir -p /srv/creds /srv/data && chown -R app:app /srv
USER app

EXPOSE 8000
# Hourly once running; every 5 s while starting, so 'healthy' still shows up within seconds.
HEALTHCHECK --interval=1h --timeout=5s --start-period=60s --start-interval=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
