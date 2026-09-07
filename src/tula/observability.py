"""Privacy-bounded operational events and HTTP request correlation.

Operational logs are separate from the evidentiary audit trail. They help an
operator diagnose a running service without retaining request bodies, query
strings, OCR text, uploaded filenames, evidence paths, cookies or passwords.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime

from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse

REQUEST_ID: ContextVar[str] = ContextVar("tula_request_id", default="")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,63}$")
_SAFE_FIELDS = frozenset({
    "request_id", "method", "route", "status", "duration_ms", "stage_elapsed_ms",
    "total_elapsed_ms", "job_id", "scan_id", "attempt", "stage", "frames",
    "rules_version", "format", "revision", "artifact_id", "error_type", "state",
})


def _request_id(value: str | None) -> str:
    candidate = (value or "").strip()
    return candidate if _REQUEST_ID.fullmatch(candidate) else uuid.uuid4().hex


def log_event(logger: logging.Logger, level: int, event: str, **fields) -> None:
    """Emit one compact JSON object containing only an allow-list of metadata."""
    safe = {
        key: value for key, value in fields.items()
        if key in _SAFE_FIELDS
        and value is not None
        and isinstance(value, (str, int, float, bool))
    }
    if "request_id" in safe and not _REQUEST_ID.fullmatch(str(safe["request_id"])):
        safe.pop("request_id")
    if not safe.get("request_id") and REQUEST_ID.get():
        safe["request_id"] = REQUEST_ID.get()
    payload = {
        "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "level": logging.getLevelName(level),
        "event": str(event)[:80],
        **safe,
    }
    # Extras keep caplog and log collectors queryable while the message itself is
    # valid JSON under a default uvicorn logging configuration.
    logger.log(
        level,
        json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
        extra={"event": payload["event"], **safe},
    )


def _route(scope: dict) -> str:
    route = scope.get("route")
    template = getattr(route, "path", None) or getattr(route, "path_format", None)
    if isinstance(template, str) and template.startswith("/"):
        return template[:160]
    path = scope.get("path", "")
    # Security can reject a request before routing. Exact public/static paths are
    # safe; arbitrary unmatched user input is not copied into logs.
    if path in {"/", "/login", "/logout", "/healthz", "/docs", "/redoc"}:
        return path
    if isinstance(path, str) and path.startswith("/static/"):
        return "/static/{asset}"
    return "<unmatched>"


class RequestObservabilityMiddleware:
    """Correlate every HTTP response without retaining user-supplied content."""

    def __init__(self, app, *, logger: logging.Logger | None = None):
        self.app = app
        self.logger = logger or logging.getLogger("tula.http")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        request_id = _request_id(
            headers.get(b"x-request-id", b"").decode("ascii", "ignore")
        )
        token = REQUEST_ID.set(request_id)
        started = time.perf_counter()
        status = 500
        response_started = False

        async def observed_send(message):
            nonlocal response_started, status
            if message["type"] == "http.response.start":
                response_started = True
                status = int(message["status"])
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        error_type = None
        try:
            await self.app(scope, receive, observed_send)
        except Exception as exc:
            error_type = type(exc).__name__
            if response_started:
                raise
            response = JSONResponse(
                {
                    "detail": "The request could not be completed. "
                    f"Use request ID {request_id} when contacting support.",
                    "request_id": request_id,
                },
                status_code=500,
            )
            await response(scope, receive, observed_send)
        finally:
            elapsed = round((time.perf_counter() - started) * 1000, 2)
            level = logging.ERROR if status >= 500 or error_type else (
                logging.WARNING if status >= 400 else logging.INFO
            )
            log_event(
                self.logger,
                level,
                "http_request_completed",
                request_id=request_id,
                method=scope.get("method", "")[:12],
                route=_route(scope),
                status=status,
                duration_ms=elapsed,
                error_type=error_type,
            )
            REQUEST_ID.reset(token)


class _SuppressUvicornAccess(logging.Filter):
    """The default access line includes the raw query string and duplicates our event."""

    def filter(self, record: logging.LogRecord) -> bool:
        return False


def suppress_default_access_log() -> None:
    """Prevent uvicorn from logging URLs that can contain inspection search data."""
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, _SuppressUvicornAccess) for item in logger.filters):
        logger.addFilter(_SuppressUvicornAccess())


class _OperationalEventOnly(logging.Filter):
    """Keep legacy exception messages and tracebacks out of this output channel."""

    def filter(self, record: logging.LogRecord) -> bool:
        return hasattr(record, "event") and str(record.getMessage()).startswith("{")


def install_operational_logging() -> logging.Handler:
    """Make structured INFO events visible under Uvicorn's default configuration."""
    logger = logging.getLogger("tula")
    for handler in logger.handlers:
        if getattr(handler, "_tula_operational", False):
            return handler
    handler = logging.StreamHandler()
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.addFilter(_OperationalEventOnly())
    handler._tula_operational = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
    if logger.level == logging.NOTSET or logger.level > logging.INFO:
        logger.setLevel(logging.INFO)
    # Keep propagation so host collectors and pytest's caplog can also receive the
    # records. Deployments should avoid adding a second application-event handler.
    return handler
