"""AGS / e2b cloud sandbox driver — the SDK-agnostic logic.

The vendor SDK is isolated in ``_AgsBackend``; these tests mock it out and
exercise preflight gating, provision orchestration (env injection + health
wait), the boot driver wiring, cloud bind_workspace COS staging, and the
e2b-id-from-URL parsing. Live AGS provisioning is not covered (needs creds).
"""

from __future__ import annotations

import sys
import types

import pytest

from valuz_agent.infra.config import settings
from valuz_agent.integrations import sandbox_ags as ags
from valuz_agent.ports.sandbox_provider import (
    SandboxBootContext,
    SandboxProvisionError,
    SandboxSpec,
)


@pytest.fixture
def ags_configured(monkeypatch):
    """All settings present + a fake ``e2b`` module importable."""
    monkeypatch.setattr(settings, "ags_api_key", "e2b_testkey")
    monkeypatch.setattr(settings, "ags_domain", "ags.example.com")
    monkeypatch.setattr(settings, "ags_kernel_template", "valuz-kernel")
    monkeypatch.setattr(settings, "ags_kernel_port", 8000)
    # Default to DYNAMIC token mode (no static token) regardless of the ambient
    # env — a stray VALUZ_AGS_KERNEL_TOKEN must not flip these tests.
    monkeypatch.setattr(settings, "ags_kernel_token", None)
    monkeypatch.setitem(sys.modules, "e2b", types.ModuleType("e2b"))
    monkeypatch.delenv("E2B_API_KEY", raising=False)


# ── preflight ──────────────────────────────────────────────────────────


def test_preflight_reports_everything_missing(monkeypatch):
    monkeypatch.setattr(settings, "ags_api_key", None)
    monkeypatch.setattr(settings, "ags_kernel_template", None)
    monkeypatch.delenv("E2B_API_KEY", raising=False)
    monkeypatch.setitem(sys.modules, "e2b", None)  # force ImportError
    problems = ags.ags_preflight()
    assert any("e2b SDK" in p for p in problems)
    assert any("API key" in p for p in problems)
    assert any("template" in p for p in problems)


def test_preflight_clean_when_configured(ags_configured):
    assert ags.ags_preflight() == []


def test_preflight_accepts_env_api_key(monkeypatch):
    monkeypatch.setattr(settings, "ags_api_key", None)
    monkeypatch.setattr(settings, "ags_kernel_template", "valuz-kernel")
    monkeypatch.setenv("E2B_API_KEY", "e2b_fromenv")
    monkeypatch.setitem(sys.modules, "e2b", types.ModuleType("e2b"))
    assert ags.ags_preflight() == []


# ── URL parsing ────────────────────────────────────────────────────────


def test_sandbox_id_from_url():
    assert (
        ags._sandbox_id_from_url("https://8000-abc123def.ags.example.com")
        == "abc123def"
    )
    assert ags._sandbox_id_from_url("https://nodash.example.com") is None
    assert ags._sandbox_id_from_url("not a url") is None


# ── provision (mocked backend + health) ────────────────────────────────


class _FakeBackend:
    last_envs: dict[str, str] | None = None
    killed = False

    def __init__(self, sandbox_id="sb-xyz", base="https://8000-sb-xyz.ags.example.com"):
        self.sandbox_id = sandbox_id
        self._base = base

    def base_url(self) -> str:
        return self._base

    async def kill(self) -> None:
        _FakeBackend.killed = True


@pytest.fixture
def mock_backend(monkeypatch):
    _FakeBackend.last_envs = None
    _FakeBackend.killed = False

    async def fake_create(*, envs=None):
        _FakeBackend.last_envs = dict(envs) if envs else None
        return _FakeBackend()

    monkeypatch.setattr(ags._AgsBackend, "create", staticmethod(fake_create))

    async def ok_health(base_url, token, *, deadline_s=120.0):
        return None

    monkeypatch.setattr(ags.AgsSandboxProvider, "_await_health", staticmethod(ok_health))


async def test_provision_injects_env_and_returns_endpoint(ags_configured, mock_backend):
    provider = ags.AgsSandboxProvider()
    spec = SandboxSpec(
        sandbox_id="host-kernel",
        kernel_db_path="/app/data/kernel.db",
        env={"ANTHROPIC_API_KEY": "sk-x"},
        host_callback_url="https://host.example:8000",
    )
    ep = await provider.provision(spec)
    assert ep.base_url == "https://8000-sb-xyz.ags.example.com"
    assert ep.token  # a token was generated
    # env carries the kernel auth token, the credential, and the ④ callback.
    envs = _FakeBackend.last_envs
    assert envs["KERNEL_AUTH_TOKEN"] == ep.token
    assert envs["ANTHROPIC_API_KEY"] == "sk-x"
    assert envs["CODEX_TOOLKIT_BASE_URL"] == "https://host.example:8000"
    # cloud: control-plane env is NOT set (macOS-only)
    assert "KERNEL_SANDBOX_CONTROL" not in envs


async def test_provision_static_token_skips_env_injection(
    ags_configured, mock_backend, monkeypatch
):
    # AGS rejects create(envs=); a configured static token → use it + pass NO
    # envs (rely on the tool's KERNEL_AUTH_TOKEN env).
    monkeypatch.setattr(settings, "ags_kernel_token", "static-tok")
    ep = await ags.AgsSandboxProvider().provision(
        SandboxSpec(
            sandbox_id="host-kernel",
            kernel_db_path="/app/data/kernel.db",
            env={"ANTHROPIC_API_KEY": "sk-x"},
            host_callback_url="https://host:8000",
        )
    )
    assert ep.token == "static-tok"
    assert _FakeBackend.last_envs is None  # no create-time env sync attempted


async def test_provision_preflight_failure_raises(monkeypatch):
    monkeypatch.setattr(settings, "ags_api_key", None)
    monkeypatch.setattr(settings, "ags_kernel_template", None)
    monkeypatch.delenv("E2B_API_KEY", raising=False)
    monkeypatch.setitem(sys.modules, "e2b", None)
    with pytest.raises(SandboxProvisionError):
        await ags.AgsSandboxProvider().provision(
            SandboxSpec(sandbox_id="x", kernel_db_path="/app/data/kernel.db")
        )


async def test_provision_kills_on_unhealthy(ags_configured, monkeypatch):
    _FakeBackend.killed = False

    async def fake_create(*, envs):
        return _FakeBackend()

    async def bad_health(base_url, token, *, deadline_s=120.0):
        raise TimeoutError("never healthy")

    monkeypatch.setattr(ags._AgsBackend, "create", staticmethod(fake_create))
    monkeypatch.setattr(ags.AgsSandboxProvider, "_await_health", staticmethod(bad_health))
    with pytest.raises(SandboxProvisionError):
        await ags.AgsSandboxProvider().provision(
            SandboxSpec(sandbox_id="host-kernel", kernel_db_path="/app/data/kernel.db")
        )
    assert _FakeBackend.killed is True  # cleaned up the dead sandbox


# ── bind_workspace COS staging ─────────────────────────────────────────


async def test_bind_workspace_no_cos_falls_back(monkeypatch):
    # COS unconfigured → no staging, but still returns an in-sandbox path (not
    # the nonexistent-in-cloud host path) so session creation doesn't break.
    monkeypatch.setattr("valuz_agent.integrations.object_store_s3.cos_object_store", lambda: None)
    provider = ags.AgsSandboxProvider()
    grant = await provider.bind_workspace("host-kernel", "/Users/me/proj", "rw")
    assert grant.kernel_cwd.startswith("/workspace/")
    assert grant.kernel_cwd != grant.host_path
    assert grant.grant_id.startswith("ags-nostore:")


async def test_bind_workspace_stages_to_cos(monkeypatch, tmp_path):
    # Configured COS → project uploaded to prefix-preserving COS key
    # <user_id><realpath>, and kernel_cwd is {mount}<realpath> (the AGS tool
    # mounts <user_id>/ at the mount, so the user_id is the mount root and
    # never appears in the in-sandbox path). One uniform rule (sandbox_paths).
    import os

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_bytes(b"print(1)")
    (tmp_path / "README.md").write_bytes(b"# hi")

    class _MemStore:
        def __init__(self):
            self.objs: dict[str, bytes] = {}

        async def put_bytes(self, key, data):
            self.objs[key] = data

    store = _MemStore()
    monkeypatch.setattr(
        "valuz_agent.integrations.object_store_s3.cos_object_store", lambda: store
    )
    from valuz_agent.infra.auth_context import reset_current_user_id, set_current_user_id
    from valuz_agent.infra.config import settings

    monkeypatch.setattr(settings, "ags_mount_path", "/workspace")
    token = set_current_user_id("user-42")
    try:
        provider = ags.AgsSandboxProvider()
        grant = await provider.bind_workspace("host-kernel", str(tmp_path), "rw")
    finally:
        reset_current_user_id(token)

    real = os.path.realpath(str(tmp_path))
    # uploaded to COS under <user_id><realpath>/...
    cos_key = grant.grant_id.split(":", 1)[1]
    assert cos_key == f"user-42{real}"
    assert f"{cos_key}/src/main.py" in store.objs
    assert store.objs[f"{cos_key}/src/main.py"] == b"print(1)"
    assert f"{cos_key}/README.md" in store.objs
    # kernel cwd = mount prefix + host realpath; user_id is the mount root only
    assert grant.kernel_cwd == f"/workspace{real}"
    assert "user-42" not in grant.kernel_cwd
    assert grant.grant_id.startswith("cos:")
    # provider.project_path (pure, no staging) yields the SAME path for that cwd
    monkeypatch.setattr(settings, "ags_mount_path", "/workspace")
    assert provider.project_path(real) == f"/workspace{real}"


# ── boot driver ────────────────────────────────────────────────────────


async def test_driver_provision_for_boot(ags_configured, mock_backend):
    driver = ags.AgsSandboxDriver()
    assert driver.name == "ags"
    ctx = SandboxBootContext(
        host="127.0.0.1",
        port=8000,
        host_callback_url="https://host.example:8000",
        passthrough_env={"OPENAI_API_KEY": "sk-o"},
    )
    result = await driver.provision_for_boot(ctx)
    assert result.endpoint.base_url == "https://8000-sb-xyz.ags.example.com"
    assert result.static_roots == ()  # every cwd needs staging (P3)
    assert _FakeBackend.last_envs["OPENAI_API_KEY"] == "sk-o"


def test_driver_attach_seeds_endpoint():
    driver = ags.AgsSandboxDriver()
    from valuz_agent.ports.sandbox_provider import SandboxEndpoint

    ep = SandboxEndpoint(sandbox_id="host-kernel", base_url="https://k", token="t")
    result = driver.attach(SandboxBootContext(host="127.0.0.1", port=8000), ep)
    assert result.endpoint is ep
    assert result.static_roots == ()


def test_driver_registered_in_registry():
    from valuz_agent.integrations import sandbox_registry

    assert "ags" in sandbox_registry.available()
