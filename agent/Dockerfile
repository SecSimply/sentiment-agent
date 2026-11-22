FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY app ./app
RUN useradd -r app && chown -R app /app
USER app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
# Railway injects PORT. Bind IPv4 0.0.0.0: this service only receives public traffic (outbound private calls need no listener).
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
