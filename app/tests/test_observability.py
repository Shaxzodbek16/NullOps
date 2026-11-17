import json
import logging

import pytest
import structlog
from flask import Flask

from observability import configure_logging, register_request_logging


@pytest.fixture
def client():
    app = Flask(__name__)
    app.config.update(TESTING=False, PROPAGATE_EXCEPTIONS=False)
    register_request_logging(app)

    @app.get("/")
    def index():
        structlog.get_logger().info("inside_request")
        return "ok"

    @app.get("/error")
    def error():
        raise RuntimeError("test error")

    return app.test_client()


def test_supplied_key_is_returned_and_bound(client):
    response = client.get("/", headers={"X-Request-Key": "client-123"})
    assert response.headers["X-Request-Key"] == "client-123"
    assert structlog.contextvars.get_contextvars() == {}


@pytest.mark.parametrize("key", ["", "bad key", "x" * 129])
def test_invalid_key_is_replaced(client, key):
    response = client.get("/", headers={"X-Request-Key": key})
    assert len(response.headers["X-Request-Key"]) == 32
    assert response.headers["X-Request-Key"] != key


def test_missing_keys_are_unique_and_error_responses_have_key(client):
    first = client.get("/")
    second = client.get("/error")
    assert second.status_code == 500
    assert first.headers["X-Request-Key"] != second.headers["X-Request-Key"]
    assert structlog.contextvars.get_contextvars() == {}


def test_structured_and_standard_logs_include_request_key(client, capsys):
    root = logging.getLogger()
    previous_handlers, previous_level = root.handlers[:], root.level
    try:
        configure_logging()
        response = client.get("/error", headers={"X-Request-Key": "error-123"})
        records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        assert response.status_code == 500
        assert all(record["request_key"] == "error-123" for record in records)
        assert any(record["event"] == "request_completed" for record in records)
        assert any("test error" in record.get("exception", "") for record in records)
    finally:
        root.handlers[:] = previous_handlers
        root.setLevel(previous_level)
