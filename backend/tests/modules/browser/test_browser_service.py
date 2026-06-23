"""Managed-browser lifecycle service.

The service wraps the ``chrome-devtools`` CLI but must never launch a real
browser in tests — so ``_run_cli`` is patched with a fake that simulates the
daemon's ``status``/``start``/``stop`` output and state transitions. The real
CLI round-trip is exercised manually against the live engine, not here.
"""

from __future__ import annotations

import os
from pathlib import Path

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


# -- engine argv / node detection ------------------------------------------


def _clear_engine_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VALUZ_NODE_PATH", raising=False)
    monkeypatch.delenv("VALUZ_CDT_ENTRY", raising=False)


def test_engine_argv_npx(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_engine_env(monkeypatch)
    monkeypatch.setattr(service.settings, "chrome_devtools_version", "1.2.0")
    assert service._engine_argv() == [
        "npx",
        "-y",
        "-p",
        "chrome-devtools-mcp@1.2.0",
        "chrome-devtools",
    ]


def test_engine_argv_vendored_node_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VALUZ_NODE_PATH", "/opt/node/bin/node")
    monkeypatch.setenv("VALUZ_CDT_ENTRY", "/opt/cdt/chrome-devtools.js")
    # vendored → invoke node by absolute path, no npx
    assert service._engine_argv() == ["/opt/node/bin/node", "/opt/cdt/chrome-devtools.js"]
    # both vars set imply a bundled runtime → node considered available
    assert service.node_available() is True


def test_engine_argv_partial_env_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    # Only one of the two vars set → not a complete vendored engine → npx.
    monkeypatch.setenv("VALUZ_NODE_PATH", "/opt/node/bin/node")
    monkeypatch.delenv("VALUZ_CDT_ENTRY", raising=False)
    monkeypatch.setattr(service.settings, "chrome_devtools_version", "1.2.0")
    monkeypatch.setattr(service.shutil, "which", lambda name: None)
    assert service._engine_argv()[0] == "npx"
    # …and a partial env does not count as available without Node on PATH
    assert service.node_available() is False


def test_node_available_uses_which(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_engine_env(monkeypatch)

    def has_node(name: str) -> str | None:
        return "/usr/bin/node" if name == "node" else None

    monkeypatch.setattr(service.shutil, "which", has_node)
    assert service.node_available() is True
    monkeypatch.setattr(service.shutil, "which", lambda name: None)
    assert service.node_available() is False


# -- friendly chrome-devtools wrapper / cli_prefix -------------------------


def _use_tmp_bin(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point both the creating + read-only bin-dir resolvers at a temp dir."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setattr(service._fs, "browser_bin_dir", lambda: bin_dir)
    monkeypatch.setattr(service, "_bin_dir", lambda: bin_dir)
    return bin_dir


def test_ensure_cli_on_path_installs_wrapper(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _clear_engine_env(monkeypatch)  # dev → npx engine baked into the wrapper
    monkeypatch.setattr(service.settings, "chrome_devtools_version", "1.2.0")
    monkeypatch.setattr(service.shutil, "which", lambda name: "/usr/bin/node")
    bin_dir = _use_tmp_bin(monkeypatch, tmp_path)
    monkeypatch.setenv("PATH", "/usr/bin")  # bin_dir not yet on PATH

    assert service.ensure_cli_on_path() == "chrome-devtools"

    wrapper = service._wrapper_path(bin_dir)
    assert wrapper.is_file()
    assert "chrome-devtools-mcp@1.2.0" in wrapper.read_text(encoding="utf-8")
    # bin dir is now first on PATH …
    assert os.environ["PATH"].split(os.pathsep)[0] == str(bin_dir)
    # … so cli_prefix is the friendly name
    assert service.cli_prefix() == "chrome-devtools"
    # idempotent: a second call doesn't duplicate the PATH entry
    service.ensure_cli_on_path()
    assert os.environ["PATH"].split(os.pathsep).count(str(bin_dir)) == 1


def test_ensure_cli_on_path_noop_without_engine(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _clear_engine_env(monkeypatch)
    monkeypatch.setattr(service.shutil, "which", lambda name: None)  # no node
    _use_tmp_bin(monkeypatch, tmp_path)
    assert service.ensure_cli_on_path() is None


def test_cli_prefix_fallback_when_not_installed(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _clear_engine_env(monkeypatch)
    monkeypatch.setattr(service.settings, "chrome_devtools_version", "1.2.0")
    _use_tmp_bin(monkeypatch, tmp_path)
    monkeypatch.setenv("PATH", "/usr/bin")  # bin dir NOT on PATH, no wrapper
    assert service.cli_prefix() == "npx -y -p chrome-devtools-mcp@1.2.0 chrome-devtools"


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
