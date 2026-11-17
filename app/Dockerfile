FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# uv: fast drop-in replacement for pip (single static binary, pinned version)
COPY --from=ghcr.io/astral-sh/uv:0.11.19 /uv /usr/local/bin/uv

WORKDIR /app

# Copy files into the image instead of hardlinking from the cache mount
ENV UV_LINK_MODE=copy

# Dependencies come from pyproject.toml + uv.lock, so every build installs the exact
# same versions. This layer is cached between code changes: only app.py invalidates it.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev

COPY app.py .

# Run as an unprivileged user, never as root
RUN useradd --create-home --uid 10001 appuser
USER appuser

# gunicorn and the dependencies live in the uv-managed virtualenv
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 5000

# Worker count comes from WEB_CONCURRENCY (gunicorn reads it natively), set via .env -> compose.
ENV WEB_CONCURRENCY=2

# --preload runs init_db() once in the master, so workers in one container never race on CREATE TABLE
CMD ["gunicorn", "--preload", "--bind", "0.0.0.0:5000", "--access-logfile", "-", "app:app"]
