"""FileCheckpointSaver — write-once file checkpointer used in-sandbox (COS mount).

No official ``langgraph.checkpoint.conformance`` in this pin, so these cover the
contract we rely on plus the COS-hardening invariants (safe_latest skips
torn/phantom files, ``*.tmp`` ignored, ns isolation, delete_thread).
"""

from __future__ import annotations

import os

from langgraph.checkpoint.base import empty_checkpoint
from src.adapters.file_checkpoint_saver import FileCheckpointSaver


def _cfg(thread_id: str, ns: str = "", cid: str | None = None) -> dict:
    c = {"thread_id": thread_id, "checkpoint_ns": ns}
    if cid is not None:
        c["checkpoint_id"] = cid
    return {"configurable": c}


def _ckpt(cid: str) -> dict:
    c = empty_checkpoint()
    c["id"] = cid
    c["channel_values"] = {"messages": [{"role": "user", "content": "hi " + cid}]}
    return c


def _meta(step: int) -> dict:
    return {"source": "loop", "step": step, "writes": {}, "parents": {}}


async def test_put_get_roundtrip_and_latest(tmp_path):
    s = FileCheckpointSaver(str(tmp_path))
    cfg = _cfg("t1")
    out1 = await s.aput(cfg, _ckpt("ck-0001"), _meta(1), {})
    # parent chain: second put carries the first as parent via config checkpoint_id
    out2 = await s.aput(out1, _ckpt("ck-0002"), _meta(2), {})

    got = await s.aget_tuple(_cfg("t1", cid="ck-0001"))
    assert got is not None
    assert got.checkpoint["id"] == "ck-0001"
    assert got.checkpoint["channel_values"]["messages"][0]["content"] == "hi ck-0001"

    latest = await s.aget_tuple(_cfg("t1"))  # no checkpoint_id -> newest
    assert latest is not None
    assert latest.checkpoint["id"] == "ck-0002"
    assert latest.parent_config["configurable"]["checkpoint_id"] == "ck-0001"
    assert out2["configurable"]["checkpoint_id"] == "ck-0002"


async def test_pending_writes(tmp_path):
    s = FileCheckpointSaver(str(tmp_path))
    cfg = await s.aput(_cfg("t1"), _ckpt("ck-0001"), _meta(1), {})
    await s.aput_writes(cfg, [("messages", {"role": "assistant", "content": "x"})], "task-A")
    got = await s.aget_tuple(cfg)
    assert got is not None
    assert ("task-A", "messages", {"role": "assistant", "content": "x"}) in got.pending_writes


async def test_list_order_limit_before(tmp_path):
    s = FileCheckpointSaver(str(tmp_path))
    prev = _cfg("t1")
    for i in range(1, 6):
        prev = await s.aput(prev, _ckpt(f"ck-{i:04d}"), _meta(i), {})
    ids = [t.checkpoint["id"] async for t in s.alist(_cfg("t1"))]
    assert ids == ["ck-0005", "ck-0004", "ck-0003", "ck-0002", "ck-0001"]  # newest-first
    limited = [t.checkpoint["id"] async for t in s.alist(_cfg("t1"), limit=2)]
    assert limited == ["ck-0005", "ck-0004"]
    bcfg = _cfg("t1", cid="ck-0003")
    before = [t.checkpoint["id"] async for t in s.alist(_cfg("t1"), before=bcfg)]
    assert before == ["ck-0002", "ck-0001"]


async def test_safe_latest_skips_torn_and_ignores_tmp(tmp_path):
    s = FileCheckpointSaver(str(tmp_path))
    await s.aput(_cfg("t1"), _ckpt("ck-0001"), _meta(1), {})
    await s.aput(_cfg("t1", cid="ck-0001"), _ckpt("ck-0002"), _meta(2), {})
    d = s._dir("t1", "")
    # a TRUNCATED file that sorts NEWEST + a leftover tmp — must be skipped/ignored
    with open(os.path.join(d, "ck-9999.ckpt.json"), "w") as f:
        f.write('{"checkpoint_id": "ck-9999", "ctype": "json", "checkpo')  # torn
    with open(os.path.join(d, "ck-9998.ckpt.json.deadbeef.tmp"), "w") as f:
        f.write("partial")
    latest = await s.aget_tuple(_cfg("t1"))
    assert latest is not None
    assert latest.checkpoint["id"] == "ck-0002"  # fell back past the torn newest
    ids = [t.checkpoint["id"] async for t in s.alist(_cfg("t1"))]
    assert ids == ["ck-0002", "ck-0001"]  # torn skipped, tmp never listed


async def test_ns_isolation_and_delete_thread(tmp_path):
    s = FileCheckpointSaver(str(tmp_path))
    await s.aput(_cfg("t1", ns=""), _ckpt("ck-0001"), _meta(1), {})
    await s.aput(_cfg("t1", ns="sub:abc"), _ckpt("ck-0009"), _meta(9), {})
    # ns isolates: the default-ns latest is not the subgraph's checkpoint
    root = await s.aget_tuple(_cfg("t1", ns=""))
    sub = await s.aget_tuple(_cfg("t1", ns="sub:abc"))
    assert root.checkpoint["id"] == "ck-0001"
    assert sub.checkpoint["id"] == "ck-0009"
    await s.adelete_thread("t1")
    assert await s.aget_tuple(_cfg("t1", ns="")) is None
    assert await s.aget_tuple(_cfg("t1", ns="sub:abc")) is None
