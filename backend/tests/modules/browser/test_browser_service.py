"""Managed-browser lifecycle service.

The service wraps the ``chrome-devtools`` CLI but must never launch a real
browser in tests — so ``_run_cli`` is patched with a fake that simulates the
daemon's ``status``/``start``/``stop`` output and state transitions. The real
CLI round-trip is exercised manually against the live engine, not here.
"""

from __future__ import annotations

import pytest

from valuz_agent.modules.browser import service
from valuz_agent.modules.browser.errors import BrowserNodeMissing, BrowserStartFailed


class FakeCli:
    """Stand-in for ``service._run_cli`` — records calls, simulates the daemon."""

    def __init__(
        self, *, initially_running: bool = False, running_after_start: bool = True
    ) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.running = initially_running
        self.running_after_start = running_after_start

    async def __call__(self, *args: str, timeout: float = 60.0) -> tuple[int, str, str]:
        self.calls.append(args)
        cmd = args[0] if args else ""
        if cmd == "start":
            if self.running_after_start:
                self.running = True
            return (0, "", "chrome-devtools start: notices on stderr")
        if cmd == "stop":
            self.running = False
            return (0, "Stopped.", "")
        if cmd == "status":
            if self.running:
                return (0, "daemon is running.\npid=4242 socket=/tmp/x.sock", "")
            return (0, "daemon is not running.", "")
        return (0, "", "")


# -- cli_prefix / node detection -------------------------------------------


def test_cli_prefix_npx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VALUZ_CDT_PATH", raising=False)
    monkeypatch.setattr(service.settings, "chrome_devtools_version", "1.2.0")
    assert service.cli_prefix() == "npx -y -p chrome-devtools-mcp@1.2.0 chrome-devtools"


def test_cli_prefix_vendored_bin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VALUZ_CDT_PATH", "/opt/cdt/chrome-devtools")
    assert service.cli_prefix() == "/opt/cdt/chrome-devtools"
    # a vendored bin implies a bundled runtime → node considered available
    assert service.node_available() is True


def test_node_available_uses_which(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VALUZ_CDT_PATH", raising=False)

    def has_node(name: str) -> str | None:
        return "/usr/bin/node" if name == "node" else None

    monkeypatch.setattr(service.shutil, "which", has_node)
    assert service.node_available() is True
    monkeypatch.setattr(service.shutil, "which", lambda name: None)
    assert service.node_available() is False


# -- status ----------------------------------------------------------------


async def test_status_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "node_available", lambda: True)
    monkeypatch.setattr(service, "_run_cli", FakeCli(initially_running=True))
    st = await service.status()
    assert st.daemon_running is True
    assert st.pid == 4242


async def test_status_not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "node_available", lambda: True)
    monkeypatch.setattr(service, "_run_cli", FakeCli(initially_running=False))
    st = await service.status()
    assert st.daemon_running is False
    assert st.pid is None


async def test_status_without_node_skips_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "node_available", lambda: False)
    fake = FakeCli()
    monkeypatch.setattr(service, "_run_cli", fake)
    st = await service.status()
    assert st.daemon_running is False
    assert st.node_ok is False
    assert any("Node" in h for h in st.hints)  # install hint surfaced to the panel
    assert fake.calls == []  # never shelled out


# -- start / stop ----------------------------------------------------------


async def test_start_requires_node(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "node_available", lambda: False)
    with pytest.raises(BrowserNodeMissing):
        await service.start()


async def test_start_idempotent_when_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "node_available", lambda: True)
    fake = FakeCli(initially_running=True)
    monkeypatch.setattr(service, "_run_cli", fake)
    res = await service.start()
    assert res.status == "already_running"
    assert all(c[0] != "start" for c in fake.calls)  # did not relaunch


async def test_start_managed_builds_args(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(service, "node_available", lambda: True)
    monkeypatch.setattr(service.settings, "browser_mode", "managed")
    monkeypatch.setattr(service._fs, "browser_profile_dir", lambda: tmp_path / "browser-chrome")
    fake = FakeCli(initially_running=False, running_after_start=True)
    monkeypatch.setattr(service, "_run_cli", fake)

    res = await service.start()
    assert res.status == "started"
    start_call = next(c for c in fake.calls if c[0] == "start")
    assert "--headless=false" in start_call
    assert any(a.startswith("--userDataDir=") and a.endswith("browser-chrome") for a in start_call)


async def test_start_attach_builds_args(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "node_available", lambda: True)
    monkeypatch.setattr(service.settings, "browser_mode", "attach")
    monkeypatch.setattr(service.settings, "browser_attach_url", "http://127.0.0.1:9222")
    fake = FakeCli(initially_running=False, running_after_start=True)
    monkeypatch.setattr(service, "_run_cli", fake)

    res = await service.start()
    assert res.status == "started"
    start_call = next(c for c in fake.calls if c[0] == "start")
    assert "--browserUrl=http://127.0.0.1:9222" in start_call
    assert not any(a.startswith("--userDataDir=") for a in start_call)


async def test_start_failure_raises(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(service, "node_available", lambda: True)
    monkeypatch.setattr(service.settings, "browser_mode", "managed")
    monkeypatch.setattr(service._fs, "browser_profile_dir", lambda: tmp_path / "browser-chrome")
    fake = FakeCli(initially_running=False, running_after_start=False)  # never comes up
    monkeypatch.setattr(service, "_run_cli", fake)
    with pytest.raises(BrowserStartFailed):
        await service.start()


async def test_stop_calls_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "node_available", lambda: True)
    fake = FakeCli(initially_running=True)
    monkeypatch.setattr(service, "_run_cli", fake)
    await service.stop()
    assert any(c[0] == "stop" for c in fake.calls)
