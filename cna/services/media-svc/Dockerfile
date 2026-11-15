FROM python:3.12-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim

# Parches de seguridad del sistema base (Trivy)
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
RUN groupadd -g 10001 appgroup && useradd -u 10001 -g appgroup -m appuser
WORKDIR /app
COPY --from=builder /install /usr/local
COPY app ./app
USER 10001:10001
EXPOSE 8004
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8004/healthz')"
# Trazas OpenTelemetry: desactivadas salvo que compose defina OTEL_TRACES_EXPORTER=otlp
ENV OTEL_TRACES_EXPORTER=none \
    OTEL_METRICS_EXPORTER=none \
    OTEL_LOGS_EXPORTER=none

CMD ["opentelemetry-instrument", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8004", "--no-access-log"]
