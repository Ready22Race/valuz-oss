"""Direct-source marketplace fallback — SkillHub + ModelScope + the built-in
agent-template/team-pack resources, queried straight from this process when
the market index is unreachable.

This is the pre-market-index normalization logic (see commit ``1280e99f``,
the last revision before the market index switch), kept alive as a
degraded-mode fallback rather than removed outright. It is used ONLY by
:mod:`valuz_agent.modules.marketplace.service` and ONLY when:

- the market index raised :class:`~.market_index.MarketIndexUnavailableError`
  for a read, AND
- ``Settings.marketplace_direct_fallback`` is true (OSS default; a
  commercial/vertical build that must not let clients bypass the index's
  channel controls sets this false).

Every result produced here is non-channel content, so the caller always
marks it ``degraded: true`` — even a *successful* fallback read is a
degraded experience relative to the curated index catalog. Unlike the
SkillHub/ModelScope reads, the agent-template/team-pack functions here hit no
network at all — they read the same local resource file
(``resources/marketplace/agent_templates.json``) and built-in pack manifests
(``resources/agent_packs/*/manifest.json``) the pre-index marketplace always
shipped with the client.

Item ids keep their pre-index namespaces so this stays visually distinct
from ``market:*`` index items: ``skillhub:skill:{slug}`` /
``modelscope:connector:{encoded_server_id}`` / ``valuz:agent:{id}`` /
``valuz:team:{pack_id}``.
"""

from __future__ import annotations

import asyncio
import base64
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from valuz_agent.i18n import get_locale
from valuz_agent.modules.agent_packs.errors import PackNotFound
from valuz_agent.modules.marketplace.errors import (
    MarketplaceItemNotFound,
    MarketplaceUpstreamError,
)
from valuz_agent.modules.marketplace.models import (
    MarketplaceCategory,
    MarketplaceCategoryList,
    MarketplaceConnectorConfig,
    MarketplaceConnectorConfigField,
    MarketplaceConnectorRequirement,
    MarketplaceEvaluationDimension,
    MarketplaceEvaluationReport,
    MarketplaceFileEntry,
    MarketplaceItem,
    MarketplaceItemDetail,
    MarketplaceItemList,
    MarketplaceSecurityProviderReport,
    MarketplaceSecurityReport,
    MarketplaceStats,
    MarketplaceTeamMember,
)
from valuz_agent.modules.marketplace.modelscope import ModelScopeClient, ModelScopeUnavailableError
from valuz_agent.modules.marketplace.skillhub import SkillHubClient, SkillHubUnavailableError
from valuz_agent.modules.marketplace.templates import (
    AgentTemplateDef,
    load_agent_templates,
    resolve_text,
)

if TYPE_CHECKING:
    from valuz_agent.modules.agent_packs.service import AgentPackService

# Product decision (2026-07-08): browsing shows ONLY SkillHub's official
# curated shelf (``推荐精选``, ~100 skills, via /api/v1/showcase/recommended);
# the category rail derives from what that shelf actually contains. The full
# 75k catalog is reachable only through search, and searching "all" is still
# scoped to this category allowlist so junk verticals stay out.
CURATED_SKILL_CATEGORIES: tuple[str, ...] = (
    "office-efficiency",
    "content-creation",
    "dev-programming",
    "data-analysis",
    "ai-agent",
    "knowledge-management",
    "business-ops",
    "professional",
)

# ModelScope exposes category filters but no category-list endpoint. These are
# the common, populated MCP categories used by its public catalog. Labels are
# Valuz-owned presentation text; the keys are passed through unchanged.
MODELSCOPE_MCP_CATEGORIES: tuple[tuple[str, str, str], ...] = (
    ("search", "搜索", "Search"),
    ("browser-automation", "浏览器自动化", "Browser automation"),
    ("developer-tools", "开发工具", "Developer tools"),
    ("file-systems", "文件管理", "File systems"),
    ("knowledge-and-memory", "知识与记忆", "Knowledge & memory"),
    ("research-and-data", "研究与数据", "Research & data"),
    ("databases", "数据库", "Databases"),
    ("finance", "金融", "Finance"),
    ("entertainment-and-media", "内容与媒体", "Content & media"),
    ("location-services", "地图与位置", "Location services"),
    ("communication", "沟通协作", "Communication"),
)

MODELSCOPE_MCP_CATEGORY_ALIASES: dict[str, tuple[str, str]] = {
    "travel-and-transportation": ("地图与出行", "Travel & transportation"),
    "art-and-culture": ("生活与文化", "Art & culture"),
    "version-control": ("开发工具", "Developer tools"),
    "image-and-video-processing": ("内容与媒体", "Content & media"),
    "AIGC": ("内容与媒体", "Content & media"),
    "aigc": ("内容与媒体", "Content & media"),
    "calendar-management": ("日历管理", "Calendar management"),
    "note-taking": ("笔记", "Note taking"),
    "cloud-platforms": ("云服务", "Cloud platforms"),
    "os-automation": ("系统自动化", "OS automation"),
    "monitoring": ("监控", "Monitoring"),
    "content-management-systems": ("内容管理", "Content management"),
    "other": ("其他", "Other"),
}


def _is_zh(locale: str) -> bool:
    return locale.lower().startswith("zh")


# ---------------------------------------------------------------------------
# Skills (SkillHub)
# ---------------------------------------------------------------------------


async def _category_labels(hub: SkillHubClient) -> dict[str, str]:
    zh = _is_zh(get_locale())
    labels: dict[str, str] = {}
    for c in await hub.categories():
        key = c.get("key")
        if not key:
            continue
        labels[key] = (c.get("name") if zh else c.get("nameEn")) or c.get("name") or key
    return labels


async def skill_categories(hub: SkillHubClient) -> MarketplaceCategoryList:
    """Rail derived from the official curated shelf — only categories the
    shelf actually contains, with the real (small) per-category counts.
    Allowlist categories keep their canonical order; extras the curators
    picked (e.g. design-media) follow, largest first."""
    try:
        labels = await _category_labels(hub)
        showcase = await hub.recommended_skills()
    except SkillHubUnavailableError:
        return MarketplaceCategoryList(categories=[], degraded=True)
    counts: dict[str, int] = {}
    for s in showcase:
        key = s.get("category")
        if key:
            counts[key] = counts.get(key, 0) + 1
    ordered = [k for k in CURATED_SKILL_CATEGORIES if k in counts]
    ordered += sorted(
        (k for k in counts if k not in CURATED_SKILL_CATEGORIES),
        key=lambda k: -counts[k],
    )
    categories = [
        MarketplaceCategory(key=k, label=labels.get(k, k), count=counts[k]) for k in ordered
    ]
    return MarketplaceCategoryList(categories=categories, degraded=False)


def _skillhub_item(
    raw: dict[str, Any],
    installed_slugs: set[str],
    cat_labels: dict[str, str],
) -> MarketplaceItem:
    zh = _is_zh(get_locale())
    slug = str(raw.get("slug") or "")
    description = (
        (raw.get("description_zh") if zh else raw.get("description"))
        or (raw.get("summary_zh") if zh else raw.get("summary"))
        or raw.get("description")
        or raw.get("description_zh")
        or raw.get("summary")
        or raw.get("summary_zh")
        or ""
    )
    labels = raw.get("labels") or {}
    badges: list[str] = []
    if str(labels.get("requires_api_key")).lower() == "true":
        badges.append("requires_api_key")
    if raw.get("verified"):
        badges.append("verified")
    if raw.get("source") == "community":
        badges.append("community")
    category = raw.get("category")
    # Display names, not keys — the upstream API can't filter by
    # subcategory anyway, so chips filter client-side on these values.
    subcategories = [
        s.get("name") or s.get("key", "")
        for s in (raw.get("subCategories") or [])
        if isinstance(s, dict)
    ]
    return MarketplaceItem(
        id=f"skillhub:skill:{slug}",
        type="skill",
        source="skillhub",
        source_ref=slug,
        title=str(raw.get("name") or slug),
        description=str(description),
        icon=raw.get("iconUrl") or None,
        category=category,
        category_label=cat_labels.get(category) if category else None,
        subcategories=[s for s in subcategories if s],
        badges=badges,  # type: ignore[arg-type]
        stats=MarketplaceStats(
            downloads=raw.get("downloads"),
            stars=raw.get("stars"),
            installs=raw.get("installs"),
        ),
        version=raw.get("version") or None,
        install_target="skill_library",
        installed=slug in installed_slugs,
    )


async def list_skills(
    hub: SkillHubClient,
    *,
    category: str | None,
    subcategory: str | None,
    q: str | None,
    page: int,
    page_size: int,
    installed_slugs: set[str],
) -> MarketplaceItemList:
    """SkillHub-only browse/search, paged in memory for browse and via the
    upstream ``page``/``pageSize`` for search."""
    items: list[MarketplaceItem] = []
    total = 0
    degraded = False
    browsing = not (q or "").strip()
    try:
        cat_labels = await _category_labels(hub)
        if browsing:
            # Browse = SkillHub's official curated shelf, paged in memory.
            raw_skills = await hub.recommended_skills()
            if category is not None:
                raw_skills = [s for s in raw_skills if s.get("category") == category]
            total = len(raw_skills)
            start = (page - 1) * page_size
            raw_skills = raw_skills[start : start + page_size]
        else:
            # Search = the full catalog, scoped to the allowlist when no
            # category is chosen (junk verticals stay out of "all").
            raw_skills, total = await hub.list_skills(
                page=page, page_size=page_size, category=category, keyword=q
            )
            if category is None:
                raw_skills = [
                    s for s in raw_skills if s.get("category") in CURATED_SKILL_CATEGORIES
                ]
        items = [_skillhub_item(s, installed_slugs, cat_labels) for s in raw_skills]
    except SkillHubUnavailableError:
        degraded = True

    if subcategory:
        items = [i for i in items if subcategory in i.subcategories]
    return MarketplaceItemList(
        items=items, total=total, page=page, page_size=page_size, degraded=degraded
    )


async def skill_detail(
    hub: SkillHubClient, slug: str, installed_slugs: set[str]
) -> MarketplaceItemDetail:
    try:
        payload = await hub.skill_detail(slug)
        files = await hub.skill_files(slug)
        cat_labels = await _category_labels(hub)
    except SkillHubUnavailableError as exc:
        raise MarketplaceUpstreamError(str(exc)) from exc
    try:
        evaluation_payload: dict[str, Any] | None = await hub.skill_evaluation(slug)
    except SkillHubUnavailableError:
        evaluation_payload = None
    skill = payload.get("skill") or {}
    if not skill.get("slug"):
        raise MarketplaceItemNotFound(f"Unknown SkillHub skill: {slug}")
    # The detail payload names fields slightly differently from the list
    # (displayName vs name) — realign before normalizing.
    raw = {**skill, "name": skill.get("displayName") or skill.get("name") or slug}
    stats = skill.get("stats") or {}
    raw.setdefault("downloads", stats.get("downloads"))
    raw.setdefault("stars", stats.get("stars"))
    raw.setdefault("installs", stats.get("installs"))
    latest = payload.get("latestVersion") or {}
    raw.setdefault("version", latest.get("version"))
    base = _skillhub_item(raw, installed_slugs, cat_labels)

    owner = (payload.get("owner") or {}).get("displayName") or skill.get("ownerName")
    security = _normalize_security(payload.get("securityReports"))
    evaluation = _normalize_evaluation(evaluation_payload)
    if security is not None and security.status == "benign":
        base.badges.append("reviewed_skillhub")
    updated_ms = skill.get("updated_at") or skill.get("updatedAt")
    updated_at = None
    if isinstance(updated_ms, (int, float)) and updated_ms > 0:
        updated_at = datetime.fromtimestamp(updated_ms / 1000, tz=UTC).date().isoformat()
    return MarketplaceItemDetail(
        **base.model_dump(),
        owner=owner,
        origin_url=skill.get("sourceUrl") or skill.get("homepage"),
        updated_at=updated_at,
        files=[
            MarketplaceFileEntry(path=f.get("path", ""), size=f.get("size"), sha256=f.get("sha256"))
            for f in files
            if f.get("path")
        ],
        security=security,
        evaluation=evaluation,
    )


def _normalize_security(reports: Any) -> MarketplaceSecurityReport | None:
    if not isinstance(reports, dict) or not reports:
        return None
    entries: list[MarketplaceSecurityProviderReport] = []
    statuses: list[str] = []
    summaries: list[str] = []
    for provider, entry in reports.items():
        if not isinstance(entry, dict):
            continue
        status = str(entry.get("status") or "unknown")
        statuses.append(status)
        text = entry.get("statusText")
        if text:
            summaries.append(str(text))
        entries.append(
            MarketplaceSecurityProviderReport(
                provider=str(provider), status=status, url=entry.get("reportUrl")
            )
        )
    if not entries:
        return None
    if all(s == "benign" for s in statuses):
        overall = "benign"
    elif any(s not in ("benign", "unknown") for s in statuses):
        overall = "flagged"
    else:
        overall = "unknown"
    return MarketplaceSecurityReport(
        status=overall,  # type: ignore[arg-type]
        summary="; ".join(dict.fromkeys(summaries)),
        reports=entries,
    )


def _coerce_score(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return round(float(value), 1)
    try:
        return round(float(value), 1) if value is not None else None
    except (TypeError, ValueError):
        return None


def _evaluation_rating(score: float | None) -> str | None:
    if score is None:
        return None
    if score >= 4.5:
        return "优秀"
    if score >= 4.0:
        return "良好"
    if score >= 3.0:
        return "中上"
    if score >= 2.0:
        return "一般"
    return "待改进"


def _normalize_evaluation(payload: Any) -> MarketplaceEvaluationReport | None:
    if not isinstance(payload, dict):
        return None
    dimensions = payload.get("dimensions")
    if not isinstance(dimensions, dict):
        return None

    dimension_meta: tuple[tuple[str, str, str], ...] = (
        ("trust", "T", "可信任度"),
        ("reliability", "R", "可靠性"),
        ("adaptability", "A", "适用性"),
        ("convention", "C", "规范性"),
        ("effectiveness", "E", "有效性"),
    )
    normalized: list[MarketplaceEvaluationDimension] = []
    scores: list[float] = []
    for key, code, label in dimension_meta:
        entry = dimensions.get(key)
        if not isinstance(entry, dict):
            continue
        raw_score = _coerce_score(entry.get("score"))
        if raw_score is None:
            items = entry.get("items")
            item_scores = [
                score
                for item in (items.values() if isinstance(items, dict) else [])
                if isinstance(item, dict)
                for score in [_coerce_score(item.get("score"))]
                if score is not None
            ]
            raw_score = round(sum(item_scores) / len(item_scores), 1) if item_scores else None
        if raw_score is not None:
            scores.append(raw_score)
        summary = entry.get("userReason") or entry.get("reason")
        normalized.append(
            MarketplaceEvaluationDimension(
                key=key,  # type: ignore[arg-type]
                code=code,  # type: ignore[arg-type]
                label=label,
                score=raw_score,
                summary=str(summary) if summary else None,
            )
        )

    score = round(sum(scores) / len(scores), 1) if scores else None
    rating = _evaluation_rating(score)
    summary = payload.get("userSummary") or payload.get("summary")
    return MarketplaceEvaluationReport(
        score=score,
        rating=rating,
        summary=str(summary) if summary else None,
        dimensions=normalized,
    )


# ---------------------------------------------------------------------------
# Connectors (ModelScope)
# ---------------------------------------------------------------------------


def connector_categories() -> MarketplaceCategoryList:
    zh = _is_zh(get_locale())
    return MarketplaceCategoryList(
        categories=[
            MarketplaceCategory(key=key, label=zh_label if zh else en_label)
            for key, zh_label, en_label in MODELSCOPE_MCP_CATEGORIES
        ],
        degraded=False,
    )


def encode_connector_ref(server_id: str) -> str:
    return base64.urlsafe_b64encode(server_id.encode()).decode().rstrip("=")


def decode_connector_ref(ref: str) -> str:
    try:
        return base64.urlsafe_b64decode(ref + "=" * (-len(ref) % 4)).decode()
    except (ValueError, UnicodeDecodeError) as exc:
        raise MarketplaceItemNotFound("Malformed ModelScope item id") from exc


def _modelscope_slug(server_id: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", server_id.lower()).strip("-")
    return f"modelscope-{slug}"[:64].rstrip("-")


def _modelscope_description(raw: dict[str, Any]) -> str:
    locales = raw.get("locales") if isinstance(raw.get("locales"), dict) else {}
    locale_key = "zh" if _is_zh(get_locale()) else "en"
    localized = locales.get(locale_key) if isinstance(locales, dict) else None
    localized = localized if isinstance(localized, dict) else {}
    direct = localized.get("description") or raw.get("description")
    if direct:
        return str(direct).strip()
    readme = localized.get("readme") or raw.get("readme")
    return _readme_summary(str(readme or ""))


def _readme_summary(readme: str) -> str:
    if not readme.strip():
        return ""
    without_code = re.sub(r"```[\s\S]*?```", "", readme.replace("\r", ""))
    for block in re.split(r"\n\s*\n", without_code):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        if all(line.startswith(("#", "![", "[![", "<", "|", "---")) for line in lines):
            continue
        text = " ".join(
            line for line in lines if not line.startswith(("#", "![", "[![", "<", "|", "---"))
        )
        text = re.sub(r"!\[[^]]*]\([^)]*\)", "", text)
        text = re.sub(r"\[([^]]+)]\([^)]*\)", r"\1", text)
        text = re.sub(r"^[>*+\-\d.\s]+", "", text)
        text = re.sub(r"[*_`~]", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) >= 20:
            return f"{text[:237]}..." if len(text) > 240 else text
    return ""


def _modelscope_fallback_description(raw: dict[str, Any]) -> str:
    title = str(raw.get("name") or raw.get("chinese_name") or "MCP")
    publisher = str(raw.get("author") or raw.get("publisher") or "ModelScope")
    publisher = publisher.lstrip("@").split("/", 1)[0] or "ModelScope"
    categories = raw.get("categories") if isinstance(raw.get("categories"), list) else []
    category = str(categories[0]) if categories else ""
    labels = {key: (zh_label, en_label) for key, zh_label, en_label in MODELSCOPE_MCP_CATEGORIES}
    labels.update(MODELSCOPE_MCP_CATEGORY_ALIASES)
    category_label = labels.get(category, (category, category))
    if _is_zh(get_locale()):
        category_text = f"{category_label[0]}类" if category_label[0] else ""
        return (
            f"{title} 是由 {publisher} 提供的{category_text} MCP 服务，"
            "可连接到 Valuz 使用其工具能力。"
        )
    category_text = f" {category_label[1]}" if category_label[1] else ""
    return f"{title} is a{category_text} MCP service published by {publisher} for use in Valuz."


def _modelscope_item(raw: dict[str, Any], installed_slugs: set[str]) -> MarketplaceItem:
    server_id = str(raw.get("id") or "")
    locales = raw.get("locales") if isinstance(raw.get("locales"), dict) else {}
    locale_key = "zh" if _is_zh(get_locale()) else "en"
    localized = locales.get(locale_key) if isinstance(locales, dict) else None
    localized = localized if isinstance(localized, dict) else {}
    title = str(localized.get("name") or raw.get("chinese_name") or raw.get("name") or server_id)
    description = _modelscope_description(raw)
    categories = raw.get("categories") if isinstance(raw.get("categories"), list) else []
    category = str(categories[0]) if categories else None
    category_labels = {
        key: zh_label if _is_zh(get_locale()) else en_label
        for key, zh_label, en_label in MODELSCOPE_MCP_CATEGORIES
    }
    category_labels.update(
        {
            key: labels[0] if _is_zh(get_locale()) else labels[1]
            for key, labels in MODELSCOPE_MCP_CATEGORY_ALIASES.items()
        }
    )
    slug = _modelscope_slug(server_id)
    badges: list[str] = []
    if raw.get("is_verified"):
        badges.append("verified")
    return MarketplaceItem(
        id=f"modelscope:connector:{encode_connector_ref(server_id)}",
        type="connector",
        source="modelscope",
        source_ref=server_id,
        title=title,
        description=description,
        icon=raw.get("logo_url") or None,
        category=category,
        category_label=category_labels.get(category, category) if category else None,
        subcategories=[str(tag) for tag in (raw.get("tags") or []) if tag],
        badges=badges,  # type: ignore[arg-type]
        stats=MarketplaceStats(
            stars=raw.get("github_stars"),
            views=raw.get("view_count"),
        ),
        install_target="connector_library",
        installed=slug in installed_slugs,
    )


async def _enrich_modelscope_descriptions(
    ms: ModelScopeClient, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Fill summary gaps from detail README without disturbing list order."""

    async def enrich(raw: dict[str, Any]) -> dict[str, Any]:
        if _modelscope_description(raw):
            return raw
        server_id = str(raw.get("id") or "")
        detail: dict[str, Any] = {}
        if server_id:
            try:
                detail = await ms.server_detail_cached(server_id)
            except ModelScopeUnavailableError:
                detail = {}
        description = _modelscope_description(detail)
        if not description:
            description = _modelscope_fallback_description({**raw, **detail})
        return {
            **raw,
            **{
                key: detail[key]
                for key in ("author", "github_stars", "is_verified")
                if key in detail
            },
            "description": description,
        }

    return list(await asyncio.gather(*(enrich(row) for row in rows)))


async def list_connectors(
    ms: ModelScopeClient,
    *,
    category: str | None,
    q: str | None,
    page: int,
    page_size: int,
    installed_slugs: set[str],
) -> MarketplaceItemList:
    try:
        rows, upstream_total = await ms.list_servers(
            category=category,
            search=(q or "").strip() or None,
            is_hosted=True,
            page=page,
            page_size=page_size,
        )
    except ModelScopeUnavailableError:
        return MarketplaceItemList(items=[], total=0, page=page, page_size=page_size, degraded=True)

    rows = await _enrich_modelscope_descriptions(ms, rows)
    items = [_modelscope_item(row, installed_slugs) for row in rows if row.get("id")]
    return MarketplaceItemList(
        items=items,
        total=min(upstream_total, 100),
        page=page,
        page_size=page_size,
        degraded=False,
    )


def _modelscope_connector_config(raw: dict[str, Any]) -> MarketplaceConnectorConfig:
    server_id = str(raw.get("id") or "modelscope-mcp")
    slug = _modelscope_slug(server_id)
    configs: list[dict[str, Any]] = []
    for wrapper in raw.get("server_config") or []:
        if not isinstance(wrapper, dict):
            continue
        servers = wrapper.get("mcpServers")
        if not isinstance(servers, dict):
            continue
        configs.extend(config for config in servers.values() if isinstance(config, dict))

    # Prefer a local package config. ModelScope validates npx/uvx packages;
    # other commands, local paths and shell wrappers are intentionally rejected.
    for config in configs:
        command = str(config.get("command") or "")
        if command not in {"npx", "uvx"}:
            continue
        args = config.get("args")
        if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
            continue
        env, fields = _normalize_modelscope_env(raw, config.get("env"))
        return MarketplaceConnectorConfig(
            slug=slug,
            transport="stdio",
            command=command,
            args=args,
            env=env,
            fields=fields,
        )

    for config in configs:
        raw_transport = str(config.get("type") or config.get("transport") or "").lower()
        if raw_transport not in {"http", "streamable_http", "streamable-http", "sse"}:
            continue
        url = str(config.get("url") or "")
        if not url.startswith("https://"):
            continue
        transport: Literal["http", "sse"] = "sse" if raw_transport == "sse" else "http"
        headers, fields = _normalize_modelscope_named_values(config.get("headers"), target="header")
        params, param_fields = _normalize_modelscope_named_values(
            config.get("params"), target="param"
        )
        fields.extend(param_fields)
        return MarketplaceConnectorConfig(
            slug=slug,
            transport=transport,
            url=url,
            headers=headers,
            params=params,
            auth_type=(
                "bearer" if any(f.name.lower() == "authorization" for f in fields) else "none"
            ),
            fields=fields,
        )

    reason = (
        "该 MCP 需要先通过 ModelScope 部署，暂时不能直接添加到 Valuz。"
        if _is_zh(get_locale())
        else "This MCP must be deployed through ModelScope before Valuz can connect to it."
    )
    return MarketplaceConnectorConfig(
        slug=slug,
        transport="stdio",
        supported=False,
        unsupported_reason=reason,
    )


def _is_secret_name(name: str) -> bool:
    return bool(re.search(r"(api[_-]?key|token|secret|password|credential)", name, re.I))


def _is_placeholder(value: Any) -> bool:
    if value is None or value == "":
        return True
    if not isinstance(value, str):
        return False
    return bool(re.search(r"(<[^>]+>|\$\{[^}]+\}|YOUR[_-]|API[_-]?KEY|TOKEN|SECRET)", value, re.I))


def _normalize_modelscope_env(
    raw: dict[str, Any], config_env: Any
) -> tuple[dict[str, str], list[MarketplaceConnectorConfigField]]:
    schema_raw = raw.get("env_schema")
    schema: dict[str, Any] = schema_raw if isinstance(schema_raw, dict) else {}
    properties_raw = schema.get("properties")
    properties: dict[str, Any] = properties_raw if isinstance(properties_raw, dict) else {}
    required = set(schema.get("required") or [])
    values = config_env if isinstance(config_env, dict) else {}
    keys = list(dict.fromkeys([*properties.keys(), *values.keys()]))
    fixed: dict[str, str] = {}
    fields: list[MarketplaceConnectorConfigField] = []
    for key in keys:
        value = values.get(key)
        prop_raw = properties.get(key)
        prop: dict[str, Any] = prop_raw if isinstance(prop_raw, dict) else {}
        if _is_placeholder(value) or key in properties:
            fields.append(
                MarketplaceConnectorConfigField(
                    key=f"env:{key}",
                    name=str(key),
                    target="env",
                    label=str(prop.get("description") or key),
                    required=key in required,
                    secret=_is_secret_name(str(key)),
                    placeholder=str(prop.get("placeholder") or "") or None,
                )
            )
        elif isinstance(value, str):
            fixed[str(key)] = value
    return fixed, fields


def _normalize_modelscope_named_values(
    raw_values: Any, *, target: str
) -> tuple[dict[str, str], list[MarketplaceConnectorConfigField]]:
    values = raw_values if isinstance(raw_values, dict) else {}
    fixed: dict[str, str] = {}
    fields: list[MarketplaceConnectorConfigField] = []
    for key, value in values.items():
        if _is_placeholder(value):
            text = str(value or "")
            prefix = "Bearer " if text.lower().startswith("bearer ") else None
            fields.append(
                MarketplaceConnectorConfigField(
                    key=f"{target}:{key}",
                    name=str(key),
                    target=target,  # type: ignore[arg-type]
                    label=str(key),
                    required=True,
                    secret=_is_secret_name(str(key)) or str(key).lower() == "authorization",
                    prefix=prefix,
                )
            )
        elif isinstance(value, str):
            fixed[str(key)] = value
    return fixed, fields


async def connector_detail(
    ms: ModelScopeClient, server_id: str, installed_slugs: set[str]
) -> MarketplaceItemDetail:
    """Detail for a decoded ModelScope ``server_id``. ``supported=False``
    connectors are still returned (marked ``locked``) — the fallback catalog
    does not filter them out, matching the pre-index behavior."""
    try:
        raw = await ms.server_detail(server_id)
    except ModelScopeUnavailableError as exc:
        raise MarketplaceUpstreamError(str(exc)) from exc
    base = _modelscope_item(raw, installed_slugs)
    config = _modelscope_connector_config(raw)
    if not config.supported:
        base.locked = True
        base.badges.append("locked")
    return MarketplaceItemDetail(
        **base.model_dump(),
        owner=str(raw.get("author") or raw.get("owner") or "ModelScope"),
        origin_url=f"https://modelscope.cn/mcp/servers/{server_id}",
        instructions=str(raw.get("readme") or "") or None,
        connector_config=config,
    )


# ---------------------------------------------------------------------------
# Agent templates — built-in, local resource file, no network
# ---------------------------------------------------------------------------


def agent_categories(packs: list[dict[str, Any]]) -> MarketplaceCategoryList:
    """Category rail for the ``agent`` kind. Matches the pre-index behavior
    exactly: this rail is derived from built-in TEAM pack scenarios — a
    single-agent template's own ``category`` field was never exposed through
    it, even before the market index switch (``list_agent_templates`` below
    filters by that field directly instead)."""
    counts: dict[str, int] = {}
    order: list[str] = []
    for pack in packs:
        key = pack.get("scenario") or None
        if not key:
            continue
        if key not in counts:
            order.append(key)
        counts[key] = counts.get(key, 0) + 1
    categories = [MarketplaceCategory(key=k, label=k, count=counts[k]) for k in order]
    return MarketplaceCategoryList(categories=categories, degraded=False)


def _agent_template_item(tpl: AgentTemplateDef, library_slugs: set[str]) -> MarketplaceItem:
    badges: list[str] = ["reviewed_valuz"]
    if any(c.requirement == "api_key" for c in tpl.connectors):
        badges.append("requires_api_key")
    if any(c.requirement == "cost" for c in tpl.connectors):
        badges.append("third_party_cost")
    return MarketplaceItem(
        id=f"valuz:agent:{tpl.id}",
        type="agent_template",
        source=tpl.source,
        source_ref=tpl.id,
        title=resolve_text(tpl.name),
        subtitle=resolve_text(tpl.role) or None,
        description=resolve_text(tpl.role),
        icon=tpl.icon,
        category=tpl.category,
        category_label=resolve_text(tpl.category_label) or None,
        badges=badges,  # type: ignore[arg-type]
        runtime=tpl.runtime,
        skill_count=len(tpl.skills),
        install_target="agent_library",
        installed=tpl.slug in library_slugs,
    )


def list_agent_templates(
    *,
    category: str | None,
    source: str | None,
    q: str | None,
    library_slugs: set[str],
) -> MarketplaceItemList:
    needle = (q or "").strip().lower()
    items: list[MarketplaceItem] = []
    for tpl in load_agent_templates():
        if category is not None and tpl.category != category:
            continue
        if source is not None and tpl.source != source:
            continue
        if needle:
            haystack = " ".join(
                [resolve_text(tpl.name), resolve_text(tpl.role), resolve_text(tpl.instructions)]
            ).lower()
            if needle not in haystack:
                continue
        items.append(_agent_template_item(tpl, library_slugs))
    return MarketplaceItemList(
        items=items, total=len(items), page=1, page_size=max(len(items), 1), degraded=False
    )


def agent_template_detail(template_id: str, library_slugs: set[str]) -> MarketplaceItemDetail:
    tpl = next((t for t in load_agent_templates() if t.id == template_id), None)
    if tpl is None:
        raise MarketplaceItemNotFound(f"Unknown agent template: {template_id}")
    base = _agent_template_item(tpl, library_slugs)
    return MarketplaceItemDetail(
        **base.model_dump(),
        owner="Valuz",
        instructions=resolve_text(tpl.instructions),
        bound_skills=[resolve_text(s) for s in tpl.skills],
        connectors=[
            MarketplaceConnectorRequirement(name=resolve_text(c.name), requirement=c.requirement)
            for c in tpl.connectors
        ],
    )


# ---------------------------------------------------------------------------
# Team templates — built-in agent packs, no network
#
# ``AgentPackService.list_packs``/``get_pack`` already annotate each pack's
# ``added``/``in_library`` state against THIS user's agent library, so —
# unlike skill/connector above — these take the pack service + user_id
# directly rather than a separately-computed ``installed_slugs`` set.
# ---------------------------------------------------------------------------


def _team_item(pack: dict[str, Any]) -> MarketplaceItem:
    roles = pack.get("roles") or []
    members = [
        MarketplaceTeamMember(
            slug=r.get("slug"),
            name=r.get("name", ""),
            role=r.get("description", ""),
            lead=i == 0,  # loader contract: first role reads as the lead
            skill_count=len(r.get("skills") or []),
        )
        for i, r in enumerate(roles)
    ]
    skill_slugs = {s for r in roles for s in (r.get("skills") or [])}
    return MarketplaceItem(
        id=f"valuz:team:{pack['id']}",
        type="agent_team_template",
        source="valuz_official",
        source_ref=pack["id"],
        title=pack.get("name", pack["id"]),
        description=pack.get("description", ""),
        icon=pack.get("icon") or None,
        category=pack.get("scenario") or None,
        category_label=pack.get("scenario") or None,
        badges=["reviewed_valuz"],
        skill_count=len(skill_slugs),
        members=members,
        install_target="agent_library",
        installed=bool(pack.get("added")),
    )


async def list_team_templates(
    packs: AgentPackService, user_id: str, *, category: str | None, q: str | None
) -> MarketplaceItemList:
    needle = (q or "").strip().lower()
    items: list[MarketplaceItem] = []
    for pack in await packs.list_packs(user_id):
        item = _team_item(pack)
        if category is not None and item.category != category:
            continue
        if needle and needle not in f"{item.title} {item.description}".lower():
            continue
        items.append(item)
    return MarketplaceItemList(
        items=items, total=len(items), page=1, page_size=max(len(items), 1), degraded=False
    )


async def team_detail(packs: AgentPackService, user_id: str, pack_id: str) -> MarketplaceItemDetail:
    try:
        pack = await packs.get_pack(user_id, pack_id)
    except PackNotFound as exc:
        raise MarketplaceItemNotFound(f"Unknown team template: {pack_id}") from exc
    base = _team_item(pack)
    roles = pack.get("roles") or []
    skill_slugs = sorted({s for r in roles for s in (r.get("skills") or [])})
    connector_slugs = sorted({c for r in roles for c in (r.get("connector_types") or [])})
    return MarketplaceItemDetail(
        **base.model_dump(),
        owner="Valuz",
        instructions=_team_collaboration_summary(pack, roles),
        workflow=_team_workflow_steps(roles),
        deliverables=_team_deliverables(pack),
        usage_notes=_team_usage_notes(pack),
        bound_skills=skill_slugs,
        connectors=[
            MarketplaceConnectorRequirement(name=c, requirement="required") for c in connector_slugs
        ],
    )


def _team_collaboration_summary(pack: dict[str, Any], roles: list[dict[str, Any]]) -> str:
    if not roles:
        return str(pack.get("description") or "")
    lead = roles[0].get("name") or "Lead"
    member_names = [str(r.get("name") or "") for r in roles[1:] if r.get("name")]
    if member_names:
        return (
            f"{lead} 负责理解目标、拆解任务和汇总成果；"
            f"{'、'.join(member_names)} 按各自职责并行处理材料、分析和产出，"
            "最后由 Lead 整合为一份可交付结果。"
        )
    return f"{lead} 负责从需求澄清到成果整理的完整工作流。"


def _team_workflow_steps(roles: list[dict[str, Any]]) -> list[str]:
    steps: list[str] = []
    for index, role in enumerate(roles, start=1):
        name = role.get("name") or f"Agent {index}"
        responsibility = role.get("description") or "完成对应阶段任务"
        steps.append(f"{name}：{responsibility}")
    if steps:
        steps.append("汇总交付：整合各成员结果，形成可复用的最终成果包")
    return steps


def _team_deliverables(pack: dict[str, Any]) -> list[str]:
    pack_id = str(pack.get("id") or "")
    category = str(pack.get("scenario") or "")
    by_pack: dict[str, list[str]] = {
        "product-strategy": [
            "产品定位与目标用户说明",
            "需求清单 / PRD 草稿",
            "竞品分析摘要",
            "MVP 路线图",
        ],
        "development-engineering": [
            "实现方案",
            "代码改动建议",
            "Code Review 清单",
            "缺陷修复与验证记录",
        ],
        "investment": [
            "行业研究纪要",
            "财务模型 / 估值表",
            "业绩跟踪摘要",
            "投研报告或路演材料草稿",
        ],
        "competitive-intelligence": [
            "竞品矩阵",
            "市场与用户洞察",
            "差异化机会判断",
            "行动建议报告",
        ],
        "video-production": ["视频脚本", "分镜表", "镜头/生成提示词", "剪辑与交付规格"],
        "contract-review": [
            "合同条款摘要",
            "风险问题清单",
            "缺失条款检查",
            "修改建议与谈判口径",
        ],
        "academic-research": [
            "论文检索清单",
            "文献综述",
            "论文结构 / 初稿",
            "引用与规范检查建议",
        ],
        "recruiting-evaluation": [
            "候选人结构化档案",
            "岗位匹配评分",
            "面试题与评分表",
            "候选人对比与建议",
        ],
        "chinese-metaphysics": [
            "排盘与问题整理",
            "文化娱乐向解读",
            "主题建议",
            "边界与免责声明",
        ],
    }
    if pack_id in by_pack:
        return by_pack[pack_id]
    by_category: dict[str, list[str]] = {
        "产品设计": ["需求分析", "方案草稿", "评审清单", "下一步计划"],
        "技术工程": ["技术方案", "实现/测试建议", "风险清单", "验收记录"],
        "金融投资": ["研究纪要", "数据/模型摘要", "风险提示", "报告草稿"],
        "营销增长": ["调研分析", "策略建议", "内容/活动方案", "复盘指标"],
        "内容创作": ["脚本/文案", "素材规划", "发布建议", "复盘建议"],
        "法务安全": ["审查摘要", "风险清单", "修改建议", "复核提醒"],
        "教育学术": ["资料清单", "分析/综述", "课程或论文草稿", "规范检查"],
        "运营人力": ["结构化记录", "评分/排序", "沟通问题", "建议报告"],
        "特色分类": ["主题解读", "参考建议", "内容化输出", "边界说明"],
    }
    return by_category.get(category, ["阶段性分析结果", "成员输出汇总", "最终交付文档"])


def _team_usage_notes(pack: dict[str, Any]) -> list[str]:
    category = str(pack.get("scenario") or "")
    notes: list[str] = []
    if category in {"金融投资", "法务安全"}:
        notes.append("高敏感领域输出仅作为草稿和辅助分析，需由专业人士复核。")
    if category == "特色分类":
        notes.append("该分类按文化/娱乐内容定位，不应作为医疗、法律、金融或重大人生决策依据。")
    return notes
