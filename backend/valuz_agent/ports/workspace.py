"""Port: project-workspace path/IO access (the ⑤ materials face).

``FsRegistry`` splits into two halves with non-overlapping responsibility
(see ``docs/design/kernel-sandbox-deployment.md`` §B.3):

- **host domain** — ``doc_asset_dir`` / ``attachment_dir`` / ``secrets_dir``
  / ``logs_dir`` / parser models. Stays on the host, never knows S3, has
  nothing to do with the sandbox. Remains directly on ``FsRegistry``.
- **project domain** — ``project_cwd`` / ``task_path`` / ``subrun_dir`` /
  ``memory_dir`` / skill staging. These resolve paths *inside a project's
  workspace*, the region both host and kernel touch. This is what
  ``WorkspaceHandle`` abstracts.

Minimal-form stance: in the local deployment (incl. a Seatbelt-sandboxed
kernel) host and kernel share one filesystem, so ``LocalWorkspaceHandle``
— returning real ``Path`` objects — is the terminal implementation. There
is no need for a remote file API. ``RemoteWorkspaceHandle`` (project cwd
living on a sandbox volume, host as a read-only viewer) is an extension
path that lands only when the SaaS form ships.

Why a handle and not just paths: the synchronous ``cwd()`` / ``subpath()``
methods cover today's call sites (everything that already does
``open(fs_registry.project_cwd(...))``); the async ``read_bytes`` /
``write_bytes`` / ``exists`` methods exist so a future
``RemoteWorkspaceHandle`` can satisfy the same protocol over the kernel
file API without those call sites changing shape.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class WorkspaceHandle(Protocol):
    """Path + IO into one project's workspace.

    ``cwd``/``subpath`` return path-likes for synchronous local use; the
    async IO methods are the seam a remote implementation fills.
    """

    def cwd(self) -> Path:
        """Absolute path of the project root."""
        ...

    def subpath(self, *parts: str) -> Path:
        """Resolve a path inside the workspace from ``cwd``."""
        ...

    async def read_bytes(self, rel: str) -> bytes:
        """Read a workspace-relative file."""
        ...

    async def write_bytes(self, rel: str, data: bytes) -> None:
        """Write a workspace-relative file (parents created)."""
        ...

    async def exists(self, rel: str) -> bool:
        """True iff a workspace-relative path exists."""
        ...


class LocalWorkspaceHandle:
    """Local implementation — host and kernel share the filesystem.

    Terminal for the local form; a thin wrapper over ``Path`` so call
    sites that need raw paths (most of them) keep using ``cwd()`` /
    ``subpath()`` while still satisfying the async protocol.
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def cwd(self) -> Path:
        return self._root

    def subpath(self, *parts: str) -> Path:
        return self._root.joinpath(*parts)

    async def read_bytes(self, rel: str) -> bytes:
        return self.subpath(rel).read_bytes()

    async def write_bytes(self, rel: str, data: bytes) -> None:
        target = self.subpath(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    async def exists(self, rel: str) -> bool:
        return self.subpath(rel).exists()


class RemoteWorkspaceHandle:
    """Cloud implementation — project files live in an object store the sandbox
    mounts as its workspace (host and kernel do NOT share a filesystem).

    The sync ``cwd()`` / ``subpath()`` return the **in-sandbox** path (e.g.
    ``/workspace/<project_id>``) — what the kernel is told to use as the session
    cwd — NOT a host path. The async IO methods read/write the backing object
    store under the project's key prefix, so the host can stage files in and
    read results back over the same ``WorkspaceHandle`` protocol the local form
    uses. Object key for a workspace-relative ``rel`` is ``{prefix}/{rel}``.
    """

    def __init__(self, store: object, *, key_prefix: str, sandbox_cwd: str) -> None:
        # ``store`` is an ``ObjectStore`` (ports.object_store) — typed loosely
        # here to keep this port free of the integration import.
        self._store = store
        self._prefix = key_prefix.rstrip("/")
        self._cwd = sandbox_cwd

    def cwd(self) -> Path:
        return Path(self._cwd)

    def subpath(self, *parts: str) -> Path:
        return Path(self._cwd).joinpath(*parts)

    def _key(self, rel: str) -> str:
        return f"{self._prefix}/{rel.lstrip('/')}"

    async def read_bytes(self, rel: str) -> bytes:
        return await self._store.get_bytes(self._key(rel))  # type: ignore[attr-defined,no-any-return]

    async def write_bytes(self, rel: str, data: bytes) -> None:
        await self._store.put_bytes(self._key(rel), data)  # type: ignore[attr-defined]

    async def exists(self, rel: str) -> bool:
        return await self._store.exists(self._key(rel))  # type: ignore[attr-defined,no-any-return]
