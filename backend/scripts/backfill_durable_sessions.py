"""One-time backfill: copy local-only kernel sessions into the durable store.

When an install switches ``KERNEL_STORE`` local → pg/remote, sessions created
*before* the switch live only in the local ``kernel.db``. pg/remote mode reads
the durable backend (DataService design §5), so those older sessions — and their
messages + event history — read as missing. This copies every local session not
already present in the durable into it.

Idempotent at **session granularity**: a session already present in the durable
is skipped (so events are never double-appended on a re-run). A session present
in the durable but with fewer events than locally is reported, not patched —
inspect it manually (a partial copy from an interrupted run).

Run it with the SAME ``VALUZ_DURABLE_DATABASE_URL`` the app uses in pg mode (the
owner-role DSN, which bypasses RLS for this admin copy)::

    VALUZ_DURABLE_DATABASE_URL='postgresql+asyncpg://valuz:valuz@127.0.0.1:5432/valuz_kernel' \\
        uv run python scripts/backfill_durable_sessions.py [--dry-run]

Back up both stores first (the durable especially). This only ever INSERTs
missing rows — it never updates or deletes.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect for src.*/app.*


async def _all_events(store, user_id: str, session_id: str) -> list:
    out: list = []
    cursor = 0
    while True:
        page = await store.get_events_after(user_id, session_id, after_seq=cursor, limit=500)
        if not page:
            break
        out.extend(page)
        cursor = page[-1].seq
        if len(page) < 500:
            break
    return out


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="report what would copy; write nothing")
    args = ap.parse_args()

    from src.core import Event

    from valuz_agent.boot.kernel import build_host_data_service_store
    from valuz_agent.infra.config import settings

    dsn = os.environ.get("VALUZ_DURABLE_DATABASE_URL", "")
    if not dsn:
        print("error: VALUZ_DURABLE_DATABASE_URL is not set", file=sys.stderr)
        return 2

    local_store, local_engine = build_host_data_service_store(settings.kernel_db_url_async)
    durable_store, durable_engine = build_host_data_service_store(dsn)

    copied = skipped = partial = 0
    try:
        sessions = await local_store.list_sessions(None, limit=1_000_000)
        print(f"local sessions: {len(sessions)}  durable DSN: {dsn.split('@')[-1]}")
        for s in sessions:
            uid = s.user_id
            existing = await durable_store.load_session(uid, s.id)
            local_events = await _all_events(local_store, uid, s.id)

            if existing is not None:
                durable_events = await _all_events(durable_store, uid, s.id)
                if len(durable_events) < len(local_events):
                    partial += 1
                    print(
                        f"  PARTIAL session {s.id} (owner={uid}): "
                        f"durable has {len(durable_events)} events, local {len(local_events)} "
                        f"— left untouched, inspect manually"
                    )
                else:
                    skipped += 1
                continue

            msgs = await local_store.list_messages_for_session(uid, s.id, limit=1_000_000)
            print(
                f"  {'(dry) ' if args.dry_run else ''}copy session {s.id} (owner={uid}) "
                f"msgs={len(msgs)} events={len(local_events)}"
            )
            if args.dry_run:
                copied += 1
                continue

            await durable_store.save_session(s)
            for m in msgs:
                await durable_store.save_message(uid, m)
            for e in local_events:
                await durable_store.append_event(
                    uid, s.id, e.message_id, Event(type=e.type, data=e.data, timestamp=e.timestamp)
                )
            copied += 1
    finally:
        await local_engine.dispose()
        await durable_engine.dispose()

    verb = "would copy" if args.dry_run else "copied"
    print(
        f"\ndone: {verb}={copied}  skipped(already present)={skipped}  "
        f"partial(needs attention)={partial}  total_local={len(sessions)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
