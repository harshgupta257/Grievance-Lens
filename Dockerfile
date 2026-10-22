# Production image for the website and API (docs/deploy.md). No database, no API key needed.
#   Build: docker build -t cpgrams-app .
#   Run:   docker run --rm -p 8000:8000 cpgrams-app      then open http://127.0.0.1:8000/
FROM python:3.11.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    APP_ENV=production \
    PORT=8000

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

# Code and the validated, committed data only (.dockerignore keeps out raw PDFs, .env and local state)
COPY src/ src/
COPY data/ data/

# Run as an unprivileged user
RUN useradd --create-home --uid 10001 app
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health', timeout=4)"

# One worker: the rate limiter lives in process memory (DESIGN.md D-046, D-049)
CMD ["sh", "-c", "exec uvicorn src.app.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --no-server-header"]
