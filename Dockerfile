FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY hermes_benchmark ./hermes_benchmark

RUN python -m pip install --no-cache-dir . \
    && mkdir -p /app/staging \
    && printf '%s\n' '{"ok":true,"service":"hermes-trendradar"}' > /app/staging/healthz

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).read()"

CMD ["python", "-m", "http.server", "8080", "--bind", "0.0.0.0", "--directory", "/app/staging"]
