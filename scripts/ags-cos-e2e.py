#!/usr/bin/env python
"""AGS + COS end-to-end smoke — proves the cloud kernel chain works.

Closed loop validated:
  1. stage a tiny project → COS under  <user_id>/projects/e2e-demo/
  2. provision an AGS sandbox from the kernel template (real driver path)
  3. wait for the kernel /health
  4. connect into the sandbox and ``ls`` the COS mount → the staged files
     must appear at  {mount_path}/<user_id>/projects/e2e-demo/  (proves the
     COS-mount-as-workspace sync)
  5. tear down + clean the COS prefix

Run (from backend/, with the ags extra so e2b + boto3 are present):

    cd backend
    set -a; . ./.env; set +a            # load VALUZ_COS_* / VALUZ_AGS_*
    uv run --extra ags python ../scripts/ags-cos-e2e.py

Requires in the environment (see backend/.env):
    VALUZ_COS_*            (validated)
    VALUZ_AGS_API_KEY      (the e2b_ key)
    VALUZ_AGS_DOMAIN       (from the AGS 快速开始 page, e.g. ap-guangzhou.tencentags.com)
    VALUZ_AGS_KERNEL_TEMPLATE  (the sandbox tool name you created)
    VALUZ_AGS_MOUNT_PATH=/workspace   (must equal the tool's COS 挂载路径)

The sandbox tool MUST be configured (console) with: image=kernel (TCR), network
=公网, lifecycle=常驻, a COS storage mount (bucket=VALUZ_COS_BUCKET, 挂载路径=
VALUZ_AGS_MOUNT_PATH), and start command  /usr/bin/tini -- /app/kernel-entrypoint.sh .
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

# Make the backend package importable when run from repo root or backend/.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "backend"))

PROJECT_REL = "projects/e2e-demo"  # path within the user's mounted prefix


def _need(name: str) -> str:
    val = os.environ.get(name) or ""
    if not val:
        sys.exit(f"✗ missing required env {name} — see this script's header.")
    return val


async def main() -> None:
    from valuz_agent.infra.config import settings
    from valuz_agent.infra.local_identity import resolve_local_user_id
    from valuz_agent.integrations.object_store_s3 import cos_object_store, stage_directory
    from valuz_agent.integrations.sandbox_ags import (
        _AgsBackend,
        _sandbox_id_from_url,
        ags_preflight,
    )
    from valuz_agent.ports.sandbox_provider import SandboxSpec

    _need("VALUZ_AGS_DOMAIN")
    _need("VALUZ_AGS_KERNEL_TEMPLATE")
    problems = ags_preflight()
    if problems:
        sys.exit("✗ AGS preflight failed: " + "; ".join(problems))

    store = cos_object_store()
    if store is None:
        sys.exit("✗ COS not configured (VALUZ_COS_*).")

    # The tool mounts THIS user's COS prefix (存储路径=/<user_id>) at the mount
    # path, so the user_id is the mount root and is NOT in the in-sandbox path.
    user_id = resolve_local_user_id()
    mount = settings.ags_mount_path.rstrip("/")
    cos_prefix = f"{user_id}/{PROJECT_REL}"  # COS key (with user_id)
    sandbox_path = f"{mount}/{PROJECT_REL}"  # in-sandbox path (user_id stripped)
    print(f"   user_id={user_id}  (tool 存储路径 must be /{user_id})")

    # 1. stage a tiny project into COS
    tmp = tempfile.mkdtemp(prefix="ags-e2e-")
    os.makedirs(os.path.join(tmp, "src"))
    with open(os.path.join(tmp, "src", "main.py"), "w") as fh:
        fh.write("print('hello from the cloud kernel workspace')\n")
    with open(os.path.join(tmp, "MARKER.txt"), "w") as fh:
        fh.write("valuz-e2e-marker\n")
    n, total = await stage_directory(
        store, cos_prefix, tmp, max_files=1000, max_bytes=10_000_000
    )
    print(f"① staged {n} files ({total} B) → COS {cos_prefix}/")

    # 2. provision via the real driver path
    from valuz_agent.integrations.sandbox_ags import AgsSandboxProvider

    provider = AgsSandboxProvider()
    spec = SandboxSpec(
        sandbox_id="e2e",
        kernel_db_path="/app/data/kernel.db",
        env={},
        host_callback_url=os.environ.get("VALUZ_HOST_EXTERNAL_URL", ""),
    )
    print("② provisioning AGS sandbox from template "
          f"{settings.ags_kernel_template!r} …")
    endpoint = await provider.provision(spec)
    print(f"   kernel up: {endpoint.base_url}  (③ /health passed)")

    e2b_id = _sandbox_id_from_url(endpoint.base_url)
    try:
        # 4. connect into the SAME sandbox and ls the COS mount
        backend = await _AgsBackend.connect(e2b_id or "")
        sbx = backend._handle  # raw e2b AsyncSandbox
        res = await sbx.commands.run(f"ls -R {sandbox_path} 2>&1 || true")
        listing = getattr(res, "stdout", str(res))
        print(f"④ ls {sandbox_path}:\n{listing}")
        ok = "main.py" in listing and "MARKER.txt" in listing
        cat = await sbx.commands.run(f"cat {sandbox_path}/MARKER.txt 2>&1 || true")
        print(f"   cat MARKER.txt: {getattr(cat, 'stdout', cat)!r}")
        print("✅ COS mount E2E: staged files VISIBLE in the sandbox"
              if ok else "✗ staged files NOT visible — check the tool's COS mount path")
    finally:
        # 5. teardown + cleanup
        await provider.destroy("e2e")
        # Clean ONLY the demo prefix — never the user's whole synced workspace.
        removed = await store.delete_prefix(f"{cos_prefix}/")
        print(f"⑤ destroyed sandbox + cleaned {removed} COS objects ({cos_prefix}/)")


if __name__ == "__main__":
    asyncio.run(main())
