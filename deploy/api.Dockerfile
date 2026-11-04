# API de Aleph (FastAPI + motores). Contexto de build: la raíz del repositorio.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONUTF8=1 \
    ALEPH_DATA_DIR=/data
WORKDIR /app

COPY backend/pyproject.toml backend/
COPY backend/aleph backend/aleph
RUN pip install --no-cache-dir "./backend[prod]" \
 && useradd --system --uid 10001 --home /app aleph \
 && mkdir -p /data && chown aleph /data

COPY deploy/api-entrypoint.sh /usr/local/bin/aleph-entrypoint
RUN chmod +x /usr/local/bin/aleph-entrypoint
USER aleph
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"
ENTRYPOINT ["aleph-entrypoint"]
# Un solo proceso: la cadena de auditoría se serializa dentro del proceso y, además, en la base.
CMD ["uvicorn", "aleph.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
