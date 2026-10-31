# Construir desde la RAÍZ del repo (necesita backend/ y frontend/):
#   docker build -f backend/Dockerfile -t evbro .
# o con `docker compose` desde deploy/ (ya apunta aquí).
FROM python:3.11-slim

WORKDIR /app

# Dependencias del sistema mínimas (`gosu` para arrancar como root solo lo justo y bajar al usuario `app`, ver entrypoint.sh)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    gosu \
    && rm -rf /var/lib/apt/lists/*

# Dependencias Python, versiones exactas (capa cacheada mientras no cambie requirements.lock)
COPY backend/requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock

# Código: backend y frontend por separado; FastAPI sirve el frontend (mismo origen)
COPY backend/src/ src/
COPY frontend/ frontend/
COPY backend/entrypoint.sh /entrypoint.sh

# Usuario sin privilegios. Persistencia (tokens, facturas, claves) en /app/data, montado como volumen; el entrypoint lo hace suyo.
RUN groupadd --system app && useradd --system --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /app/data && chown -R app:app /app && chmod +x /entrypoint.sh

# Commit del que sale la imagen (se ve en Ajustes → Sistema): --build-arg EVBRO_COMMIT=$(git rev-parse --short HEAD)
ARG EVBRO_COMMIT=""

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    EVBRO_COMMIT=${EVBRO_COMMIT}

EXPOSE 8080 8082

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8080/health || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
