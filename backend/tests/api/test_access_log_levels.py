"""Access-log noise control (the ``服务`` panel's 2000-line buffer).

The desktop panel renders the backend log file at INFO. UI polling
(``/v1/runs``, ``/v1/sessions/{id}/events``) used to log every hit at
INFO and filled the entire buffer within minutes, drowning host/kernel
signal. ``TimingMiddleware`` now assigns the level by what a request
says about system health; these tests pin that mapping.
"""

from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from valuz_agent.api.middleware import TimingMiddleware


@pytest.fixture()
def app_client() -> TestClient:
    app = FastAPI()
    app.add_middleware(TimingMiddleware)

    @app.get("/v1/runs")
    async def list_runs() -> dict:  # routine poll read
        return {}

    @app.post("/v1/projects")
    async def create_project() -> dict:  # mutation
        return {}

    @app.get("/v1/missing")
    async def missing() -> dict:  # failure
        from fastapi import HTTPException

        raise HTTPException(status_code=404)

    @app.get("/v1/system/status")
    async def status() -> dict:  # hard-skipped poll
        return {}

    @app.get("/.well-known/oauth-authorization-server")
    async def oauth_root() -> dict:  # MCP OAuth-discovery probe → 404 by design
        from fastapi import HTTPException

        raise HTTPException(status_code=404)

    return TestClient(app, raise_server_exceptions=False)


def _records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "valuz_agent.api.access"]


def test_successful_get_logs_at_debug(
    app_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG, logger="valuz_agent.api.access"):
        app_client.get("/v1/runs")
    [record] = _records(caplog)
    assert record.levelno == logging.DEBUG  # invisible at the panel's INFO


def test_mutation_logs_at_info(app_client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG, logger="valuz_agent.api.access"):
        app_client.post("/v1/projects")
    [record] = _records(caplog)
    assert record.levelno == logging.INFO


def test_failure_logs_at_warning(app_client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG, logger="valuz_agent.api.access"):
        app_client.get("/v1/missing")
    [record] = _records(caplog)
    assert record.levelno == logging.WARNING
    assert record.status == 404  # type: ignore[attr-defined]


def test_status_poll_is_fully_skipped(
    app_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG, logger="valuz_agent.api.access"):
        response = app_client.get("/v1/system/status")
    assert _records(caplog) == []
    # Headers still stamped — only the log line is suppressed.
    assert "X-Process-Time-Ms" in response.headers


def test_oauth_discovery_404_is_fully_skipped(
    app_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    # The MCP client probes ``/.well-known/oauth-...`` before every connection
    # to the in-process MCP mounts; our local servers carry no auth, so the
    # 404 is expected. It must NOT log at WARNING (404 ≥ 400) or it floods the
    # panel on every session's MCP connect.
    with caplog.at_level(logging.DEBUG, logger="valuz_agent.api.access"):
        response = app_client.get("/.well-known/oauth-authorization-server")
    assert response.status_code == 404
    assert _records(caplog) == []
    assert "X-Process-Time-Ms" in response.headers
