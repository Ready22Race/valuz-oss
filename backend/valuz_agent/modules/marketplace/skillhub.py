"""Async client for the SkillHub public catalog (``api.skillhub.cn``).

The marketplace service is the ONLY consumer — the frontend never talks to
SkillHub directly. Every method here is read-only catalog metadata; the
actual skill archive download happens through the skills URL-import
pipeline (which enforces the size/count caps), pointed at
:meth:`SkillHubClient.download_url`.

Upstream quirks (verified 2026-07-08):

- ``GET /api/skills`` wraps its payload in ``{code, data, message}``;
  ``/api/v1/categories`` and ``/api/v1/skills/{slug}/files`` return
  ``{count, items|files}``; the detail endpoint returns a bare object.
- ``/api/v1/skills/{slug}/evaluation`` returns SkillHub's TRACE quality
  report when available.
- Server-side filters: ``category=``, ``keyword=``, ``source=``. There is
  no subcategory / verified filter; default sort is the curated ``score``.
- ``/api/v1/download?slug=`` answers with a 302 to a COS-hosted zip.
"""

from __future__ import annotations

import logging
import time
from typing import Any, cast
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.skillhub.cn"

_TIMEOUT_SECONDS = 15.0
_CATEGORIES_TTL = 600.0
_LIST_TTL = 60.0
_DETAIL_TTL = 300.0


class SkillHubUnavailableError(Exception):
    """SkillHub could not be reached or returned an unusable payload."""


class SkillHubClient:
    """Thin cached reader over the SkillHub HTTP API.

    A short in-memory TTL cache keeps tab switches and pagination snappy and
    shields SkillHub from per-keystroke traffic; it is per-process state, not
    a durable mirror.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._client = client  # injected in tests; None → one client per call
        self._cache: dict[str, tuple[float, Any]] = {}

    # -- low-level ---------------------------------------------------------

    async def _get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self._base}{path}"
        try:
            if self._client is not None:
                resp = await self._client.get(url, params=params)
            else:
                async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                    resp = await client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            logger.warning("skillhub request failed: %s %s: %s", path, params, exc)
            raise SkillHubUnavailableError(str(exc)) from exc
        except ValueError as exc:  # non-JSON body
            logger.warning("skillhub returned non-JSON for %s: %s", path, exc)
            raise SkillHubUnavailableError("invalid JSON from SkillHub") from exc

    def _cached(self, key: str) -> Any | None:
        entry = self._cache.get(key)
        if entry is not None and entry[0] > time.monotonic():
            return entry[1]
        return None

    def _store(self, key: str, value: Any, ttl: float) -> None:
        self._cache[key] = (time.monotonic() + ttl, value)

    # -- catalog reads -------------------------------------------------------

    async def categories(self) -> list[dict[str, Any]]:
        """Level-1 category tree: ``[{key, name, nameEn, ...}]``."""
        cache_key = "categories"
        hit = self._cached(cache_key)
        if hit is not None:
            return cast(list[dict[str, Any]], hit)
        payload = await self._get_json("/api/v1/categories")
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise SkillHubUnavailableError("unexpected categories payload")
        self._store(cache_key, items, _CATEGORIES_TTL)
        return items

    async def list_skills(
        self,
        *,
        page: int = 1,
        page_size: int = 30,
        category: str | None = None,
        keyword: str | None = None,
        source: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """One page of the skill catalog (score-sorted) plus the total count."""
        params: dict[str, Any] = {"page": page, "pageSize": page_size}
        if category:
            params["category"] = category
        if keyword:
            params["keyword"] = keyword
        if source:
            params["source"] = source
        cache_key = f"skills:{sorted(params.items())}"
        hit = self._cached(cache_key)
        if hit is not None:
            return cast(tuple[list[dict[str, Any]], int], hit)
        payload = await self._get_json("/api/skills", params=params)
        if not isinstance(payload, dict) or payload.get("code") != 0:
            raise SkillHubUnavailableError("unexpected skills payload")
        data = payload.get("data") or {}
        skills = data.get("skills") or []
        total = int(data.get("total") or 0)
        if not isinstance(skills, list):
            raise SkillHubUnavailableError("unexpected skills payload")
        result = (skills, total)
        self._store(cache_key, result, _LIST_TTL)
        return result

    async def recommended_skills(self) -> list[dict[str, Any]]:
        """SkillHub's official curated shelf (``推荐精选``, ~100 skills).

        Same item shape as :meth:`list_skills`; the order is SkillHub's own
        curation order. This is the browse surface — the full catalog is only
        reachable through search.
        """
        cache_key = "showcase:recommended"
        hit = self._cached(cache_key)
        if hit is not None:
            return cast(list[dict[str, Any]], hit)
        payload = await self._get_json("/api/v1/showcase/recommended")
        skills = payload.get("skills") if isinstance(payload, dict) else None
        if not isinstance(skills, list):
            raise SkillHubUnavailableError("unexpected showcase payload")
        self._store(cache_key, skills, _CATEGORIES_TTL)
        return skills

    async def skill_detail(self, slug: str) -> dict[str, Any]:
        """Detail payload: ``{skill, owner, latestVersion, securityReports, …}``."""
        cache_key = f"detail:{slug}"
        hit = self._cached(cache_key)
        if hit is not None:
            return cast(dict[str, Any], hit)
        payload = await self._get_json(f"/api/v1/skills/{quote(slug, safe='')}")
        if not isinstance(payload, dict) or "skill" not in payload:
            raise SkillHubUnavailableError("unexpected skill detail payload")
        self._store(cache_key, payload, _DETAIL_TTL)
        return payload

    async def skill_files(self, slug: str) -> list[dict[str, Any]]:
        """Pre-install file listing: ``[{path, sha256, size}]``."""
        cache_key = f"files:{slug}"
        hit = self._cached(cache_key)
        if hit is not None:
            return cast(list[dict[str, Any]], hit)
        payload = await self._get_json(f"/api/v1/skills/{quote(slug, safe='')}/files")
        files = payload.get("files") if isinstance(payload, dict) else None
        if not isinstance(files, list):
            raise SkillHubUnavailableError("unexpected skill files payload")
        self._store(cache_key, files, _DETAIL_TTL)
        return files

    async def skill_evaluation(self, slug: str) -> dict[str, Any]:
        """TRACE quality report for the skill, when SkillHub exposes one."""
        cache_key = f"evaluation:{slug}"
        hit = self._cached(cache_key)
        if hit is not None:
            return cast(dict[str, Any], hit)
        payload = await self._get_json(f"/api/v1/skills/{quote(slug, safe='')}/evaluation")
        if not isinstance(payload, dict) or not isinstance(payload.get("dimensions"), dict):
            raise SkillHubUnavailableError("unexpected skill evaluation payload")
        self._store(cache_key, payload, _DETAIL_TTL)
        return payload

    def download_url(self, slug: str) -> str:
        """Archive URL for the skills URL-import pipeline (302 → COS zip)."""
        return f"{self._base}/api/v1/download?slug={quote(slug, safe='')}"
