FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first so this layer is cached between code changes
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app.py .

# Run as an unprivileged user, never as root
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 5000

# Worker count comes from WEB_CONCURRENCY (gunicorn reads it natively), set via .env -> compose.
ENV WEB_CONCURRENCY=2

# --preload runs init_db() once in the master, so workers in one container never race on CREATE TABLE
CMD ["gunicorn", "--preload", "--bind", "0.0.0.0:5000", "--access-logfile", "-", "app:app"]
