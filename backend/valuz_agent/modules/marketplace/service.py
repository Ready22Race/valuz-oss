"""MarketplaceService — normalizes every supply source into one item shape.

Sources → items:

- SkillHub skills (``skillhub:skill:{slug}``) — remote catalog, restricted to
  the curated category allowlist; installs go through the existing skill
  URL-import pipeline (caps + provenance). Valuz official skills are NOT
  market items — they ship with the client (or install alongside official
  teams).
- Curated single-agent templates (``valuz:agent:{id}``) — local resource
  file; install creates a library agent.
- Built-in team packs (``valuz:team:{id}``) — the agent-pack module; install
  first downloads any SkillHub skill dependencies declared by the pack, then
  delegates to ``AgentPackService.import_pack``.

SkillHub outages must never blank the marketplace: list/category reads
degrade to empty results with ``degraded: true`` instead of failing.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from valuz_agent.i18n import get_locale
from valuz_agent.modules.agent_packs.service import AgentPackService
from valuz_agent.modules.agents.service import AgentService, MemberAlreadyExistsError
from valuz_agent.modules.marketplace.errors import (
    MarketplaceItemNotFound,
    MarketplaceUpstreamError,
)
from valuz_agent.modules.marketplace.models import (
    MarketplaceCategory,
    MarketplaceCategoryList,
    MarketplaceConnectorRequirement,
    MarketplaceEvaluationDimension,
    MarketplaceEvaluationReport,
    MarketplaceFileEntry,
    MarketplaceInstallResult,
    MarketplaceItem,
    MarketplaceItemDetail,
    MarketplaceItemList,
    MarketplaceSecurityProviderReport,
    MarketplaceSecurityReport,
    MarketplaceStats,
    MarketplaceTeamMember,
)
from valuz_agent.modules.marketplace.skillhub import SkillHubClient, SkillHubUnavailableError
from valuz_agent.modules.marketplace.templates import (
    AgentTemplateDef,
    load_agent_templates,
    resolve_text,
)
from valuz_agent.modules.skills.models import SkillImportUrlConfirmRequest
from valuz_agent.modules.skills.service import SkillLibraryService

logger = logging.getLogger(__name__)

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


def _is_zh(locale: str) -> bool:
    return locale.lower().startswith("zh")


class MarketplaceService:
    def __init__(
        self,
        *,
        skillhub: SkillHubClient,
        skill_service: SkillLibraryService,
        agent_service: AgentService,
        pack_service: AgentPackService,
    ) -> None:
        self._hub = skillhub
        self._skills = skill_service
        self._agents = agent_service
        self._packs = pack_service

    # ------------------------------------------------------------------
    # Categories
    # ------------------------------------------------------------------

    async def list_categories(self, user_id: str, kind: str) -> MarketplaceCategoryList:
        if kind == "agent":
            return await self._agent_categories(user_id)
        return await self._skill_categories()

    async def _agent_categories(self, user_id: str) -> MarketplaceCategoryList:
        counts: dict[str, int] = {}
        labels: dict[str, str] = {}
        order: list[str] = []

        def add(key: str | None, label: str | None = None) -> None:
            if not key:
                return
            if key not in counts:
                order.append(key)
                labels[key] = label or key
            counts[key] = counts.get(key, 0) + 1

        for pack in await self._packs.list_packs(user_id):
            add(pack.get("scenario") or None)

        categories = [
            MarketplaceCategory(key=key, label=labels[key], count=counts[key]) for key in order
        ]
        return MarketplaceCategoryList(categories=categories, degraded=False)

    async def _skill_categories(self) -> MarketplaceCategoryList:
        """Rail derived from the official curated shelf — only categories the
        shelf actually contains, with the real (small) per-category counts.
        Allowlist categories keep their canonical order; extras the curators
        picked (e.g. design-media) follow, largest first."""
        try:
            labels = await self._category_labels()
            showcase = await self._hub.recommended_skills()
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

    # ------------------------------------------------------------------
    # Items
    # ------------------------------------------------------------------

    async def list_items(
        self,
        user_id: str,
        *,
        type_: str,
        category: str | None = None,
        subcategory: str | None = None,
        source: str | None = None,
        q: str | None = None,
        page: int = 1,
        page_size: int = 30,
    ) -> MarketplaceItemList:
        if type_ == "agent_template":
            return self._list_agent_templates(
                await self._library_agent_slugs(user_id),
                category=category,
                source=source,
                q=q,
            )
        if type_ == "agent_team_template":
            return await self._list_team_templates(user_id, category=category, q=q)
        return await self._list_skill_items(
            user_id,
            category=category,
            subcategory=subcategory,
            source=source,
            q=q,
            page=page,
            page_size=page_size,
        )

    # -- skills -------------------------------------------------------------

    async def _list_skill_items(
        self,
        user_id: str,
        *,
        category: str | None,
        subcategory: str | None,
        source: str | None,
        q: str | None,
        page: int,
        page_size: int,
    ) -> MarketplaceItemList:
        # The Skills tab is SkillHub-only — Valuz official skills ship with
        # the client (or install alongside official teams) and never appear
        # as market items.
        index_rows = await self._skills.list_indexed_skills(user_id)
        installed_slugs = {row.slug for row in index_rows if self._is_installed_skill_row(row)}

        items: list[MarketplaceItem] = []
        total = 0
        degraded = False
        browsing = not (q or "").strip()
        try:
            cat_labels = await self._category_labels()
            if browsing:
                # Browse = SkillHub's official curated shelf, paged in memory.
                raw_skills = await self._hub.recommended_skills()
                if category is not None:
                    raw_skills = [s for s in raw_skills if s.get("category") == category]
                total = len(raw_skills)
                start = (page - 1) * page_size
                raw_skills = raw_skills[start : start + page_size]
            else:
                # Search = the full catalog, scoped to the allowlist when no
                # category is chosen (junk verticals stay out of "all").
                raw_skills, total = await self._hub.list_skills(
                    page=page,
                    page_size=page_size,
                    category=category,
                    keyword=q,
                )
                if category is None:
                    raw_skills = [
                        s for s in raw_skills if s.get("category") in CURATED_SKILL_CATEGORIES
                    ]
            items = [self._skillhub_item(s, installed_slugs, cat_labels) for s in raw_skills]
        except SkillHubUnavailableError:
            degraded = True

        if subcategory:
            items = [i for i in items if subcategory in i.subcategories]
        return MarketplaceItemList(
            items=items, total=total, page=page, page_size=page_size, degraded=degraded
        )

    async def _category_labels(self) -> dict[str, str]:
        zh = _is_zh(get_locale())
        labels: dict[str, str] = {}
        for c in await self._hub.categories():
            key = c.get("key")
            if not key:
                continue
            labels[key] = (c.get("name") if zh else c.get("nameEn")) or c.get("name") or key
        return labels

    def _skillhub_item(
        self,
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

    # -- agent templates ------------------------------------------------------

    async def _library_agent_slugs(self, user_id: str) -> set[str]:
        return {a.slug for a in await self._agents.list_agents(user_id)}

    def _list_agent_templates(
        self,
        library_slugs: set[str],
        *,
        category: str | None,
        source: str | None,
        q: str | None,
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
            items.append(self._agent_template_item(tpl, library_slugs))
        return MarketplaceItemList(
            items=items, total=len(items), page=1, page_size=max(len(items), 1), degraded=False
        )

    def _agent_template_item(
        self, tpl: AgentTemplateDef, library_slugs: set[str]
    ) -> MarketplaceItem:
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

    # -- team templates -------------------------------------------------------

    async def _list_team_templates(
        self,
        user_id: str,
        *,
        category: str | None,
        q: str | None,
    ) -> MarketplaceItemList:
        needle = (q or "").strip().lower()
        items: list[MarketplaceItem] = []
        for pack in await self._packs.list_packs(user_id):
            item = self._team_item(pack)
            if category is not None and item.category != category:
                continue
            if needle and needle not in f"{item.title} {item.description}".lower():
                continue
            items.append(item)
        return MarketplaceItemList(
            items=items, total=len(items), page=1, page_size=max(len(items), 1), degraded=False
        )

    def _team_item(self, pack: dict[str, Any]) -> MarketplaceItem:
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

    # ------------------------------------------------------------------
    # Detail
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_item_id(item_id: str) -> tuple[str, str, str]:
        parts = item_id.split(":", 2)
        if len(parts) != 3 or not all(parts):
            raise MarketplaceItemNotFound(f"Malformed marketplace item id: {item_id}")
        return parts[0], parts[1], parts[2]

    @staticmethod
    def _is_installed_skill_row(row: Any) -> bool:
        """A stale index row must not make Marketplace show "installed".

        Deleting a skill removes its directory immediately. Older rows may
        still say ``available`` until a rescan, so verify the indexed source
        path exists when present.
        """
        if getattr(row, "status", "available") != "available":
            return False
        source_path = getattr(row, "source_path", None)
        return not source_path or Path(str(source_path)).exists()

    async def get_item(self, user_id: str, item_id: str) -> MarketplaceItemDetail:
        ns, kind, ref = self._parse_item_id(item_id)
        if ns == "skillhub" and kind == "skill":
            return await self._skillhub_detail(user_id, ref)
        if ns == "valuz" and kind == "agent":
            return await self._agent_template_detail(user_id, ref)
        if ns == "valuz" and kind == "team":
            return await self._team_detail(user_id, ref)
        raise MarketplaceItemNotFound(f"Unknown marketplace item: {item_id}")

    async def _skillhub_detail(self, user_id: str, slug: str) -> MarketplaceItemDetail:
        try:
            payload = await self._hub.skill_detail(slug)
            files = await self._hub.skill_files(slug)
            cat_labels = await self._category_labels()
        except SkillHubUnavailableError as exc:
            raise MarketplaceUpstreamError(str(exc)) from exc
        try:
            evaluation_payload: dict[str, Any] | None = await self._hub.skill_evaluation(slug)
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
        indexed = await self._skills.get_indexed_skill(user_id, slug)
        installed = indexed is not None and self._is_installed_skill_row(indexed)
        base = self._skillhub_item(raw, {slug} if installed else set(), cat_labels)

        owner = (payload.get("owner") or {}).get("displayName") or skill.get("ownerName")
        security = self._normalize_security(payload.get("securityReports"))
        evaluation = self._normalize_evaluation(evaluation_payload)
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
                MarketplaceFileEntry(
                    path=f.get("path", ""), size=f.get("size"), sha256=f.get("sha256")
                )
                for f in files
                if f.get("path")
            ],
            security=security,
            evaluation=evaluation,
        )

    @staticmethod
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

    @staticmethod
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
            raw_score = MarketplaceService._coerce_score(entry.get("score"))
            if raw_score is None:
                items = entry.get("items")
                item_scores = [
                    score
                    for item in (items.values() if isinstance(items, dict) else [])
                    if isinstance(item, dict)
                    for score in [MarketplaceService._coerce_score(item.get("score"))]
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
        rating = MarketplaceService._evaluation_rating(score)
        summary = payload.get("userSummary") or payload.get("summary")
        return MarketplaceEvaluationReport(
            score=score,
            rating=rating,
            summary=str(summary) if summary else None,
            dimensions=normalized,
        )

    @staticmethod
    def _coerce_score(value: Any) -> float | None:
        if isinstance(value, (int, float)):
            return round(float(value), 1)
        try:
            return round(float(value), 1) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
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

    async def _agent_template_detail(self, user_id: str, template_id: str) -> MarketplaceItemDetail:
        tpl = next((t for t in load_agent_templates() if t.id == template_id), None)
        if tpl is None:
            raise MarketplaceItemNotFound(f"Unknown agent template: {template_id}")
        base = self._agent_template_item(tpl, await self._library_agent_slugs(user_id))
        return MarketplaceItemDetail(
            **base.model_dump(),
            owner="Valuz",
            instructions=resolve_text(tpl.instructions),
            bound_skills=[resolve_text(s) for s in tpl.skills],
            connectors=[
                MarketplaceConnectorRequirement(
                    name=resolve_text(c.name), requirement=c.requirement
                )
                for c in tpl.connectors
            ],
        )

    async def _team_detail(self, user_id: str, pack_id: str) -> MarketplaceItemDetail:
        from valuz_agent.modules.agent_packs.errors import PackNotFound

        try:
            pack = await self._packs.get_pack(user_id, pack_id)
        except PackNotFound as exc:
            raise MarketplaceItemNotFound(f"Unknown team template: {pack_id}") from exc
        base = self._team_item(pack)
        roles = pack.get("roles") or []
        skill_slugs = sorted({s for r in roles for s in (r.get("skills") or [])})
        connector_slugs = sorted({c for r in roles for c in (r.get("connector_types") or [])})
        return MarketplaceItemDetail(
            **base.model_dump(),
            owner="Valuz",
            instructions=self._team_collaboration_summary(pack, roles),
            workflow=self._team_workflow_steps(roles),
            deliverables=self._team_deliverables(pack),
            usage_notes=self._team_usage_notes(pack),
            bound_skills=skill_slugs,
            connectors=[
                MarketplaceConnectorRequirement(name=c, requirement="required")
                for c in connector_slugs
            ],
        )

    @staticmethod
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

    @staticmethod
    def _team_workflow_steps(roles: list[dict[str, Any]]) -> list[str]:
        steps: list[str] = []
        for index, role in enumerate(roles, start=1):
            name = role.get("name") or f"Agent {index}"
            responsibility = role.get("description") or "完成对应阶段任务"
            steps.append(f"{name}：{responsibility}")
        if steps:
            steps.append("汇总交付：整合各成员结果，形成可复用的最终成果包")
        return steps

    @staticmethod
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

    @staticmethod
    def _team_usage_notes(pack: dict[str, Any]) -> list[str]:
        category = str(pack.get("scenario") or "")
        notes: list[str] = []
        if category in {"金融投资", "法务安全"}:
            notes.append("高敏感领域输出仅作为草稿和辅助分析，需由专业人士复核。")
        if category == "特色分类":
            notes.append("该分类按文化/娱乐内容定位，不应作为医疗、法律、金融或重大人生决策依据。")
        return notes

    # ------------------------------------------------------------------
    # Install
    # ------------------------------------------------------------------

    async def install(
        self,
        user_id: str,
        item_id: str,
        *,
        runtime: str | None = None,
        provider_id: str | None = None,
        model: str | None = None,
        effort: str | None = None,
    ) -> MarketplaceInstallResult:
        """Confirmed install. ``runtime/provider_id/model/effort`` are the
        caller-resolved deploy defaults, required only for agent/team items
        (the route resolves them lazily to keep skill installs independent of
        model-channel setup)."""
        ns, kind, ref = self._parse_item_id(item_id)
        if ns == "skillhub" and kind == "skill":
            return await self._install_skillhub_skill(user_id, item_id, ref)
        if ns == "valuz" and kind == "agent":
            return await self._install_agent_template(
                user_id, item_id, ref, runtime=runtime, provider_id=provider_id,
                model=model, effort=effort,
            )
        if ns == "valuz" and kind == "team":
            return await self._install_team(
                user_id, item_id, ref, runtime=runtime, provider_id=provider_id,
                model=model, effort=effort,
            )
        raise MarketplaceItemNotFound(f"Unknown marketplace item: {item_id}")

    async def _install_skillhub_skill(
        self,
        user_id: str,
        item_id: str,
        slug: str,
        *,
        allow_rename: bool = True,
    ) -> MarketplaceInstallResult:
        existing = await self._skills.get_indexed_skill(user_id, slug)
        if existing is not None and self._is_installed_skill_row(existing):
            return MarketplaceInstallResult(
                item_id=item_id, status="already_installed", installed_ref=slug
            )
        from valuz_agent.modules.skills.errors import SkillImportFailed

        url = self._hub.download_url(slug)
        try:
            preview = await self._skills.import_url_preview(user_id, url)
        except SkillImportFailed as exc:
            # A fetch failure here is an upstream problem (SkillHub or its
            # CDN), not a bad request from the user.
            if "Failed to fetch URL" in str(exc):
                raise MarketplaceUpstreamError(str(exc)) from exc
            raise
        # Preserve the SkillHub catalog slug locally. Some archives use a
        # friendlier manifest name than their catalog slug (e.g. "ima skill"
        # vs "ima-skills"); the marketplace installed-state is keyed by the
        # catalog slug, so imports must keep that slug stable.
        name = slug
        if allow_rename and preview.name_conflict and preview.suggested_name:
            name = preview.suggested_name
        view = await self._skills.confirm_url_import(
            user_id,
            SkillImportUrlConfirmRequest(preview_id=preview.preview_id, name=name),
        )
        logger.info("marketplace installed skillhub skill %s as %s", slug, view.slug)
        return MarketplaceInstallResult(
            item_id=item_id, status="installed", installed_ref=view.slug
        )

    async def _install_agent_template(
        self,
        user_id: str,
        item_id: str,
        template_id: str,
        *,
        runtime: str | None,
        provider_id: str | None,
        model: str | None,
        effort: str | None,
    ) -> MarketplaceInstallResult:
        tpl = next((t for t in load_agent_templates() if t.id == template_id), None)
        if tpl is None:
            raise MarketplaceItemNotFound(f"Unknown agent template: {template_id}")
        payload: dict[str, Any] = {
            "slug": tpl.slug,
            "name": resolve_text(tpl.name),
            "description": resolve_text(tpl.role),
            "instructions": resolve_text(tpl.instructions),
            "avatar": tpl.icon,
            "effort": effort or tpl.effort,
        }
        if runtime:
            payload["runtime"] = runtime
        if model:
            payload["model"] = model
        if provider_id:
            payload["provider_id"] = provider_id
        try:
            row = await self._agents.create_agent(user_id, payload)
        except MemberAlreadyExistsError:
            return MarketplaceInstallResult(
                item_id=item_id, status="already_installed", installed_ref=tpl.slug
            )
        logger.info("marketplace installed agent template %s as %s", template_id, row.slug)
        return MarketplaceInstallResult(item_id=item_id, status="installed", installed_ref=row.slug)

    async def _install_team(
        self,
        user_id: str,
        item_id: str,
        pack_id: str,
        *,
        runtime: str | None,
        provider_id: str | None,
        model: str | None,
        effort: str | None,
    ) -> MarketplaceInstallResult:
        from valuz_agent.modules.agent_packs.errors import PackNotFound

        try:
            pack = await self._packs.get_pack(user_id, pack_id)
            await self._install_team_skillhub_dependencies(user_id, pack)
            result = await self._packs.import_pack(
                user_id,
                pack_id,
                runtime=runtime or "claude_agent",
                provider_id=provider_id or "",
                model=model or "",
                effort=effort,
            )
        except PackNotFound as exc:
            raise MarketplaceItemNotFound(f"Unknown team template: {pack_id}") from exc
        created = int(result.get("created") or 0)
        skipped = int(result.get("skipped") or 0)
        logger.info(
            "marketplace installed team pack %s (created=%d skipped=%d)",
            pack_id, created, skipped,
        )
        return MarketplaceInstallResult(
            item_id=item_id,
            status="installed" if created > 0 else "already_installed",
            installed_ref=pack_id,
            created=created,
            skipped=skipped,
        )

    async def _install_team_skillhub_dependencies(
        self, user_id: str, pack: dict[str, Any]
    ) -> None:
        """Install remote SkillHub skills a curated Team depends on.

        Team manifests stay declarative: Valuz owns the Team/Agent structure,
        while SkillHub dependencies are downloaded at import time so the
        resulting agents can resolve their skill slugs immediately.
        """
        for dep in pack.get("skills") or []:
            if dep.get("source") != "skillhub":
                continue
            slug = str(dep.get("slug") or "")
            if not slug:
                continue
            await self._install_skillhub_skill(
                user_id,
                f"skillhub:skill:{slug}",
                slug,
                allow_rename=False,
            )
