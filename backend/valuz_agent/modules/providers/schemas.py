"""Shared provider-list contract (ADR-011).

``LLMChannel`` is the single "one channel, one row" display view shared
across **frontend / OSS / extension point / overlay**. It is self-contained and
key-free: a row carries everything the picker UIs need to render and group a
channel, and nothing a credential resolver needs (those live on the
``resolve`` path — see :mod:`valuz_agent.ports.llm_provider`).

Field-ownership rule: **one value per channel goes on the outer item; anything
that differs per model goes on its :class:`LLMModel`.**

This module is a leaf — pure dataclasses, no imports from ``ports`` or other
modules — so both the port (which annotates ``list() -> list[LLMChannel]``)
and producers (OSS service + overlay catalogs) can depend on it without cycles.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class LLMModel:
    """One selectable model under a channel — self-contained, one row.

    Attributes:
        id: Wire id; rides the request ``model`` field as-is.
        label: Display name; ``None`` → the frontend falls back to
            ``modelLabel(id)``.
        protocols: The wire protocols THIS model speaks (UI hyphen form:
            ``anthropic`` / ``openai-completion`` / ``openai-response`` /
            ``gemini``). ``()`` → "no declared restriction": the consumer
            falls back to the channel-level ``compatible_protocols``.
    """

    id: str
    label: str | None = None
    protocols: tuple[str, ...] = ()


@dataclass
class LLMChannel:
    """A single channel row in the provider list.

    Producers fill their own rows (OSS judges its user rows; the extension
    point judges its contributed rows); OSS appends both with no further
    judgement (ADR-011 "组装：拼接，不判断他方"). ``source`` / ``group`` are
    opaque keys — the producer sets them, OSS passes them through.
    """

    # ── identity ──────────────────────────────────────────────────────
    id: str
    name: str
    provider_kind: str
    # ── source / governance (opaque keys; set by the producing side) ──
    source: str
    deletable: bool
    is_default: bool
    credential_source: str
    # one value per channel → outer item
    default_model: str | None = None
    auth_type: str = "api_key"
    enabled: bool = True
    test_status: str = "never"
    # Human-readable reason the channel is currently unavailable, e.g.
    # "未登录 Valuz 账户". ``None`` when available.
    unavailable_reason: str | None = None
    # ── protocols (channel level; manage-area "可用于" badge) ─────────
    protocol: str | None = None
    effective_protocol: str = "openai-completion"
    compatible_protocols: list[str] = field(default_factory=list)
    # ── capability / grouping ─────────────────────────────────────────
    # Serves the Responses API → can drive the codex runtime. A capability
    # flag, NOT a source judgement.
    serves_responses: bool = False
    # Opaque grouping key (frontend localizes into a section header).
    group: str = "api_key"
    # Group sort, smaller = earlier.
    group_rank: int = 50
    # ── models ────────────────────────────────────────────────────────
    models: list[LLMModel] = field(default_factory=list)


@dataclass
class LLMChannelDetail(LLMChannel):
    """The edit-dialog view — list row plus connection-management fields."""

    base_url: str | None = None
    supports_custom_base_url: bool = False
    supports_connection_test: bool = True


__all__ = ["LLMChannelDetail", "LLMChannel", "LLMModel"]
