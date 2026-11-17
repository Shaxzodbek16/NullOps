"""Small Flask service backed by MariaDB.

Endpoints:
  GET /         - records a visit and returns the total visit count
  GET /health   - liveness + database readiness check (used by Docker healthcheck)
"""
import os
import socket

import pymysql
from flask import Flask, jsonify

app = Flask(__name__)

# Name of the MariaDB named lock used to serialize schema init across containers
SCHEMA_LOCK_NAME = "nullops_schema_init"


def db_connect() -> pymysql.connections.Connection:
    return pymysql.connect(
        host=os.environ.get("DB_HOST", "db"),
        port=int(os.environ.get("DB_PORT", "3306")),
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASS"],
        database=os.environ["DB_NAME"],
        connect_timeout=3,
    )


def init_db() -> None:
    conn = db_connect()
    try:
        with conn.cursor() as cur:
            # Several replicas start at once (docker compose up --scale app=N) and all run
            # this. A named lock makes them create the schema one at a time.
            cur.execute("SELECT GET_LOCK(%s, 10)", (SCHEMA_LOCK_NAME,))
            try:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS visits (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        hostname VARCHAR(255) NOT NULL,
                        visited_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
            finally:
                cur.execute("SELECT RELEASE_LOCK(%s)", (SCHEMA_LOCK_NAME,))
        conn.commit()
    finally:
        conn.close()


@app.get("/")
def index():
    hostname = socket.gethostname()
    # No application lock needed: each INSERT is atomic in InnoDB, and the count runs in
    # the same transaction, so it always includes this visit plus every committed one.
    # A Python lock would not help anyway: it cannot span gunicorn workers or replicas.
    conn = db_connect()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO visits (hostname) VALUES (%s)", (hostname,))
            cur.execute("SELECT COUNT(*) FROM visits")
            total = cur.fetchone()[0]
        conn.commit()
    finally:
        conn.close()
    return jsonify(
        message="Hello from NullOps Flask app",
        served_by=hostname,
        total_visits=total,
    )


@app.get("/health")
def health():
    try:
        conn = db_connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        finally:
            conn.close()
    except pymysql.MySQLError as exc:
        return jsonify(status="unhealthy", database=str(exc)), 503
    return jsonify(status="ok", database="reachable")


init_db()
