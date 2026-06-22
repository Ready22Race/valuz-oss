"""Connector ORM model.

A connector represents an MCP server the user has wired into their project.
There are three flavours:

- ``builtin``: First-party data sources bundled with Valuz (e.g. the
  Reportify MCP). Seeded at boot; the user cannot delete them.
- ``directory``: Well-known third-party MCP servers surfaced in the
  Connector Directory (GitHub, Notion, Linear, …). Installed via the
  in-conversation ``connector_install`` flow with dynamic OAuth (RFC 7591).
- ``custom``: User-defined MCP servers. Two transports:
  ``http``  — any HTTP/SSE-based MCP server reachable over the network.
  ``stdio`` — local process-based MCP server (filesystem, git, browser, …)
              spawned by the Electron main process.

The connector's optional/blob attributes — header/param credentials, OAuth
metadata / client info / token (+ expiry), and stdio args / env — live OUT of
this row in a sparse ``valuz_connector_attr`` key→value table (one row per
present attribute). ``ConnectorRow`` exposes them as transparent properties, so
callers still read/write ``row.headers_json``, ``row.oauth_token_json`` etc.
unchanged. ``headers_json`` / ``params_json`` hold ``{name: {"value", "secret"}}``
— ``secret: true`` values are plaintext at rest but withheld from ``GET``.

The transient PKCE handoff during the OAuth dance is NOT stored here — it is
ephemeral auth scratch kept in ``ext.cache`` (a file cache locally, Redis on the
shared backend), keyed by the ``state`` token.
"""

from __future__ import annotations

from typing import Literal

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.orm.collections import attribute_keyed_dict

from valuz_agent.infra.database import Base, PrimaryKeyMixin, TimestampMixin, UserMixin
from valuz_agent.infra.time_utils import now_ms


def _attr_prop(key: str) -> property:
    """A transparent ``str | None`` property backed by ``ConnectorRow._attrs``."""
    return property(
        lambda self: self._attr_get(key),
        lambda self, v: self._attr_set(key, v),
    )

# Canonical set of connector auth strategies. Single source of truth shared
# by the API schemas, the service layer and the catalog so callers don't
# have to guess valid values. ``oauth`` is the self-contained PKCE flow
# (connectors.py); ``bearer`` / ``none`` are now purely informational —
# header/param injection is driven solely by the object-list + per-entry
# ``secret`` (see service.build_overrides), not by auth_type. There is
# deliberately no ``oauth_account`` and no ``api_key``.
AuthType = Literal["none", "bearer", "oauth"]

# Canonical set of connector transports. ``http``/``sse`` are network MCP
# servers; ``stdio`` is a local process spawned by the desktop shell.
TransportType = Literal["http", "sse", "stdio"]


class ConnectorRow(Base, PrimaryKeyMixin, TimestampMixin, UserMixin):
    """One MCP connector installed (or built-in) for the local user."""

    __tablename__ = "valuz_connector"
    # DB-level enforcement of the canonical AuthType / TransportType sets —
    # the column stays a plain String (SQLite has no native enum) but a
    # CHECK constraint rejects out-of-set values at write time.
    __table_args__ = (
        CheckConstraint(
            "auth_type IN ('none', 'bearer', 'oauth')",
            name="ck_valuz_connector_auth_type",
        ),
        CheckConstraint(
            "transport IN ('http', 'sse', 'stdio')",
            name="ck_valuz_connector_transport",
        ),
    )

    slug: Mapped[str] = mapped_column(String(128), unique=True)
    display_name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text)

    connector_type: Mapped[str] = mapped_column(String(32))
    transport: Mapped[str] = mapped_column(String(16), default="http")

    url: Mapped[str | None] = mapped_column(Text)

    auth_type: Mapped[str] = mapped_column(String(32), default="none")

    command: Mapped[str | None] = mapped_column(Text)
    working_dir: Mapped[str | None] = mapped_column(Text)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    status: Mapped[str] = mapped_column(String(32), default="unknown")
    tool_count: Mapped[int | None] = mapped_column(Integer)
    last_tested_at: Mapped[int | None] = mapped_column(BigInteger)
    error_message: Mapped[str | None] = mapped_column(Text)

    # ── Sparse extension attributes (valuz_connector_attr) ────────────────
    # The optional blob attributes live in a key→value side table, eagerly
    # loaded with the row and exposed as the properties below so callers keep
    # using ``row.headers_json`` etc. unchanged. There is no DB-level
    # ForeignKey (matching the other ``valuz_*`` tables); the relationship is
    # joined explicitly via ``foreign()`` and cleanup is handled by the ORM
    # cascade plus the datastore's explicit attr delete.
    _attrs: Mapped[dict[str, ConnectorAttrRow]] = relationship(
        "ConnectorAttrRow",
        collection_class=attribute_keyed_dict("key"),
        cascade="all, delete-orphan",
        lazy="selectin",
        primaryjoin="foreign(ConnectorAttrRow.connector_id) == ConnectorRow.id",
    )

    def _attr_get(self, key: str) -> str | None:
        a = self._attrs.get(key)
        return a.value if a is not None else None

    def _attr_set(self, key: str, value: object) -> None:
        if value is None:
            self._attrs.pop(key, None)
        elif key in self._attrs:
            self._attrs[key].value = str(value)
        else:
            # ``user_id`` is best-effort here (the parent may not be owned yet
            # at construction time); the datastore re-stamps it authoritatively
            # from the caller's owner before commit.
            self._attrs[key] = ConnectorAttrRow(
                key=key, value=str(value), user_id=self.user_id
            )

    # The accessor names keep the ``_json`` suffix (callers read ``row.headers_json``
    # etc. unchanged), but the stored attr KEY drops it — the table holds
    # ``headers`` / ``oauth_token`` / … , not ``headers_json``.
    oauth_metadata_json = _attr_prop("oauth_metadata")
    oauth_client_info_json = _attr_prop("oauth_client_info")
    oauth_token_json = _attr_prop("oauth_token")
    args_json = _attr_prop("args")
    env_json = _attr_prop("env")
    # Self-describing header/param entries ``{name: {"value", "secret"}}``.
    headers_json = _attr_prop("headers")
    params_json = _attr_prop("params")

    @property
    def oauth_token_expires_at(self) -> int | None:
        v = self._attr_get("oauth_token_expires_at")
        return int(v) if v is not None else None

    @oauth_token_expires_at.setter
    def oauth_token_expires_at(self, v: int | None) -> None:
        self._attr_set("oauth_token_expires_at", None if v is None else int(v))


class ConnectorAttrRow(Base, UserMixin):
    """Sparse ``connector_id → key → value`` extension attributes.

    Holds a connector's optional blob attributes (header/param creds, OAuth
    metadata/client/token + expiry, stdio args/env) out of the main row — one
    row per present attribute. ``ConnectorRow`` proxies them as properties.

    Carries the owner's ``user_id`` (``UserMixin``) like every other business
    table, and deliberately has NO DB-level ForeignKey to ``valuz_connector``:
    referential cleanup is the ORM cascade's job (plus the datastore's explicit
    attr delete), consistent with the other ``valuz_*`` tables.
    """

    __tablename__ = "valuz_connector_attr"

    connector_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class ProjectConnectorRow(Base, UserMixin):
    """Which connectors a project has enabled (per-owner, per-project).

    Replaces the legacy ``<project>/.claude/project-config.json`` ``connectors``
    list: that file-backed store assumed a per-user local filesystem, which a
    shared multi-client backend does not have. One row per (project, slug);
    mirrors the skills module's ``ProjectSkillConfigRow``.
    """

    __tablename__ = "valuz_project_connector"

    project_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    slug: Mapped[str] = mapped_column(String(128), primary_key=True)
    added_at: Mapped[int] = mapped_column(BigInteger, default=now_ms)
