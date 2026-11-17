"""Structured logging and request correlation for the Flask service."""

import logging
import re
import sys
import time
from uuid import uuid4

import structlog
from flask import Flask, g, request
from werkzeug.wrappers import Response

REQUEST_KEY_HEADER = "X-Request-Key"
# Restrict caller-provided keys to a compact, log-safe token.
_REQUEST_KEY_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,128}\Z")


def configure_logging() -> None:
    """Render application and standard-library log records as JSON."""
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root_logger = logging.getLogger()
    root_logger.handlers[:] = [handler]
    root_logger.setLevel(logging.INFO)
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def register_request_logging(app: Flask) -> None:
    """Bind a request key and emit one completion log for each response."""
    logger = structlog.get_logger(__name__)

    @app.before_request
    def begin_request() -> None:
        structlog.contextvars.clear_contextvars()
        supplied_key = request.headers.get(REQUEST_KEY_HEADER, "")
        g.request_key = (
            supplied_key
            if _REQUEST_KEY_PATTERN.fullmatch(supplied_key)
            else uuid4().hex
        )
        g.request_started_at = time.monotonic()
        structlog.contextvars.bind_contextvars(request_key=g.request_key)

    @app.after_request
    def finish_request(response: Response) -> Response:
        response.headers[REQUEST_KEY_HEADER] = g.request_key
        logger.info(
            "request_completed",
            method=request.method,
            path=request.path,
            status_code=response.status_code,
            duration_ms=round((time.monotonic() - g.request_started_at) * 1000, 2),
        )
        return response

    @app.teardown_request
    def clear_request_context(error: BaseException | None) -> None:
        structlog.contextvars.clear_contextvars()
