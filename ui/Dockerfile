FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY server ./server
COPY --from=web /web/dist ./static
RUN useradd -r app && chown -R app /app
USER app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
# One worker on purpose: SQLite has a single writer and a Railway volume attaches to a single instance.
CMD ["sh", "-c", "uvicorn server.asgi:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
