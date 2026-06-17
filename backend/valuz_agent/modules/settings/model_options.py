"""Build the "pick a default model" option list (GET /v1/settings/model-options).

This is a *read model* distinct from the provider-management list
(``GET /v1/providers``, which is for add/edit/delete/test). It returns
fully-resolved, render-ready options so the picker UIs (onboarding's
``ConnectStep`` and Settings → Model's default-config card) can stay dumb:

* every model carries the **runtimes it can run on** + a **preferred
  ``default_runtime``** — the frontend never derives a runtime from a
  provider kind again;
* same-named models inside one provider are **disambiguated** here;
* a logical system channel that an overlay registers as multiple
  per-runtime descriptors collapses into one provider with a unioned model
  list (each model still routes to the descriptor that owns it, via its id).

The one thing this endpoint does NOT resolve is CLI-subscription login
state: that credential lives in the local CLI keychain, invisible to the
server. Subscription providers are returned with ``status="client_resolved"``
and the client fills availability in from its own ``checkCliLogin`` probe.

See ``docs/design/model-default-picker-contract.md`` in the commercial repo
for the full contract + rollout.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from valuz_agent.adapters.runtime_registry import RUNTIME_REGISTRY

# All UI (hyphen-form) wire protocols. An empty per-model protocol list means
# "no declared restriction" (gateway semantics) → treat as every protocol so
# the model surfaces on every runtime it could conceivably run on.
_ALL_PROTOCOLS: tuple[str, ...] = (
    "anthropic",
    "openai-completion",
    "openai-response",
    "gemini",
)

# CLI-subscription provider kinds. These pin to their CLI's wire shape and are
# excluded from the deepagents (Valuz Agent) runtime — the runtime can't reach
# the CLI's own keychain. Mirrors the frontend ``SUBSCRIPTION_PROVIDER_KINDS``.
_SUBSCRIPTION_KINDS: frozenset[str] = frozenset({"claude-subscription", "codex-subscription"})

# Preferred runtime when a model can run on more than one. Onboarding's one-click
# pick uses ``default_runtime``; this order is the tie-break. claude_agent first
# (richest reasoning), then codex, then the generic deepagents.
_RUNTIME_PRIORITY: tuple[str, ...] = ("claude_agent", "codex", "deepagents")

# Display order of the groups in the picker.
_GROUP_ORDER: tuple[str, ...] = ("subscription", "system", "api_key", "org")

# provider_kind → the CLI tool the client probes / launches for login.
_CLI_TOOL_BY_KIND: dict[str, str] = {
    "claude-subscription": "claude",
    "codex-subscription": "codex",
}


def _runtimes_for_model(protocols: list[str], provider_kind: str) -> list[str]:
    """Which runtimes can run a model speaking ``protocols``, priority-ordered.

    Mirrors the frontend ``isProviderRuntimeCompatible`` but at the *model*
    granularity (a system channel's models can speak different protocols). Empty
    ``protocols`` = "no declared restriction" → treated as every protocol.
    """
    protos = set(protocols) if protocols else set(_ALL_PROTOCOLS)
    out: set[str] = set()

    # claude_agent: Claude Code SDK only sends anthropic-shape requests.
    if "anthropic" in protos & set(RUNTIME_REGISTRY["claude_agent"].supported_protocols):
        out.add("claude_agent")

    # codex: its own ChatGPT subscription, OR a system/gateway channel serving
    # the Responses API. A bare user OpenAI key can't drive codex (it walks its
    # own keychain), so only codex-subscription / system qualify.
    if provider_kind == "codex-subscription":
        out.add("codex")
    elif provider_kind == "system" and (
        protos & set(RUNTIME_REGISTRY["codex"].supported_protocols)
    ):
        out.add("codex")

    # deepagents: any non-subscription channel speaking a protocol it accepts.
    if provider_kind not in _SUBSCRIPTION_KINDS and (
        protos & set(RUNTIME_REGISTRY["deepagents"].supported_protocols)
    ):
        out.add("deepagents")

    return [r for r in _RUNTIME_PRIORITY if r in out]


def _group_key(source: str, auth_type: str) -> str:
    """Bucket a provider into the picker's display groups."""
    if source == "org":
        return "org"
    if source == "system":
        return "system"
    if auth_type == "oauth":
        return "subscription"
    return "api_key"


# ── Wire schema ──────────────────────────────────────────────────────


class ModelOption(BaseModel):
    model_id: str
    # The provider that OWNS this model — what a pick writes back as
    # ``default_provider_id`` so resolution hits the right descriptor. May
    # differ from the enclosing card's ``provider_id`` when several same-named
    # system descriptors are merged into one display card.
    provider_id: str
    # Display label, disambiguated within its provider (so two genuinely
    # different models that share a name don't read identically).
    label: str
    # Every runtime this model can run on (priority-ordered).
    runtimes: list[str]
    # Preferred runtime for a one-click pick. Always ``runtimes[0]``.
    default_runtime: str
    is_current_default: bool


class ModelOptionProvider(BaseModel):
    provider_id: str
    label: str
    kind: str  # provider_kind
    source: str  # user | system | org | template
    # The CLI tool a subscription provider logs in through (claude / codex);
    # ``None`` for non-subscription providers.
    cli_tool: str | None
    # ``available`` / ``unavailable`` are server-authoritative (system / api_key).
    # ``client_resolved`` = the client must fill it in from CLI keychain state
    # (subscription providers — their credential is local + invisible to us).
    status: str
    unavailable_reason: str | None
    models: list[ModelOption]


class ModelOptionGroup(BaseModel):
    key: str  # subscription | system | api_key | org — frontend localizes the header
    providers: list[ModelOptionProvider]


class CurrentDefault(BaseModel):
    runtime: str | None
    provider_id: str | None
    model: str | None


class ModelOptionsResponse(BaseModel):
    current: CurrentDefault
    groups: list[ModelOptionGroup]


# ── Builder input ────────────────────────────────────────────────────


@dataclass(frozen=True)
class ProviderOptionInput:
    """The subset of ``ProviderListItem`` the builder reads, plus per-model
    protocols. Decoupled from the dataclass so the builder is pure + trivially
    testable (no DB / registry)."""

    id: str
    name: str
    provider_kind: str
    source: str
    auth_type: str
    enabled: bool
    unavailable_reason: str | None
    compatible_protocols: list[str]
    model_options: list[str]
    model_labels: dict[str, str]
    # ``{model_id: [hyphen-form protocols]}`` for providers whose models speak
    # different protocols (system channels). Empty → every model falls back to
    # ``compatible_protocols``.
    model_protocols: dict[str, list[str]]


def _provider_status(p: ProviderOptionInput, group: str) -> tuple[str, str | None]:
    if group == "subscription":
        # Credential is the local CLI keychain — server can't see it.
        return "client_resolved", None
    if p.enabled:
        return "available", None
    return "unavailable", p.unavailable_reason


def _build_raw_provider(
    p: ProviderOptionInput, current: CurrentDefault
) -> ModelOptionProvider | None:
    """One input provider → a card (models NOT yet disambiguated). ``None`` when
    no model has a runnable runtime (the card would be empty noise)."""
    options: list[ModelOption] = []
    for mid in p.model_options:
        protocols = p.model_protocols.get(mid) or p.compatible_protocols
        runtimes = _runtimes_for_model(protocols, p.provider_kind)
        if not runtimes:
            # No runtime can run this model → not a selectable default.
            continue
        options.append(
            ModelOption(
                model_id=mid,
                provider_id=p.id,
                label=p.model_labels.get(mid) or mid,
                runtimes=runtimes,
                default_runtime=runtimes[0],
                is_current_default=(p.id == current.provider_id and mid == current.model),
            )
        )
    if not options:
        return None
    status, reason = _provider_status(p, _group_key(p.source, p.auth_type))
    return ModelOptionProvider(
        provider_id=p.id,
        label=p.name,
        kind=p.provider_kind,
        source=p.source,
        cli_tool=_CLI_TOOL_BY_KIND.get(p.provider_kind),
        status=status,
        unavailable_reason=reason,
        models=options,
    )


def _merge_same_name(cards: list[ModelOptionProvider]) -> list[ModelOptionProvider]:
    """Collapse same-labelled cards into one, preserving first-seen order.

    A logical system channel is registered as several per-runtime descriptors
    that share a display name; in a flat picker that reads as duplicate cards.
    Merge them into one whose models are the union (deduped by model_id, runtimes
    unioned) — each ``ModelOption`` keeps its own ``provider_id`` so a pick still
    routes to the descriptor that owns it. The card's own ``provider_id`` / status
    come from the first member (a display anchor only)."""
    merged: dict[str, ModelOptionProvider] = {}
    order: list[str] = []
    for card in cards:
        existing = merged.get(card.label)
        if existing is None:
            merged[card.label] = card.model_copy(deep=True)
            order.append(card.label)
            continue
        seen = {m.model_id for m in existing.models}
        for m in card.models:
            if m.model_id not in seen:
                existing.models.append(m)
                seen.add(m.model_id)
                continue
            # Same model reachable via another descriptor → union its runtimes.
            for cur in existing.models:
                if cur.model_id == m.model_id:
                    union = set(cur.runtimes) | set(m.runtimes)
                    cur.runtimes = [r for r in _RUNTIME_PRIORITY if r in union]
                    break
    return [merged[label] for label in order]


def build_model_options(
    providers: list[ProviderOptionInput],
    current: CurrentDefault,
) -> ModelOptionsResponse:
    """Pure builder: providers (+ per-model protocols) → grouped, resolved options.

    Drops models with no runnable runtime and providers left with no models.
    Same-named system channels collapse to one card (Settings disambiguates them
    by runtime; a flat picker can't), and labels colliding inside a card are
    disambiguated last — after the merge.
    """
    by_group: dict[str, list[ModelOptionProvider]] = {}
    for p in providers:
        card = _build_raw_provider(p, current)
        if card is not None:
            by_group.setdefault(_group_key(p.source, p.auth_type), []).append(card)

    # Merge only the system group: same-named cards there are a deliberate
    # "one logical channel, one descriptor per runtime" signal. Leave user-named
    # groups (api_key / subscription) alone so two coincidentally same-named user
    # keys stay distinct.
    if "system" in by_group:
        by_group["system"] = _merge_same_name(by_group["system"])

    # No label disambiguation: each picker view filters by runtime (onboarding
    # prefers claude_agent; Settings has a runtime selector), so two same-named
    # models — a Claude variant + a Codex variant of one logical model — never
    # appear together in one view. Raw labels keep the picker clean.
    groups = [
        ModelOptionGroup(key=key, providers=by_group[key])
        for key in _GROUP_ORDER
        if key in by_group
    ]
    return ModelOptionsResponse(current=current, groups=groups)


__all__ = [
    "CurrentDefault",
    "ModelOption",
    "ModelOptionGroup",
    "ModelOptionProvider",
    "ModelOptionsResponse",
    "ProviderOptionInput",
    "build_model_options",
]
