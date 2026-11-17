"""Small Flask service backed by MariaDB, with every credential stored in Vault.

The container holds no database password. At startup it logs into Vault with its
AppRole (role_id / secret_id are mounted from a volume filled by vault-bootstrap)
and reads the credentials from the KV v2 mount.

Endpoints:
  GET /         - records a visit and returns the total visit count
  GET /health   - liveness + database readiness check (used by Docker healthcheck)
"""
import os
import socket
import time

import hvac
import pymysql
from flask import Flask, jsonify

app = Flask(__name__)

# Name of the MariaDB named lock used to serialize schema init across containers
SCHEMA_LOCK_NAME = "nullops_schema_init"

# MariaDB error codes that mean "these credentials are no longer valid"
AUTH_ERRNOS = (1044, 1045)

_db_credentials = None


def _read_file(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read().strip()


def fetch_db_credentials(attempts: int = 10, delay: float = 2.0) -> dict:
    """Log into Vault with the AppRole and read the database credentials.

    Compose waits for vault-bootstrap to finish before starting this container, but a
    short retry keeps a restarting Vault from taking the app down with it.
    """
    role_id = _read_file(os.environ.get("VAULT_ROLE_ID_FILE", "/vault/approle/role_id"))
    secret_id = _read_file(os.environ.get("VAULT_SECRET_ID_FILE", "/vault/approle/secret_id"))
    mount = os.environ.get("VAULT_KV_MOUNT", "nullops")
    path = os.environ.get("VAULT_DB_SECRET_PATH", "db/app")

    last_error = None
    for attempt in range(attempts):
        try:
            client = hvac.Client(url=os.environ.get("VAULT_ADDR", "http://vault:8200"), timeout=5)
            client.auth.approle.login(role_id=role_id, secret_id=secret_id)
            secret = client.secrets.kv.v2.read_secret_version(
                path=path, mount_point=mount, raise_on_deleted_version=True
            )
            data = secret["data"]["data"]
            return {
                "user": data["username"],
                "password": data["password"],
                "database": data["database"],
            }
        except Exception as exc:  # noqa: BLE001 - network, auth and KV errors all retry
            last_error = exc
            if attempt < attempts - 1:
                time.sleep(delay)
    raise RuntimeError(f"could not read the database credentials from Vault: {last_error}")


def db_credentials(refresh: bool = False) -> dict:
    """Cached credentials; gunicorn --preload loads them once in the master process."""
    global _db_credentials
    if refresh or _db_credentials is None:
        _db_credentials = fetch_db_credentials()
    return _db_credentials


def _connect(creds: dict) -> pymysql.connections.Connection:
    return pymysql.connect(
        host=os.environ.get("DB_HOST", "db"),
        port=int(os.environ.get("DB_PORT", "3306")),
        user=creds["user"],
        password=creds["password"],
        database=creds["database"],
        connect_timeout=3,
    )


def db_connect() -> pymysql.connections.Connection:
    try:
        return _connect(db_credentials())
    except pymysql.err.OperationalError as exc:
        # The password was rotated in Vault while this process was running: fetch the
        # new version once instead of failing until the container is restarted.
        if exc.args[0] not in AUTH_ERRNOS:
            raise
        return _connect(db_credentials(refresh=True))


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
    return jsonify(status="ok", database="reachable", secrets="vault")


init_db()
