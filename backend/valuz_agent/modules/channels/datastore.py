"""Datastore for channel thread bindings."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.modules.channels.models import AgentChannelBindingRow, ChannelThreadBindingRow
from valuz_agent.modules.channels.schemas import (
    AgentChannelBinding,
    ChannelRouteKey,
    ChannelThreadBinding,
)


class AgentChannelBindingDatastore:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get(
        self,
        *,
        user_id: str,
        platform: str,
        agent_slug: str,
    ) -> AgentChannelBinding | None:
        row = (
            (
                await self._db.execute(
                    select(AgentChannelBindingRow).where(
                        AgentChannelBindingRow.user_id == user_id,
                        AgentChannelBindingRow.platform == platform,
                        AgentChannelBindingRow.agent_slug == agent_slug,
                    )
                )
            )
            .scalars()
            .first()
        )
        return _agent_binding_to_schema(row) if row is not None else None

    async def list_enabled(
        self,
        *,
        platform: str,
        user_id: str | None = None,
    ) -> list[AgentChannelBinding]:
        """Enabled bindings for a platform.

        ``user_id=None`` lists across owners — the long-connection supervisors
        use it because a background loader has no request identity to filter
        by; each row carries its own owner (the supervisor must never guess one
        from ambient process identity).
        """
        conditions = [
            AgentChannelBindingRow.platform == platform,
            AgentChannelBindingRow.enabled.is_(True),
        ]
        if user_id is not None:
            conditions.append(AgentChannelBindingRow.user_id == user_id)
        rows = (
            (
                await self._db.execute(
                    select(AgentChannelBindingRow)
                    .where(*conditions)
                    .order_by(AgentChannelBindingRow.updated_at.asc())
                )
            )
            .scalars()
            .all()
        )
        return [_agent_binding_to_schema(row) for row in rows]

    async def get_enabled_by_channel_instance(
        self,
        *,
        platform: str,
        channel_instance_id: str,
    ) -> AgentChannelBinding | None:
        row = (
            (
                await self._db.execute(
                    select(AgentChannelBindingRow)
                    .where(
                        AgentChannelBindingRow.platform == platform,
                        AgentChannelBindingRow.channel_instance_id == channel_instance_id,
                        AgentChannelBindingRow.enabled.is_(True),
                    )
                    .order_by(AgentChannelBindingRow.updated_at.desc())
                )
            )
            .scalars()
            .first()
        )
        return _agent_binding_to_schema(row) if row is not None else None

    async def upsert(
        self,
        *,
        user_id: str,
        platform: str,
        agent_slug: str,
        channel_instance_id: str,
        bot_id: str,
        secret_ref: str | None,
        enabled: bool,
        bot_name: str | None = None,
        ws_url: str | None = None,
    ) -> AgentChannelBinding:
        row = (
            (
                await self._db.execute(
                    select(AgentChannelBindingRow).where(
                        AgentChannelBindingRow.user_id == user_id,
                        AgentChannelBindingRow.platform == platform,
                        AgentChannelBindingRow.agent_slug == agent_slug,
                    )
                )
            )
            .scalars()
            .first()
        )
        if row is None:
            row = AgentChannelBindingRow(
                user_id=user_id,
                platform=platform,
                agent_slug=agent_slug,
                channel_instance_id=channel_instance_id,
                bot_id=bot_id,
                secret_ref=secret_ref,
                enabled=enabled,
                bot_name=bot_name,
                ws_url=ws_url,
            )
            self._db.add(row)
        else:
            row.channel_instance_id = channel_instance_id
            row.bot_id = bot_id
            row.secret_ref = secret_ref
            row.enabled = enabled
            row.bot_name = bot_name
            row.ws_url = ws_url
        await self._db.commit()
        await self._db.refresh(row)
        return _agent_binding_to_schema(row)


class ChannelThreadBindingDatastore:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_for_thread(
        self,
        *,
        user_id: str,
        channel_instance_id: str,
        external_chat_id: str,
        external_thread_id: str,
        agent_slug: str,
    ) -> ChannelThreadBinding | None:
        row = (
            (
                await self._db.execute(
                    select(ChannelThreadBindingRow)
                    .where(
                        ChannelThreadBindingRow.user_id == user_id,
                        ChannelThreadBindingRow.channel_instance_id == channel_instance_id,
                        ChannelThreadBindingRow.external_chat_id == external_chat_id,
                        ChannelThreadBindingRow.external_thread_id == external_thread_id,
                        ChannelThreadBindingRow.agent_slug == agent_slug,
                    )
                    .order_by(ChannelThreadBindingRow.updated_at.desc())
                )
            )
            .scalars()
            .first()
        )
        return _row_to_binding(row) if row is not None else None

    async def upsert(self, *, user_id: str, key: ChannelRouteKey, session_id: str) -> None:
        row = (
            (
                await self._db.execute(
                    select(ChannelThreadBindingRow).where(
                        ChannelThreadBindingRow.user_id == user_id,
                        ChannelThreadBindingRow.channel_instance_id == key.channel_instance_id,
                        ChannelThreadBindingRow.external_chat_id == key.external_chat_id,
                        ChannelThreadBindingRow.external_thread_id == key.external_thread_id,
                        ChannelThreadBindingRow.agent_slug == key.agent_slug,
                        ChannelThreadBindingRow.project_id == key.project_id,
                    )
                )
            )
            .scalars()
            .first()
        )
        if row is None:
            row = ChannelThreadBindingRow(
                user_id=user_id,
                channel_instance_id=key.channel_instance_id,
                external_chat_id=key.external_chat_id,
                external_thread_id=key.external_thread_id,
                agent_slug=key.agent_slug,
                project_id=key.project_id,
                session_id=session_id,
            )
            self._db.add(row)
        else:
            row.session_id = session_id
        await self._db.commit()


def _row_to_binding(row: ChannelThreadBindingRow) -> ChannelThreadBinding:
    return ChannelThreadBinding(
        channel_instance_id=row.channel_instance_id,
        external_chat_id=row.external_chat_id,
        external_thread_id=row.external_thread_id,
        agent_slug=row.agent_slug,
        project_id=row.project_id,
        session_id=row.session_id,
    )


def _agent_binding_to_schema(row: AgentChannelBindingRow) -> AgentChannelBinding:
    return AgentChannelBinding(
        id=row.id,
        owner_user_id=row.user_id,
        platform=row.platform,
        channel_instance_id=row.channel_instance_id,
        agent_slug=row.agent_slug,
        bot_id=row.bot_id,
        secret_ref=row.secret_ref,
        enabled=row.enabled,
        bot_name=row.bot_name,
        ws_url=row.ws_url,
    )


__all__ = ["AgentChannelBindingDatastore", "ChannelThreadBindingDatastore"]
