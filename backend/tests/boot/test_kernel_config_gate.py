"""Config gate (``KERNEL_CONFIG_WAIT``) — parse, wait/apply, and auth-at-runtime.

The gate lets a snapshot-frozen kernel receive its per-instance env AFTER boot
(see ``app/config_gate.py``). These tests pin the three behaviors the
commercial overlay depends on:

1. the env-file dialect (plain / ``export``-prefixed / shell-quoted lines);
2. ``wait_for_config`` blocking until the file appears, then applying it to
   ``os.environ`` (and timing out only when asked to);
3. the standalone-auth middleware enforcing the token at REQUEST time — a
   token that arrives via the gate (after import) must still be enforced.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede app.*
from __future__ import annotations

import asyncio
import dataclasses
import os

import pytest

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect for app.*

from app.config_gate import gate_enabled, parse_env_lines, wait_for_config


# ── parse_env_lines ──────────────────────────────────────────────────────────


def test_parse_plain_and_export_and_quoted() -> None:
    text = "\n".join(
        [
            "KERNEL_STORE=remote",
            "export KERNEL_AUTH_TOKEN=tok123",
            "export VALUZ_DATA_API_URL='https://d.example/api?x=1'",
            'GREETING="hello world"',
        ]
    )
    env = parse_env_lines(text)
    assert env == {
        "KERNEL_STORE": "remote",
        "KERNEL_AUTH_TOKEN": "tok123",
        "VALUZ_DATA_API_URL": "https://d.example/api?x=1",
        "GREETING": "hello world",
    }


def test_parse_skips_blanks_comments_and_malformed() -> None:
    text = "\n".join(
        [
            "",
            "# comment",
            "NOEQUALS",
            "BAD KEY=1",
            "1LEADING=x",
            "OK=1",
        ]
    )
    assert parse_env_lines(text) == {"OK": "1"}


def test_parse_empty_value() -> None:
    assert parse_env_lines("EMPTY=") == {"EMPTY": ""}


# ── gate_enabled ─────────────────────────────────────────────────────────────


def test_gate_enabled_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KERNEL_CONFIG_WAIT", raising=False)
    assert gate_enabled() is False
    monkeypatch.setenv("KERNEL_CONFIG_WAIT", "1")
    assert gate_enabled() is True
    monkeypatch.setenv("KERNEL_CONFIG_WAIT", "0")
    assert gate_enabled() is False


# ── wait_for_config ──────────────────────────────────────────────────────────


async def test_wait_blocks_until_file_appears_then_applies(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "env"

    async def _writer() -> None:
        await asyncio.sleep(0.25)
        cfg.write_text("export TESTGATE_A=1\nTESTGATE_B='b v'\n")

    writer = asyncio.create_task(_writer())
    try:
        env = await wait_for_config(str(cfg), poll_interval_s=0.05)
    finally:
        await writer
    assert env == {"TESTGATE_A": "1", "TESTGATE_B": "b v"}
    try:
        assert os.environ["TESTGATE_A"] == "1"
        assert os.environ["TESTGATE_B"] == "b v"
    finally:
        os.environ.pop("TESTGATE_A", None)
        os.environ.pop("TESTGATE_B", None)


async def test_wait_times_out_when_asked(tmp_path) -> None:
    with pytest.raises(TimeoutError):
        await wait_for_config(
            str(tmp_path / "never"), poll_interval_s=0.02, timeout_s=0.1
        )


# ── auth middleware enforces the token at request time ──────────────────────


async def test_auth_enforced_for_token_arriving_after_import() -> None:
    """Simulate the gate: rebind app.main.config with a token AFTER import and
    assert the middleware enforces it (the old import-time conditional
    registration would silently skip auth in exactly this scenario)."""
    import httpx

    from app import main as kernel_main

    original = kernel_main.config
    kernel_main.config = dataclasses.replace(original, auth_token="gate-tok")
    try:
        transport = httpx.ASGITransport(app=kernel_main.app)  # no lifespan
        async with httpx.AsyncClient(
            transport=transport, base_url="http://kernel"
        ) as client:
            # /health stays open
            assert (await client.get("/health")).status_code == 200
            # any other route: 401 without / with wrong token
            assert (await client.get("/sessions")).status_code == 401
            assert (
                await client.get(
                    "/sessions", headers={"Authorization": "Bearer wrong"}
                )
            ).status_code == 401
            # correct token passes the middleware (route itself may still 4xx/5xx
            # without lifespan-initialized deps — only 401 is the middleware's)
            resp = await client.get(
                "/sessions", headers={"Authorization": "Bearer gate-tok"}
            )
            assert resp.status_code != 401
    finally:
        kernel_main.config = original


async def test_auth_passthrough_without_token() -> None:
    import httpx

    from app import main as kernel_main

    original = kernel_main.config
    kernel_main.config = dataclasses.replace(original, auth_token=None)
    try:
        transport = httpx.ASGITransport(app=kernel_main.app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://kernel"
        ) as client:
            assert (await client.get("/health")).status_code == 200
            # no token configured -> middleware passes through (route status is
            # whatever the route does; it must NOT be the middleware's 401)
            assert (await client.get("/sessions")).status_code != 401
    finally:
        kernel_main.config = original
