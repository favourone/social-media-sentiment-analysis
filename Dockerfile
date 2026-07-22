FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --system app && useradd --system --gid app --home-dir /app app

COPY requirements.txt requirements.lock ./
RUN python -m pip install --upgrade pip && python -m pip install -r requirements.lock

COPY . .
RUN mkdir -p /app/runtime/reports /app/runtime/imports && chown -R app:app /app

USER app
EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/api/v1/health/live', timeout=3)"

CMD ["waitress-serve", "--listen=0.0.0.0:5000", "web.app:app"]
