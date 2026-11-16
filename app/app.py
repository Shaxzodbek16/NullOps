"""Small Flask service backed by PostgreSQL.

Endpoints:
  GET /         - records a visit and returns the total visit count
  GET /health   - liveness + database readiness check (used by Docker healthcheck)
"""
import os
import socket

import psycopg
from flask import Flask, jsonify

app = Flask(__name__)


def db_conninfo() -> str:
    return (
        f"host={os.environ.get('DB_HOST', 'db')} "
        f"port={os.environ.get('DB_PORT', '5432')} "
        f"dbname={os.environ['DB_NAME']} "
        f"user={os.environ['DB_USER']} "
        f"password={os.environ['DB_PASS']} "
        "connect_timeout=3"
    )


# Arbitrary constant key for the schema-init advisory lock
SCHEMA_LOCK_KEY = 4242


def init_db() -> None:
    with psycopg.connect(db_conninfo()) as conn:
        # Several replicas start at once (docker compose up --scale app=N) and all run this.
        # Concurrent CREATE TABLE IF NOT EXISTS can still fail with a unique violation in
        # Postgres, so serialize schema init across containers. The lock is released
        # automatically when this transaction commits.
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (SCHEMA_LOCK_KEY,))
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS visits (
                id SERIAL PRIMARY KEY,
                hostname TEXT NOT NULL,
                visited_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )


@app.get("/")
def index():
    hostname = socket.gethostname()
    # No application lock needed: each INSERT is atomic in Postgres, and the count runs in
    # the same transaction, so it always includes this visit plus every committed one.
    # A Python lock would not help anyway: it cannot span gunicorn workers or replicas.
    with psycopg.connect(db_conninfo()) as conn:
        conn.execute("INSERT INTO visits (hostname) VALUES (%s)", (hostname,))
        total = conn.execute("SELECT count(*) FROM visits").fetchone()[0]
    return jsonify(
        message="Hello from NullOps Flask app",
        served_by=hostname,
        total_visits=total,
    )


@app.get("/health")
def health():
    try:
        with psycopg.connect(db_conninfo()) as conn:
            conn.execute("SELECT 1")
    except psycopg.Error as exc:
        return jsonify(status="unhealthy", database=str(exc).splitlines()[0]), 503
    return jsonify(status="ok", database="reachable")


init_db()
