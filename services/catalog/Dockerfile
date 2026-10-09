FROM ghcr.io/astral-sh/uv:0.9.4@sha256:c4089b0085cf4d38e38d5cdaa5e57752c1878a6f41f2e3a3a234dc5f23942cb4 AS uv
FROM python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY services/catalog/pyproject.toml services/catalog/uv.lock ./
RUN uv sync --frozen --no-dev --no-cache
COPY services/catalog/app.py services/catalog/telemetry.py ./
RUN useradd --uid 10001 --create-home workshop
USER 10001
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["/app/.venv/bin/uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
