"""The prefix-preserving projection rule + that the sync layout, cwd staging,
and skill translation all agree on it."""

from __future__ import annotations

import os

from valuz_agent.integrations.sandbox_paths import cos_key_for, mount_path_for


def test_mount_path_prepends_prefix():
    assert mount_path_for("/Users/u/p", "/workspace") == "/workspace/Users/u/p"
    # trailing slash on the mount prefix is normalized
    assert mount_path_for("/Users/u/p", "/workspace/") == "/workspace/Users/u/p"


def test_mount_path_is_idempotent():
    # A path already under the mount must not be double-prefixed (re-projecting
    # a skills-refresh on an already-cloud session is a no-op).
    once = mount_path_for("/Users/u/p", "/workspace")
    assert mount_path_for(once, "/workspace") == once
    assert mount_path_for("/workspace", "/workspace") == "/workspace"


def test_cos_key_roots_at_user_prefix():
    assert cos_key_for("/Users/u/p", "user-42") == "user-42/Users/u/p"


def test_cos_key_and_mount_path_line_up():
    # The contract the AGS COS mount enforces: the tool mounts {user_id}/ at the
    # mount prefix, so cos_key with {user_id} stripped + mount prefix == the
    # mount path. This is what makes one sync layout serve cwd + skills.
    real = "/Users/u/proj"
    uid = "user-42"
    key = cos_key_for(real, uid)  # user-42/Users/u/proj
    in_mount = key[len(uid):]  # /Users/u/proj  (what the mount exposes)
    assert "/workspace" + in_mount == mount_path_for(real, "/workspace")


def test_skill_sync_sources_are_skill_dirs(tmp_path, monkeypatch):
    # skill_sync_sources() returns only the skill roots (what a cloud kernel
    # pre-syncs); projects/chats are staged per-session instead.
    from valuz_agent.integrations import cos_sync

    fake = [
        cos_sync.SyncSource("projects", tmp_path / "proj", is_skill=False),
        cos_sync.SyncSource("skills/claude", tmp_path / "sk", is_skill=True),
    ]
    monkeypatch.setattr(cos_sync, "local_sync_sources", lambda: fake)
    out = cos_sync.skill_sync_sources()
    assert [s.name for s in out] == ["skills/claude"]


async def test_sync_uses_abs_path_keys(tmp_path, monkeypatch):
    # sync_local_to_cos keys by {user_id}{realpath}, matching the mount layout.
    from valuz_agent.integrations import cos_sync

    (tmp_path / "a.txt").write_bytes(b"x")

    class _MemStore:
        def __init__(self):
            self.objs: dict[str, bytes] = {}

        async def put_bytes(self, key, data):
            self.objs[key] = data

    store = _MemStore()
    src = cos_sync.SyncSource("skills/claude", tmp_path, is_skill=True)
    report = await cos_sync.sync_local_to_cos("user-42", store=store, sources=[src])
    real = os.path.realpath(str(tmp_path))
    assert f"user-42{real}/a.txt" in store.objs
    assert report.total_files == 1
