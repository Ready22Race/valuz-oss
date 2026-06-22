"""Connector datastore — async SQLAlchemy ORM access.

All connector state lives in the host DB: connector rows (including their secret
columns), and the per-project connector selection (``valuz_project_connector``,
formerly ``<project>/.claude/project-config.json``). A shared multi-client
backend has no per-user local filesystem, so nothing here touches disk.
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.infra.db import async_commit_with_retry
from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.connectors.models import (
    ConnectorAttrRow,
    ConnectorRow,
    ProjectConnectorRow,
)


class ConnectorDatastore:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_all(self, user_id: str) -> list[ConnectorRow]:
        return list(
            (
                await self._db.execute(
                    select(ConnectorRow)
                    .where(ConnectorRow.user_id == user_id)
                    .order_by(ConnectorRow.display_name)
                )
            )
            .scalars()
            .all()
        )

    async def list_enabled(self, user_id: str) -> list[ConnectorRow]:
        return list(
            (
                await self._db.execute(
                    select(ConnectorRow)
                    .where(ConnectorRow.user_id == user_id, ConnectorRow.enabled)
                    .order_by(ConnectorRow.display_name)
                )
            )
            .scalars()
            .all()
        )

    async def get_by_id(self, user_id: str, connector_id: str) -> ConnectorRow | None:
        return (
            (
                await self._db.execute(
                    select(ConnectorRow).where(
                        ConnectorRow.id == connector_id, ConnectorRow.user_id == user_id
                    )
                )
            )
            .scalars()
            .first()
        )

    async def get_by_slug(self, user_id: str, slug: str) -> ConnectorRow | None:
        return (
            (
                await self._db.execute(
                    select(ConnectorRow).where(
                        ConnectorRow.slug == slug, ConnectorRow.user_id == user_id
                    )
                )
            )
            .scalars()
            .first()
        )

    async def create(self, user_id: str, row: ConnectorRow) -> ConnectorRow:
        # Owner passed explicitly (no ContextVar write-stamp default). Stamp the
        # row AND its sparse attr rows — the latter may have been built (via the
        # property setters) before the owner was known at construction time.
        row.user_id = user_id
        for attr in row._attrs.values():
            attr.user_id = user_id
        self._db.add(row)
        await self._db.commit()
        await self._db.refresh(row)
        return row

    async def update(self, row: ConnectorRow) -> ConnectorRow:
        # ``row`` came from an owner-scoped read; merge preserves its user_id.
        # Re-stamp the attr rows too: a setter run during this update (e.g. a
        # refreshed OAuth token) creates a new attr row that must inherit the
        # owner. ``_attrs`` is selectin-loaded, so this touches no DB.
        for attr in row._attrs.values():
            attr.user_id = row.user_id
        merged = await self._db.merge(row)
        await self._db.commit()
        await self._db.refresh(merged)
        return merged

    async def delete(self, user_id: str, connector_id: str) -> bool:
        row = await self.get_by_id(user_id, connector_id)
        if row is None:
            return False
        # Drop the connector's extension attributes explicitly (a Core bulk
        # delete bypasses ORM cascade, and test sqlite engines don't enable the
        # FK ON DELETE CASCADE the app engine sets).
        await self._db.execute(
            delete(ConnectorAttrRow).where(
                ConnectorAttrRow.connector_id == connector_id,
                ConnectorAttrRow.user_id == user_id,
            )
        )
        await self._db.execute(
            delete(ConnectorRow).where(
                ConnectorRow.id == connector_id, ConnectorRow.user_id == user_id
            )
        )
        await self._db.commit()
        return True

    # ------------------------------------------------------------------
    # Per-project connector selection (persisted in valuz_project_connector)
    # ------------------------------------------------------------------

    async def get_project_connectors(self, user_id: str, project_id: str) -> list[str]:
        rows = (
            (
                await self._db.execute(
                    select(ProjectConnectorRow)
                    .where(
                        ProjectConnectorRow.project_id == project_id,
                        ProjectConnectorRow.user_id == user_id,
                    )
                    # Selection is a membership set (resolved per-slug); order by
                    # slug for a stable, deterministic return — rows inserted in
                    # one ``set`` call share an ``added_at`` so it can't order them.
                    .order_by(ProjectConnectorRow.slug)
                )
            )
            .scalars()
            .all()
        )
        return [r.slug for r in rows]

    async def set_project_connectors(
        self, user_id: str, project_id: str, slugs: list[str]
    ) -> None:
        # Desired-state replace: drop this project's rows, re-insert the new set.
        await self._db.execute(
            delete(ProjectConnectorRow).where(
                ProjectConnectorRow.project_id == project_id,
                ProjectConnectorRow.user_id == user_id,
            )
        )
        added = now_ms()
        self._db.add_all(
            [
                ProjectConnectorRow(
                    project_id=project_id, slug=slug, user_id=user_id, added_at=added
                )
                for slug in slugs
            ]
        )
        await async_commit_with_retry(
            self._db, where="ConnectorDatastore.set_project_connectors"
        )
