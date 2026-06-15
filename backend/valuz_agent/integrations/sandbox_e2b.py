"""E2BSandboxProvider — the cloud kernel sandbox over the E2B protocol.

The ① supply face for E2B-compatible backends: **Tencent Cloud Agent Runtime**
(`<region>.tencentags.com`), the open-source **CubeSandbox**, or hosted **E2B**
— all the same SDK, distinguished only by `domain` + `api_key` (see
``docs/design/kernel-sandbox-deployment.md``). Each sandbox is a hardware-
isolated microVM booted from a **template** built from the standalone kernel
image (``docker/kernel.Dockerfile``); the host drives the kernel over HTTP via
``HttpKernelClient`` against the port E2B exposes.

This is the cloud counterpart of ``SeatbeltSandboxProvider``: same
``SandboxProvider`` contract, but the "process" is a remote microVM reached
through the E2B gateway, and ``bind_workspace`` stages through the E2B file API
instead of issuing a macOS sandbox extension.

Decisions locked for v1 (see the design doc):
- **one long-running sandbox per kernel** (provisioned at boot, killed at exit);
- **cloud is source-of-truth** for project files (the kernel works in
  ``/workspace/{id}``; the host reads back on demand via the file API), so
  ``bind_workspace`` only ensures the dir exists — no stage-in from local;
- **sandbox-local SQLite** (the template's entrypoint migrates + serves; the DB
  lives and dies with the sandbox).
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import httpx

from valuz_agent.ports.sandbox_provider import (
    MountGrant,
    SandboxEndpoint,
    SandboxProvisionError,
    SandboxSpec,
)

if TYPE_CHECKING:
    from e2b import AsyncSandbox

_log = logging.getLogger("valuz_agent.sandbox")


def e2b_preflight(domain: str | None, api_key: str | None, template: str | None) -> list[str]:
    """Reasons this host can't provision an E2B sandbox (empty = OK).

    Pure config check, called BEFORE provisioning so the failure is upfront and
    actionable instead of a cryptic mid-create error. The ``e2b`` SDK import is
    also verified (it's an optional backend dep).
    """
    problems: list[str] = []
    if not domain:
        problems.append("VALUZ_E2B_DOMAIN unset (e.g. ap-guangzhou.tencentags.com)")
    if not api_key:
        problems.append("VALUZ_E2B_API_KEY unset (console-created key, E2B form 'e2b_...')")
    if not template:
        problems.append("VALUZ_E2B_TEMPLATE unset (the sandbox template/tool name)")
    try:
        import e2b  # noqa: F401
    except ImportError:
        problems.append("the 'e2b' package is not installed")
    return problems


class E2BSandboxProvider:
    """``SandboxProvider`` backed by the E2B AsyncSandbox SDK.

    Holds the live ``AsyncSandbox`` handles in memory keyed by ``sandbox_id``
    (the v1 single-process model — like Seatbelt's ``_procs``); reconnecting a
    handle across a host restart is an ``AsyncSandbox.connect`` upgrade.
    """

    def __init__(
        self,
        *,
        domain: str,
        api_key: str,
        template: str,
        kernel_port: int = 8000,
        sandbox_timeout_s: int = 3600,
        health_deadline_s: float = 90.0,
    ) -> None:
        self._domain = domain
        self._api_key = api_key
        self._template = template
        self._kernel_port = kernel_port
        self._sandbox_timeout_s = sandbox_timeout_s
        self._health_deadline_s = health_deadline_s
        self._sandboxes: dict[str, AsyncSandbox] = {}
        self._tokens: dict[str, str] = {}

    @classmethod
    def from_settings(cls) -> E2BSandboxProvider:
        from valuz_agent.infra.config import settings

        problems = e2b_preflight(
            settings.e2b_domain, settings.e2b_api_key, settings.e2b_template
        )
        if problems:
            raise SandboxProvisionError("E2B preflight failed: " + "; ".join(problems))
        return cls(
            domain=settings.e2b_domain,
            api_key=settings.e2b_api_key,  # type: ignore[arg-type]  (preflight asserts non-None)
            template=settings.e2b_template,  # type: ignore[arg-type]
            kernel_port=settings.e2b_kernel_port,
        )

    @property
    def _api(self) -> dict[str, Any]:
        return {"api_key": self._api_key, "domain": self._domain}

    async def provision(self, spec: SandboxSpec) -> SandboxEndpoint:
        from e2b import AsyncSandbox

        # The kernel inside the template authenticates with this; the host
        # client sends it back. Generated per-sandbox, injected as env.
        token = secrets.token_urlsafe(24)
        envs: dict[str, str] = {
            "KERNEL_AUTH_TOKEN": token,
            "HOST": "0.0.0.0",
            "PORT": str(self._kernel_port),
            # ⑥ L1 credential injection (provider keys) + ④ host callback.
            **spec.env,
        }
        if spec.host_callback_url:
            envs["CODEX_TOOLKIT_BASE_URL"] = spec.host_callback_url

        try:
            sbx = await AsyncSandbox.create(
                template=self._template,
                envs=envs,
                timeout=self._sandbox_timeout_s,
                metadata={"valuz_sandbox_id": spec.sandbox_id},
                **self._api,
            )
        except Exception as exc:  # noqa: BLE001 — SDK raises a variety of errors
            raise SandboxProvisionError(f"e2b sandbox create failed: {exc}") from exc

        self._sandboxes[sbx.sandbox_id] = sbx
        self._tokens[sbx.sandbox_id] = token

        # E2B exposes an internal port as a public host; the kernel speaks HTTPS
        # through the gateway. get_host returns "host[:port]" — scheme is https.
        base_url = f"https://{sbx.get_host(self._kernel_port)}"
        try:
            await self._await_health(base_url, token)
        except Exception as exc:
            await self.destroy(sbx.sandbox_id)
            raise SandboxProvisionError(
                f"e2b sandbox started but the kernel never became healthy: {exc}"
            ) from exc
        _log.warning("kernel running in E2B sandbox %s at %s", sbx.sandbox_id, base_url)
        return SandboxEndpoint(sandbox_id=sbx.sandbox_id, base_url=base_url, token=token)

    async def health(self, sandbox_id: str) -> bool:
        sbx = self._sandboxes.get(sandbox_id)
        if sbx is None:
            return False
        try:
            return bool(await sbx.is_running())
        except Exception:  # noqa: BLE001
            return False

    async def destroy(self, sandbox_id: str) -> None:
        sbx = self._sandboxes.pop(sandbox_id, None)
        self._tokens.pop(sandbox_id, None)
        if sbx is None:
            return
        try:
            await sbx.kill()
        except Exception:  # noqa: BLE001 — best-effort teardown
            _log.warning("e2b sandbox %s kill failed (will time out server-side)", sandbox_id)

    async def bind_workspace(
        self, sandbox_id: str, host_path: str, mode: Literal["rw", "ro"] = "rw"
    ) -> MountGrant:
        """Ensure the project's workspace dir exists inside the sandbox and
        return its in-sandbox path as ``kernel_cwd``.

        Cloud-source-of-truth (v1): we do NOT copy local files in — the kernel
        creates/owns the project under ``/workspace/{id}`` and the host reads
        back through the file API (``RemoteWorkspaceHandle``). The id is derived
        deterministically from the host path so the same project always maps to
        the same in-sandbox dir.
        """
        sbx = self._sandboxes.get(sandbox_id)
        if sbx is None:
            raise SandboxProvisionError(f"unknown sandbox {sandbox_id!r} — provision first")
        real = os.path.realpath(str(Path(host_path).expanduser()))
        digest = hashlib.sha1(real.encode()).hexdigest()[:8]  # noqa: S324 (id, not security)
        kernel_cwd = f"/workspace/{Path(real).name}-{digest}"
        try:
            await sbx.files.make_dir(kernel_cwd)
        except Exception as exc:  # noqa: BLE001
            raise SandboxProvisionError(
                f"e2b workspace make_dir {kernel_cwd!r} failed: {exc}"
            ) from exc
        return MountGrant(grant_id=kernel_cwd, kernel_cwd=kernel_cwd, host_path=real, mode=mode)

    async def unbind_workspace(self, sandbox_id: str, grant_id: str) -> None:
        # Cloud source-of-truth: the workspace lives with the sandbox and is
        # reclaimed on destroy. Nothing to revoke per-project.
        return None

    # ---- internals -----------------------------------------------------

    async def _await_health(self, base_url: str, token: str) -> None:
        deadline = time.monotonic() + self._health_deadline_s
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(timeout=5.0) as c:
            last_err: Exception | None = None
            while time.monotonic() < deadline:
                try:
                    r = await c.get(f"{base_url}/health", headers=headers)
                    if r.status_code == 200:
                        return
                except httpx.HTTPError as exc:
                    last_err = exc
                import asyncio

                await asyncio.sleep(1.0)
            raise SandboxProvisionError(f"/health never 200 within deadline: {last_err}")
