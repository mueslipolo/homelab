# Runtime image: no OCR engine, no ML model, no compiler.
# Paperless has already OCR'd the document by the time we see it, so all we need
# is a PDF text layer reader and an HTTP client.
FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PAPERLESS_TARMED_CACHE_DIR=/data/cache \
    PAPERLESS_TARMED_TARIFF_PATH=/data/tariff_types.json

WORKDIR /app

COPY pyproject.toml README.md ./
COPY paperless_tarmed ./paperless_tarmed

# pypdfium2 and httpx ship manylinux wheels for x86_64, aarch64 and armv7l, so
# this resolves without a build toolchain on any Synology architecture.
RUN pip install --no-cache-dir ".[serve]"

RUN useradd --create-home --uid 1000 paperless-tarmed \
    && mkdir -p /data/cache \
    && chown -R paperless-tarmed:paperless-tarmed /data
USER paperless-tarmed

EXPOSE 8000

HEALTHCHECK --interval=60s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status==200 else 1)"

ENTRYPOINT ["paperless-tarmed"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
