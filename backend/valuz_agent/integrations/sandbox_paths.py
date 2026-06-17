"""Prefix-preserving path projection — the host↔sandbox translation rule.

A cloud kernel mounts the user's COS prefix at ``settings.ags_mount_path``
(``/workspace``). The mount mirrors the host's absolute-path tree verbatim, so
translating *any* host path into the sandbox is a single uniform rule: **keep
the absolute path, prepend the mount prefix**. The same rule, with the user_id
as the COS root, gives the object key the mount reads from.

One rule, three consumers — cwd staging (``bind_workspace``), skill translation
(the kernel seam), and the sync layout (``cos_sync``) — so there is no per-root
remapping table, no ``builtin:`` special case, and no kernel-side change: every
host path (cwd, project paths, user/official/builtin skills) is treated the
same way. Per-user isolation holds because the COS mount roots at ``{user_id}/``
(the user_id is the mount root and never appears in the in-sandbox path).
"""

from __future__ import annotations


def mount_path_for(host_real: str, mount_prefix: str) -> str:
    """Host absolute realpath → in-sandbox mount path.

    ``/Users/u/p`` under mount ``/workspace`` → ``/workspace/Users/u/p``.
    Idempotent: a path already under the mount prefix is returned unchanged, so
    re-projecting (e.g. a skills-refresh on an already-cloud session) is safe.
    """
    mp = mount_prefix.rstrip("/")
    if host_real == mp or host_real.startswith(mp + "/"):
        return host_real
    # ``host_real`` is absolute (starts with "/"), so ``mp + host_real`` yields
    # exactly one separator at the join.
    return mp + host_real


def cos_key_for(host_real: str, user_id: str) -> str:
    """Host absolute realpath → COS object key under the user's prefix.

    ``/Users/u/p`` for user ``42`` → ``42/Users/u/p``. The AGS tool mounts
    ``{user_id}/`` at the mount prefix, so this key surfaces inside the sandbox
    at exactly ``mount_path_for(host_real, mount_prefix)``.
    """
    return f"{user_id}{host_real}"  # host_real starts with "/"
