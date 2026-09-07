"""Operational events are useful for diagnosis without becoming a data leak."""
from __future__ import annotations

import json
import logging
from io import StringIO

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from tula.observability import (
    RequestObservabilityMiddleware,
    install_operational_logging,
    log_event,
    suppress_default_access_log,
)
from tula.security import SecurityStore, install_security


def _events(caplog, name: str, event: str):
    return [record for record in caplog.records
            if record.name == name and getattr(record, "event", None) == event]


def test_event_payload_is_json_and_drops_unapproved_or_sensitive_fields(caplog):
    logger = logging.getLogger("tula.test.events")
    with caplog.at_level(logging.INFO, logger=logger.name):
        log_event(
            logger,
            logging.INFO,
            "inspection_stage",
            job_id="job-12345678",
            stage="ocr",
            duration_ms=12.5,
            transcription="MRP private label text",
            password="never-log-this",
            path="C:/private/evidence.png",
        )
    record, = _events(caplog, logger.name, "inspection_stage")
    payload = json.loads(record.getMessage())
    assert payload["event"] == "inspection_stage"
    assert payload["job_id"] == "job-12345678"
    assert payload["stage"] == "ocr" and payload["duration_ms"] == 12.5
    assert "timestamp" in payload and payload["level"] == "INFO"
    assert "private" not in record.getMessage()
    assert "password" not in record.getMessage()
    assert "evidence.png" not in record.getMessage()


def test_http_request_id_route_template_and_privacy_boundary(caplog):
    app = FastAPI()
    app.add_middleware(RequestObservabilityMiddleware)

    @app.get("/items/{item_id}")
    def item(item_id: str, request: Request):
        log_event(
            logging.getLogger("tula.test.child"),
            logging.INFO,
            "inside_request",
            state="ok",
            query=str(request.url.query),
            item=item_id,
        )
        return {"ok": True}

    requested_id = "browser-correlation-123"
    with caplog.at_level(logging.INFO), TestClient(app) as client:
        response = client.get(
            "/items/private-product-name",
            params={"password": "query-secret"},
            headers={"X-Request-ID": requested_id},
        )
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == requested_id
    request_event, = _events(caplog, "tula.http", "http_request_completed")
    assert request_event.request_id == requested_id
    assert request_event.route == "/items/{item_id}"
    assert request_event.method == "GET" and request_event.status == 200
    assert request_event.duration_ms >= 0
    child_event, = _events(caplog, "tula.test.child", "inside_request")
    assert child_event.request_id == requested_id and child_event.state == "ok"
    rendered = "\n".join(
        record.getMessage() for record in caplog.records if record.name.startswith("tula.")
    )
    assert "private-product-name" not in rendered
    assert "query-secret" not in rendered


def test_invalid_correlation_value_is_replaced_and_unmatched_path_is_redacted(caplog):
    app = FastAPI()
    app.add_middleware(RequestObservabilityMiddleware)
    with caplog.at_level(logging.WARNING, logger="tula.http"), TestClient(app) as client:
        response = client.get(
            "/unknown/private-package-name",
            headers={"X-Request-ID": "bad id\nforged"},
        )
    assert response.status_code == 404
    assigned = response.headers["X-Request-ID"]
    assert len(assigned) == 32 and assigned.isalnum()
    event, = _events(caplog, "tula.http", "http_request_completed")
    assert event.request_id == assigned and event.route == "<unmatched>" and event.status == 404
    assert "private-package-name" not in event.getMessage()
    assert "forged" not in event.getMessage()


def test_server_exception_records_only_type_and_correlation(caplog):
    app = FastAPI()
    app.add_middleware(RequestObservabilityMiddleware)

    @app.get("/fail")
    def fail():
        raise RuntimeError("private database detail")

    with caplog.at_level(logging.ERROR, logger="tula.http"), TestClient(
        app, raise_server_exceptions=False
    ) as client:
        response = client.get("/fail")
    assert response.status_code == 500
    event, = _events(caplog, "tula.http", "http_request_completed")
    assert event.status == 500 and event.error_type == "RuntimeError"
    assert event.route == "/fail"
    assert response.headers["X-Request-ID"] == event.request_id
    assert response.json()["request_id"] == event.request_id
    assert event.request_id in response.json()["detail"]
    assert "private database detail" not in event.getMessage()
    assert "private database detail" not in response.text


def test_outer_observability_covers_authentication_rejection(tmp_path, caplog):
    app = FastAPI()
    store = SecurityStore(tmp_path / "security.db")
    store.create_user(
        "admin", "Strong observability password 2026!", role="admin", bootstrap=True
    )
    install_security(app, store.path)
    app.add_middleware(RequestObservabilityMiddleware)

    @app.get("/protected")
    def protected():
        return {"ok": True}

    with caplog.at_level(logging.WARNING, logger="tula.http"), TestClient(
        app, base_url="https://testserver"
    ) as client:
        response = client.get("/protected", headers={"Accept": "application/json"})
    assert response.status_code == 401
    event, = _events(caplog, "tula.http", "http_request_completed")
    assert event.status == 401 and event.route == "<unmatched>"
    assert response.headers["X-Request-ID"] == event.request_id


def test_default_access_line_is_suppressed_without_disabling_other_uvicorn_logs():
    access = logging.getLogger("uvicorn.access")
    server = logging.getLogger("uvicorn.error")
    before_access = list(access.filters)
    before_server = list(server.filters)
    try:
        suppress_default_access_log()
        suppress_default_access_log()
        installed = [
            item for item in access.filters
            if type(item).__module__ == "tula.observability"
            and type(item).__name__ == "_SuppressUvicornAccess"
        ]
        assert len(installed) == 1
        record = logging.LogRecord(
            "uvicorn.access", logging.INFO, __file__, 1,
            '%s - "%s %s HTTP/%s" %d',
            ("127.0.0.1", "GET", "/search?q=private", "1.1", 200), None,
        )
        assert installed[0].filter(record) is False
        assert server.filters == before_server
    finally:
        access.filters[:] = before_access


def test_operational_handler_emits_events_but_not_legacy_exception_text():
    parent = logging.getLogger("tula")
    handler = install_operational_logging()
    original_stream = handler.stream
    stream = StringIO()
    handler.setStream(stream)
    try:
        log_event(logging.getLogger("tula.test.runtime"), logging.INFO, "runtime_ready", state="ok")
        try:
            raise RuntimeError("private traceback value")
        except RuntimeError:
            logging.getLogger("tula.test.runtime").exception("legacy_failure")
        lines = stream.getvalue().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["event"] == "runtime_ready"
        assert "private traceback value" not in stream.getvalue()
    finally:
        handler.setStream(original_stream)
    assert handler in parent.handlers
