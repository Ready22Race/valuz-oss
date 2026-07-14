"""DRAFT — paste into OSS at
``vendor/valuz-oss/backend/kernel/src/adapters/file_checkpoint_saver.py``
(land via a valuz-agent PR; this repo only holds the draft).

FileCheckpointSaver — a LangGraph ``BaseCheckpointSaver`` that persists each
checkpoint as an IMMUTABLE, uniquely-named file under a directory that the
sandbox mounts to per-owner COS (same class as ~/.claude). Chosen over sqlite
after empirical proof (qa, 2026-07) that sqlite-on-COS corrupts (WAL -shm mmap /
fcntl / whole-object PUT → SIGBUS / "database disk image is malformed"), while
write-once files on COS never corrupt.

Design invariants (each validated on qa sandboxes):
  1. write-once + unique name + tmp→rename commit  → no torn/partial reads
     (cosfs rename = server-side atomic COPY; readers ignore ``*.tmp``).
  2. "latest" = list newest→older, skip FileNotFoundError (phantom listing) AND
     JSONDecodeError (a torn tail), return first that loads (``_safe_latest``).
     COS is strong read-after-write per known key; only LIST is eventually
     consistent, so never trust a bare max(listdir)+open.
  3. checkpoint_id is a time-ordered uuid6 → lexicographic filename order == time
     order, so newest-first == sort(reverse=True).
  4. single-writer per thread (enforced upstream by the session lease) — the COS
     mount isolates per OWNER (per-owner subpath) and per SESSION (thread_id
     subdir); it does NOT protect two sandboxes driving the SAME session
     (double-drive) — that is the lease's job, exactly like ~/.claude.
  5. host pre-creates the COS prefix at provision (it is just another
     ``_mount_specs`` entry) — a missing prefix = dead mount = write raises.

Known boundaries handled elsewhere:
  - cold-mount LIST lag: a fresh sandbox resuming the instant the writer died may
    not yet LIST the very newest file → resume from N-1 → LangGraph replays one
    superstep (idempotent via pending writes). Optionally cross-check the
    expected latest checkpoint_id against the (synchronously durable) kernel
    events in PG.
  - unbounded growth: ``gc(keep=...)`` prunes old checkpoints; call it from a
    retention hook. listdir is O(N) (measured 201 files → ~50-200ms).

Async file I/O wraps blocking cosfs ops in ``asyncio.to_thread`` so graph
execution never blocks. Sync methods reuse the same blocking helpers.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import tempfile
import uuid
from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any
from urllib.parse import quote

from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from langgraph.checkpoint.serde.base import SerializerProtocol

_CKPT_SUFFIX = ".ckpt.json"
_WRITES_SUFFIX = ".writes.json"
_NS_ROOT = "_ns_root"  # directory name for the default (empty) checkpoint_ns


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _unb64(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


def _seg(s: str) -> str:
    """A filesystem/COS-safe path segment. ``quote`` encodes ``/ : | ..`` etc.;
    empty ns maps to a fixed dir so we never emit an empty path component."""
    return quote(s, safe="") if s else _NS_ROOT


class FileCheckpointSaver(BaseCheckpointSaver[str]):
    def __init__(self, root: str, *, serde: SerializerProtocol | None = None) -> None:
        super().__init__(serde=serde)
        self.root = root

    # ---- paths ----------------------------------------------------------
    def _dir(self, thread_id: str, ns: str) -> str:
        return os.path.join(self.root, _seg(thread_id), _seg(ns))

    def _ckpt_path(self, thread_id: str, ns: str, cid: str) -> str:
        return os.path.join(self._dir(thread_id, ns), _seg(cid) + _CKPT_SUFFIX)

    def _writes_path(self, thread_id: str, ns: str, cid: str, task_id: str) -> str:
        name = f"{_seg(cid)}.{_seg(task_id)}{_WRITES_SUFFIX}"
        return os.path.join(self._dir(thread_id, ns), name)

    # ---- blocking helpers (reused by sync + async) ----------------------
    @staticmethod
    def _write_json(path: str, doc: Any) -> None:
        """Atomic commit: write a unique temp file in the same dir, fsync, then
        rename over the final name (cosfs rename = server-side atomic COPY).
        A crash leaves only an ignored ``*.tmp``; readers never see a partial."""
        d = os.path.dirname(path)
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, suffix=f".{uuid.uuid4().hex}.tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(doc, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    @staticmethod
    def _read_json(path: str) -> dict[str, Any] | None:
        try:
            with open(path) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def _list_ckpt_ids_desc(self, thread_id: str, ns: str) -> list[str]:
        d = self._dir(thread_id, ns)
        try:
            names = os.listdir(d)
        except FileNotFoundError:
            return []
        ids = [n[: -len(_CKPT_SUFFIX)] for n in names if n.endswith(_CKPT_SUFFIX)]
        return sorted(ids, reverse=True)  # invariant 3: lexicographic == time desc

    def _safe_latest(self, thread_id: str, ns: str) -> dict[str, Any] | None:
        """Newest checkpoint doc that actually loads (invariant 2)."""
        for seg_cid in self._list_ckpt_ids_desc(thread_id, ns):
            doc = self._read_json(os.path.join(self._dir(thread_id, ns), seg_cid + _CKPT_SUFFIX))
            if doc is not None:
                return doc
        return None

    def _load_writes(self, thread_id: str, ns: str, cid: str) -> list[tuple[str, str, Any]]:
        d = self._dir(thread_id, ns)
        prefix = _seg(cid) + "."
        out: list[tuple[str, str, Any]] = []
        try:
            names = os.listdir(d)
        except FileNotFoundError:
            return out
        for n in names:
            if not (n.startswith(prefix) and n.endswith(_WRITES_SUFFIX)):
                continue
            task_seg = n[len(prefix) : -len(_WRITES_SUFFIX)]
            doc = self._read_json(os.path.join(d, n))
            if not doc:
                continue
            for item in doc:
                value = self.serde.loads_typed((item["wtype"], _unb64(item["value"])))
                out.append((task_seg, item["channel"], value))
        return out

    def _tuple_from_doc(self, thread_id: str, ns: str, doc: dict[str, Any]) -> CheckpointTuple:
        cid = doc["checkpoint_id"]
        checkpoint: Checkpoint = self.serde.loads_typed((doc["ctype"], _unb64(doc["checkpoint"])))
        metadata: CheckpointMetadata = self.serde.loads_typed(
            (doc["mtype"], _unb64(doc["metadata"]))
        )
        parent = doc.get("parent_checkpoint_id")
        cfg = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ns, "checkpoint_id": cid}}
        parent_cfg = (
            {"configurable": {"thread_id": thread_id, "checkpoint_ns": ns, "checkpoint_id": parent}}
            if parent
            else None
        )
        return CheckpointTuple(
            config=cfg,
            checkpoint=checkpoint,
            metadata=metadata,
            parent_config=parent_cfg,
            pending_writes=self._load_writes(thread_id, ns, cid),
        )

    def _put_doc(self, config: dict, checkpoint: Checkpoint, metadata: CheckpointMetadata) -> dict:
        thread_id = config["configurable"]["thread_id"]
        ns = config["configurable"].get("checkpoint_ns", "")
        cid = checkpoint["id"]
        ctype, cbytes = self.serde.dumps_typed(checkpoint)  # full checkpoint incl. channel_values
        mtype, mbytes = self.serde.dumps_typed(metadata)
        doc = {
            "v": 1,
            "checkpoint_id": cid,
            "parent_checkpoint_id": config["configurable"].get("checkpoint_id"),
            "ctype": ctype,
            "checkpoint": _b64(cbytes),
            "mtype": mtype,
            "metadata": _b64(mbytes),
        }
        self._write_json(self._ckpt_path(thread_id, ns, cid), doc)
        return {"configurable": {"thread_id": thread_id, "checkpoint_ns": ns, "checkpoint_id": cid}}

    def _put_writes_doc(
        self, config: dict, writes: Sequence[tuple[str, Any]], task_id: str
    ) -> None:
        thread_id = config["configurable"]["thread_id"]
        ns = config["configurable"].get("checkpoint_ns", "")
        cid = config["configurable"]["checkpoint_id"]
        items = []
        for idx, (channel, value) in enumerate(writes):
            wtype, wbytes = self.serde.dumps_typed(value)
            items.append({"idx": idx, "channel": channel, "wtype": wtype, "value": _b64(wbytes)})
        # one file per (checkpoint_id, task_id); re-call for the same task overwrites (last wins).
        self._write_json(self._writes_path(thread_id, ns, cid, task_id), items)

    # ---- sync API -------------------------------------------------------
    def put(
        self,
        config: dict,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: Any,
    ) -> dict:
        return self._put_doc(config, checkpoint, metadata)

    def put_writes(
        self, config: dict, writes: Sequence[tuple[str, Any]], task_id: str, task_path: str = ""
    ) -> None:
        self._put_writes_doc(config, writes, task_id)

    def get_tuple(self, config: dict) -> CheckpointTuple | None:
        thread_id = config["configurable"]["thread_id"]
        ns = config["configurable"].get("checkpoint_ns", "")
        cid = config["configurable"].get("checkpoint_id")
        doc = (
            self._read_json(self._ckpt_path(thread_id, ns, cid))
            if cid
            else self._safe_latest(thread_id, ns)
        )
        return self._tuple_from_doc(thread_id, ns, doc) if doc else None

    def list(
        self,
        config: dict | None,
        *,
        filter: dict[str, Any] | None = None,
        before: dict | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        if config is None:
            return
        thread_id = config["configurable"]["thread_id"]
        ns = config["configurable"].get("checkpoint_ns", "")
        before_id = before["configurable"]["checkpoint_id"] if before else None
        n = 0
        for seg_cid in self._list_ckpt_ids_desc(thread_id, ns):
            doc = self._read_json(os.path.join(self._dir(thread_id, ns), seg_cid + _CKPT_SUFFIX))
            if doc is None:
                continue  # phantom / torn — skip
            if before_id is not None and not (doc["checkpoint_id"] < before_id):
                continue
            if filter:
                meta = self.serde.loads_typed((doc["mtype"], _unb64(doc["metadata"])))
                if any(meta.get(k) != v for k, v in filter.items()):
                    continue
            yield self._tuple_from_doc(thread_id, ns, doc)
            n += 1
            if limit is not None and n >= limit:
                return

    def delete_thread(self, thread_id: str) -> None:
        shutil.rmtree(os.path.join(self.root, _seg(thread_id)), ignore_errors=True)

    # ---- async API (wrap blocking cosfs ops off the loop) ---------------
    async def aput(
        self,
        config: dict,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: Any,
    ) -> dict:
        return await asyncio.to_thread(self._put_doc, config, checkpoint, metadata)

    async def aput_writes(
        self, config: dict, writes: Sequence[tuple[str, Any]], task_id: str, task_path: str = ""
    ) -> None:
        await asyncio.to_thread(self._put_writes_doc, config, writes, task_id)

    async def aget_tuple(self, config: dict) -> CheckpointTuple | None:
        return await asyncio.to_thread(self.get_tuple, config)

    async def alist(
        self,
        config: dict | None,
        *,
        filter: dict[str, Any] | None = None,
        before: dict | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        items = await asyncio.to_thread(
            lambda: list(self.list(config, filter=filter, before=before, limit=limit))
        )
        for it in items:
            yield it

    async def adelete_thread(self, thread_id: str) -> None:
        await asyncio.to_thread(self.delete_thread, thread_id)

    # ---- retention (call from a hook; unbounded growth otherwise) --------
    def gc(self, thread_id: str, ns: str = "", *, keep: int = 200) -> int:
        """Keep the newest ``keep`` checkpoints (+ their writes) for a thread/ns,
        delete older ones. Returns the count removed. Safe under single-writer."""
        ids = self._list_ckpt_ids_desc(thread_id, ns)
        d = self._dir(thread_id, ns)
        removed = 0
        for seg_cid in ids[keep:]:
            try:
                os.unlink(os.path.join(d, seg_cid + _CKPT_SUFFIX))
                removed += 1
            except OSError:
                pass
            for n in os.listdir(d) if os.path.isdir(d) else []:
                if n.startswith(seg_cid + ".") and n.endswith(_WRITES_SUFFIX):
                    try:
                        os.unlink(os.path.join(d, n))
                    except OSError:
                        pass
        return removed
