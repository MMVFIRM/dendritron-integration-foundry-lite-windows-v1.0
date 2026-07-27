FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1
WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m pip install --upgrade pip build && python -m build --wheel --outdir /wheel

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN groupadd --system difoundry && useradd --system --gid difoundry --home-dir /app difoundry
COPY --from=builder /wheel/*.whl /tmp/
RUN python -m pip install --upgrade pip && \
    python -m pip install "/tmp/$(basename $(ls /tmp/*.whl))[production]" && \
    rm -f /tmp/*.whl && \
    chown -R difoundry:difoundry /app
USER difoundry
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/platform/liveness', timeout=2)"
CMD ["uvicorn", "difoundry.api:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
