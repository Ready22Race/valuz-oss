from pydantic import BaseModel
from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from valuz_agent.infra.database import Base, PrimaryKeyMixin, TimestampMixin, UserMixin


class ProjectRow(Base, PrimaryKeyMixin, TimestampMixin, UserMixin):
    __tablename__ = "valuz_project"

    name: Mapped[str] = mapped_column(String(256))
    kind: Mapped[str] = mapped_column(String(32))  # chat | project
    root_path: Mapped[str | None] = mapped_column(Text)
    icon: Mapped[str | None] = mapped_column(String(16))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    # ``instructions_md`` (formerly the 1:1 ``valuz_workspace_context`` table,
    # folded into the main row) is the user-authored prompt source.
    instructions_md: Mapped[str | None] = mapped_column(Text)
    # DEPRECATED / inert: the early single-blob project memory. Superseded by the
    # file-based project memory at ``~/.valuz/memories/projects/<id>/`` (see
    # modules/memory). Nothing reads or writes these anymore; they are kept only
    # because the host's 0-migration baseline policy (boot/schema.py) makes a
    # physical column drop a full host-table reset — they drop for free at the
    # next baseline regen.
    memory_summary: Mapped[str | None] = mapped_column(Text)
    memory_version: Mapped[int] = mapped_column(Integer, default=0)


class ProjectCreateRequest(BaseModel):
    name: str
    root_path: str
