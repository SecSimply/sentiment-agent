FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY app ./app
RUN useradd -r app && chown -R app /app
USER app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
# Railway injects PORT. `::` = dual-stack so the service also works on Railway's IPv6 private network.
CMD ["sh", "-c", "uvicorn app.main:app --host :: --port ${PORT:-8000}"]
