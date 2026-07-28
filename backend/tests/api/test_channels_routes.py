from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from valuz_agent.api.deps import get_channel_ingress_service
from valuz_agent.api.routes import channels as channels_routes


class _Uow:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *exc: object) -> None:
        return None


class _FakeAgentChannelBindingDatastore:
    binding: Any = None

    def __init__(self, _db: object) -> None:
        pass

    async def get(
        self,
        *,
        user_id: str,
        platform: str,
        agent_slug: str,
    ) -> Any:
        if (
            self.binding is not None
            and self.binding.owner_user_id == user_id
            and self.binding.platform == platform
            and self.binding.agent_slug == agent_slug
        ):
            return self.binding
        return None

    async def get_enabled_by_channel_instance(
        self,
        *,
        platform: str,
        channel_instance_id: str,
    ) -> Any:
        if (
            self.binding is None
            or self.binding.platform != platform
            or self.binding.channel_instance_id != channel_instance_id
            or not self.binding.enabled
        ):
            return None
        return self.binding

    async def upsert(
        self,
        *,
        user_id: str,
        platform: str,
        agent_slug: str,
        channel_instance_id: str,
        bot_id: str,
        secret_ref: str | None,
        enabled: bool,
        bot_name: str | None = None,
        ws_url: str | None = None,
    ) -> Any:
        self.__class__.binding = SimpleNamespace(
            id="binding-1",
            owner_user_id=user_id,
            platform=platform,
            channel_instance_id=channel_instance_id,
            agent_slug=agent_slug,
            bot_id=bot_id,
            secret_ref=secret_ref,
            enabled=enabled,
            bot_name=bot_name,
            ws_url=ws_url,
        )
        return self.__class__.binding


class _FakeSupervisor:
    def status_for(self, _agent_slug: str) -> SimpleNamespace:
        return SimpleNamespace(status="stopped", connected=False, last_error=None)

    async def restart(self) -> None:
        return None


def test_feishu_url_verification_uses_bound_agent_secret(
    monkeypatch,
) -> None:
    _FakeAgentChannelBindingDatastore.binding = SimpleNamespace(
        id="binding-1",
        owner_user_id="u1",
        platform="feishu",
        channel_instance_id="feishu-main",
        agent_slug="developer",
        bot_id="cli_app_1",
        secret_ref="channel/feishu/developer",
        enabled=True,
        bot_name=None,
        ws_url=None,
    )
    monkeypatch.setattr(channels_routes, "async_unit_of_work", lambda: _Uow())
    monkeypatch.setattr(
        channels_routes,
        "AgentChannelBindingDatastore",
        _FakeAgentChannelBindingDatastore,
    )
    monkeypatch.setattr(
        channels_routes.secret_store,
        "get",
        lambda user_id, ref: (
            json.dumps({"verification_token": "verify-token", "encrypt_key": ""})
            if user_id == "u1" and ref == "channel/feishu/developer"
            else None
        ),
    )
    app = FastAPI()
    app.include_router(channels_routes.router)
    app.dependency_overrides[get_channel_ingress_service] = lambda: object()

    response = TestClient(app).post(
        "/v1/channels/feishu/feishu-main/callback",
        json={
            "type": "url_verification",
            "token": "verify-token",
            "challenge": "challenge-code",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"challenge": "challenge-code"}


def test_update_feishu_binding_stores_token_payload(
    monkeypatch,
) -> None:
    _FakeAgentChannelBindingDatastore.binding = None
    saved: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(channels_routes, "async_unit_of_work", lambda: _Uow())
    monkeypatch.setattr(
        channels_routes,
        "AgentChannelBindingDatastore",
        _FakeAgentChannelBindingDatastore,
    )
    monkeypatch.setattr(
        channels_routes.secret_store,
        "get",
        lambda user_id, ref: saved.get((user_id, ref)),
    )
    monkeypatch.setattr(
        channels_routes.secret_store,
        "put",
        lambda user_id, ref, value: saved.__setitem__((user_id, ref), value),
    )
    monkeypatch.setattr(channels_routes, "feishu_supervisor", _FakeSupervisor())
    app = FastAPI()
    app.include_router(channels_routes.router)
    app.dependency_overrides[channels_routes.get_current_user_id] = lambda: "u1"

    response = TestClient(app).put(
        "/v1/channels/feishu/bindings/developer",
        json={
            "enabled": True,
            "agent_slug": "developer",
            "app_id": "cli_app_1",
            "app_secret": "app-secret",
            "verification_token": "verify-token",
            "encrypt_key": "encrypt-key",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "enabled": True,
        "channel_instance_id": "feishu-main",
        "owner_user_id": "u1",
        "agent_slug": "developer",
        "app_id": "cli_app_1",
        "has_app_secret": True,
        "has_verification_token": True,
        "has_encrypt_key": True,
        "connected": False,
        "connection_status": "stopped",
        "connection_error": None,
    }
    assert json.loads(saved[("u1", "channel/feishu/developer")]) == {
        "app_secret": "app-secret",
        "verification_token": "verify-token",
        "encrypt_key": "encrypt-key",
    }


def test_update_feishu_binding_requires_app_secret_for_new_binding(
    monkeypatch,
) -> None:
    _FakeAgentChannelBindingDatastore.binding = None
    saved: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(channels_routes, "async_unit_of_work", lambda: _Uow())
    monkeypatch.setattr(
        channels_routes,
        "AgentChannelBindingDatastore",
        _FakeAgentChannelBindingDatastore,
    )
    monkeypatch.setattr(
        channels_routes.secret_store,
        "get",
        lambda user_id, ref: saved.get((user_id, ref)),
    )
    monkeypatch.setattr(channels_routes, "feishu_supervisor", _FakeSupervisor())
    app = FastAPI()
    app.include_router(channels_routes.router)
    app.dependency_overrides[channels_routes.get_current_user_id] = lambda: "u1"

    response = TestClient(app).put(
        "/v1/channels/feishu/bindings/developer",
        json={
            "enabled": True,
            "agent_slug": "developer",
            "app_id": "cli_app_1",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "App Secret is required"


def test_test_feishu_binding_probes_credentials_and_reports_status(
    monkeypatch,
) -> None:
    _FakeAgentChannelBindingDatastore.binding = SimpleNamespace(
        id="binding-1",
        owner_user_id="u1",
        platform="feishu",
        channel_instance_id="feishu-main",
        agent_slug="developer",
        bot_id="cli_app_1",
        secret_ref="channel/feishu/developer",
        enabled=True,
        bot_name=None,
        ws_url=None,
    )
    saved = {
        ("u1", "channel/feishu/developer"): json.dumps({"app_secret": "app-secret"})
    }
    monkeypatch.setattr(channels_routes, "async_unit_of_work", lambda: _Uow())
    monkeypatch.setattr(
        channels_routes,
        "AgentChannelBindingDatastore",
        _FakeAgentChannelBindingDatastore,
    )
    monkeypatch.setattr(
        channels_routes.secret_store,
        "get",
        lambda user_id, ref: saved.get((user_id, ref)),
    )
    monkeypatch.setattr(channels_routes, "feishu_supervisor", _FakeSupervisor())
    probes: list[tuple[str, str]] = []

    async def fake_check(app_id: str, app_secret: str) -> tuple[bool, str | None]:
        probes.append((app_id, app_secret))
        return True, None

    monkeypatch.setattr(channels_routes, "_check_feishu_credentials", fake_check)
    app = FastAPI()
    app.include_router(channels_routes.router)
    app.dependency_overrides[channels_routes.get_current_user_id] = lambda: "u1"

    response = TestClient(app).post("/v1/channels/feishu/bindings/developer/test")

    assert response.status_code == 200
    assert response.json() == {
        "credential_ok": True,
        "error": None,
        "connected": False,
        "connection_status": "stopped",
        "connection_error": None,
    }
    assert probes == [("cli_app_1", "app-secret")]


def test_test_feishu_binding_404_without_binding(monkeypatch) -> None:
    _FakeAgentChannelBindingDatastore.binding = None
    monkeypatch.setattr(channels_routes, "async_unit_of_work", lambda: _Uow())
    monkeypatch.setattr(
        channels_routes,
        "AgentChannelBindingDatastore",
        _FakeAgentChannelBindingDatastore,
    )
    app = FastAPI()
    app.include_router(channels_routes.router)
    app.dependency_overrides[channels_routes.get_current_user_id] = lambda: "u1"

    response = TestClient(app).post("/v1/channels/feishu/bindings/developer/test")

    assert response.status_code == 404
