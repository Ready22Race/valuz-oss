import os
import tempfile
from functools import wraps
from pathlib import Path

# The document parser offloads to a ``ProcessPoolExecutor`` in production
# (see ``valuz_agent.infra.parse_pool``). For the unit suite we force the
# in-thread / inline fallback so parse tests stay fast and don't spawn
# subprocesses (which re-import modules and slow CI). The dedicated offload
# regression test (``test_parse_pool_offload``) re-enables the pool explicitly.
os.environ.setdefault("VALUZ_PARSE_POOL_DISABLED", "1")

# ---------------------------------------------------------------------------
# Home sandbox — env-level isolation that survives config-module reloads.
#
# History: the suite repeatedly leaked fixture skills (``created``,
# ``empty-session-N``, ``weekly-report-vN``, …) and ``local-test-owner`` DB
# rows into the REAL user home (``~/.agent[s]/skills``, ``~/.valuz-oss``).
# Attribute-level monkeypatches on the ``settings`` singleton (including the
# ``_isolate_user_skills_dir`` fence below) cannot fully stop that: any test
# that reloads ``valuz_agent.infra.config`` builds a NEW ``Settings`` from the
# environment, and code holding the fresh singleton writes to the real
# defaults again. Pinning the environment itself — before ANY valuz_agent
# import — makes every (re)constructed ``Settings`` land in the sandbox.
#
# Force-set (not ``setdefault``): a dev shell exporting VALUZ_DATA_DIR (e.g.
# ``scripts/dev.sh`` pins ``~/.valuz-oss-dev``) must not bleed into tests
# either.
_HOME_SANDBOX = Path(tempfile.mkdtemp(prefix="valuz-test-home-"))

# (1) Positive pins — every real-home path a test could write lands INSIDE the
# sandbox. Each of these fields defaults to a location under the user's actual
# home; pinning them makes the resolved path sandbox-relative instead.
os.environ["VALUZ_DATA_DIR"] = str(_HOME_SANDBOX / "valuz-data")
os.environ["VALUZ_LOG_DIR"] = str(_HOME_SANDBOX / "logs")
os.environ["VALUZ_USER_SKILLS_DIR"] = str(_HOME_SANDBOX / "user-skills")
# ``user_project_root`` defaults to ``~/Valuz`` — a REAL directory the user
# keeps data in (backups live under ``~/Valuz/backups``). Tests that create a
# managed project (``ProjectService.create_project`` / import-confirm without a
# ``root_path``) write a project marker there via ``fs_registry.project_root()``.
os.environ["VALUZ_USER_PROJECT_ROOT"] = str(_HOME_SANDBOX / "projects")
# ``backup_root`` defaults to ``~/.valuz-oss-backups`` — another real-home
# location; pin it so backup tests (and any code touching the default backup
# destination) stay inside the sandbox.
os.environ["VALUZ_BACKUP_ROOT"] = str(_HOME_SANDBOX / "backups")

# (2) Kernel durable-store tier — force the in-process/local backend so a test
# that boots the kernel dual-writes to the SANDBOXED host db (boot injects the
# sandbox ``db_url`` as the durable URL only when ``KERNEL_STORE == "local"``),
# never to an ambient pg/remote backend. Read exact-case via ``os.getenv``.
os.environ["KERNEL_STORE"] = "local"

# (3) Clear ambient overrides that would REDIRECT a write OUTSIDE the sandbox,
# so each falls back to its sandboxed default:
#   * VALUZ_DATABASE_URL / VALUZ_KERNEL_DATABASE_URL — ``fs_registry.db_url()`` /
#     ``kernel_db_url()`` return these VERBATIM when set, bypassing the
#     data-dir-derived SQLite path pinned above.
#   * VALUZ_DURABLE_DATABASE_URL / VALUZ_DATA_API_* — the kernel ``AppConfig``
#     reads these directly; left set they would dual-write to a real pg/remote
#     durable store even with the host db sandboxed.
#   * VALUZ_USER_SKILL_STAGING_DIR / VALUZ_USER_TEMP_DIR — optional dir
#     overrides whose ``None`` default already resolves under the sandboxed
#     data dir / OS temp; deleting an ambient value restores that safe default
#     (unlike PINNING them, which would flip the legacy-staging branch).
# A dev shell or CI env exporting any of these would re-leak into a real DB or
# real home that the filesystem tripwire below cannot see. Case-insensitive:
# pydantic-settings matches env vars without regard to case, so any spelling
# must go; VALUZ_DATA_API_* is matched by prefix (URL / TOKEN / KIND).
_SANDBOX_ESCAPE_HATCHES = frozenset(
    {
        "VALUZ_DATABASE_URL",
        "VALUZ_KERNEL_DATABASE_URL",
        "VALUZ_DURABLE_DATABASE_URL",
        "VALUZ_USER_SKILL_STAGING_DIR",
        "VALUZ_USER_TEMP_DIR",
    }
)
for _escape_key in [
    k
    for k in os.environ
    if k.upper() in _SANDBOX_ESCAPE_HATCHES or k.upper().startswith("VALUZ_DATA_API_")
]:
    del os.environ[_escape_key]

# ---------------------------------------------------------------------------
# Owner context — explicit-identity semantics (no implicit fallback).
#
# Production seeds the boot context once via ``ensure_local_identity()``;
# requests get their owner from ``AuthMiddleware``. Tests are neither, so this
# autouse fixture plays the boot role: every test runs with an explicitly-set
# owner, and inserts from a never-seeded context keep failing loudly (covered
# by ``tests/infra/test_ownership.py``, which opts out via fresh Contexts).
# ---------------------------------------------------------------------------
import pytest  # noqa: E402
import inspect


@pytest.fixture(autouse=True)
def _seed_owner_context():
    from valuz_agent.infra.auth_context import (
        reset_current_user_id,
        set_current_user_id,
    )

    token = set_current_user_id("local-test-owner")
    yield
    reset_current_user_id(token)


# ---------------------------------------------------------------------------
# CLI-subscription login probe — default every test to "logged in".
#
# ``ProviderService.list_providers`` / ``get_provider`` gate the Claude·Codex
# subscription channels on a real ``claude auth status`` / ``codex login status``
# shell-out (see ``modules.providers.cli_login_probe``). Left unmocked, every
# provider-list test would spawn those subprocesses — slow, and the result would
# depend on whether the dev/CI machine happens to be logged in. Default to
# "logged in" so subscription channels keep their models (the pre-gate behaviour
# the bulk of the suite asserts); the dedicated gate tests override this.
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _default_cli_login(monkeypatch):
    async def _logged_in(_tool):
        return True

    monkeypatch.setattr(
        "valuz_agent.modules.providers.service.detect_cli_login",
        _logged_in,
        raising=True,
    )


_DEFAULT_TEST_USER_ID = "local-test-owner"


def _patch_default_user_id(defaulted_fn, default_user_id: str = _DEFAULT_TEST_USER_ID):
    if not callable(defaulted_fn):  # pragma: no cover
        return defaulted_fn

    sig = inspect.signature(defaulted_fn)
    if "user_id" not in sig.parameters:
        return defaulted_fn

    params = list(sig.parameters.values())
    user_param = sig.parameters["user_id"]
    user_idx = params.index(user_param)

    def _call_with_user_id(*args, **kwargs):
        if "user_id" not in kwargs and len(args) <= user_idx:
            if user_param.kind == inspect.Parameter.POSITIONAL_ONLY:
                return defaulted_fn(*args[:user_idx], default_user_id, *args[user_idx:], **kwargs)
            kwargs = dict(kwargs)
            kwargs["user_id"] = default_user_id
        return defaulted_fn(*args, **kwargs)

    if inspect.iscoroutinefunction(defaulted_fn):

        @wraps(defaulted_fn)
        async def _async_wrapper(*args, **kwargs):
            return await _call_with_user_id(*args, **kwargs)

        return _async_wrapper

    @wraps(defaulted_fn)
    def _wrapper(*args, **kwargs):
        return _call_with_user_id(*args, **kwargs)

    return _wrapper


def _apply_default_user_id_patches():
    from valuz_agent.adapters import agent_resolver, capability_resolver, event_sse_adapter
    from valuz_agent.adapters import model_resolver, provider_resolver
    from valuz_agent.api.routes import onboarding
    from valuz_agent.modules.decisions import service as decisions_service
    from valuz_agent.modules.memory import runner
    from valuz_agent.modules.projects import tools as project_tools
    from valuz_agent.modules.settings import parser_routing
    from valuz_agent.integrations import tools_agent_proposal
    from valuz_agent.integrations import tools_skill_creator
    from valuz_agent.modules.parser.setup_jobs import base as setup_jobs_base
    from valuz_agent.modules.decisions import aggregator as decisions_aggregator
    from valuz_agent.modules.memory import tools as memory_tools

    patches = [
        (agent_resolver, "_resolve_agent_provider"),
        (agent_resolver, "build_member_session"),
        (capability_resolver, "resolve_session_capabilities"),
        (event_sse_adapter, "list_events_after"),
        (event_sse_adapter, "list_events_window"),
        (event_sse_adapter, "iter_events_sse"),
        (model_resolver, "resolve_model"),
        (provider_resolver, "resolve_model_provider"),
        (provider_resolver, "resolve_runtime_provider"),
        (onboarding, "_resolve_deploy_target"),
        (project_tools, "_handler"),
        (parser_routing, "get_primary_plugin_id"),
        (parser_routing, "set_primary_plugin_id"),
        (parser_routing, "get_by_kind"),
        (parser_routing, "set_by_kind"),
        (parser_routing, "get_fallback_to_local_on_error"),
        (parser_routing, "set_fallback_to_local_on_error"),
        (parser_routing, "get_plugin_config"),
        (parser_routing, "update_plugin_config"),
        (setup_jobs_base.SetupJobController, "get"),
        (setup_jobs_base.SetupJobController, "start"),
        (setup_jobs_base.SetupJobController, "cancel"),
        (setup_jobs_base.SetupJobController, "_write_row"),
        (setup_jobs_base.SetupJobController, "_update_progress"),
        (decisions_service, "enrich_pending"),
        (runner, "run_extraction_for_session"),
        (tools_agent_proposal, "_propose_agent_handler"),
        (tools_agent_proposal, "_update_agent_handler"),
        (tools_agent_proposal, "_list_skills_handler"),
        (tools_agent_proposal, "_list_agents_handler"),
        (tools_agent_proposal, "_list_model_options_handler"),
        (tools_agent_proposal, "_list_project_members_handler"),
        (tools_skill_creator, "_submit_skill_handler"),
        (decisions_aggregator, "enrich_pending"),
        (memory_tools, "_memory_handler"),
    ]

    for target_module, attr in patches:
        setattr(target_module, attr, _patch_default_user_id(getattr(target_module, attr)))


def pytest_sessionstart(session):
    _apply_default_user_id_patches()


@pytest.fixture(autouse=True)
def _reset_host_data_plane():
    """Unbind the host data-plane client between tests.

    ``kernel_client.bind_host_data_store`` (run by ``bind_data_service`` during
    boot-path tests) binds a module-global durable-backed client; in production
    the process serves exactly one app so the global is rebound once per boot,
    but across tests a stale binding would silently redirect every non-runtime
    facade read to a dead temp store."""
    yield
    from valuz_agent.adapters import kernel_client

    kernel_client.bind_host_data_store(None)


@pytest.fixture(autouse=True)
def _isolate_user_skills_dir(tmp_path, monkeypatch):
    """Hard fence: NEVER let a test write the real ``~/.agents/skills``.

    ``settings.user_skills_dir`` defaults to the REAL home skill library
    (shared by the packaged app and every dev instance). A test that
    exercises skill create/import without its own isolation used to leak
    fixture skills (``created``, ``empty-session-2``, ...) into the user's
    actual library, which every instance then indexed and materialized into
    every project. Tests that need a specific root still win: their own
    monkeypatch runs after this autouse fixture.
    """
    from valuz_agent.infra.config import settings as _settings

    monkeypatch.setattr(_settings, "user_skills_dir", tmp_path / "_isolated-user-skills")


# ---------------------------------------------------------------------------
# Real-home leak tripwire — no leak may ever land silently again.
#
# The env sandbox above closes every KNOWN write path, but the next
# ``Path.home()`` shortcut someone adds would leak silently for weeks (as the
# ``~/.agent/skills`` fixture pollution did — three cleanup rounds between
# 2026-07-10 and 2026-07-14). This session fixture snapshots the real home
# targets before the first test and fails the run loudly if the suite added
# anything to them. Watched: both skill library spellings (the pre-26a3e1e8
# default was ``~/.agent/skills``), the production data dir, and the legacy
# CLI skill roots (read-only by contract — a write there is always a bug).
#
# Note: entries are compared by top-level NAME, so a concurrently running
# desktop app quietly rewriting file contents under ``~/.valuz-oss`` does not
# false-positive; only something NEW appearing does. If this fires for you
# locally, a test wrote outside the sandbox — fix the test, don't widen the
# watchlist.
# ---------------------------------------------------------------------------
_REAL_HOME_WATCHED = (
    Path.home() / ".agents" / "skills",
    Path.home() / ".agent" / "skills",
    Path.home() / ".valuz-oss",
    Path.home() / "Valuz",  # user_project_root default — real user data lives here
    Path.home() / ".claude" / "skills",
    Path.home() / ".codex" / "skills",
)


def _real_home_snapshot() -> dict[str, set[str] | None]:
    return {
        str(root): (set(os.listdir(root)) if root.is_dir() else None) for root in _REAL_HOME_WATCHED
    }


# Baseline captured at conftest IMPORT time — this runs before
# ``pytest_sessionstart`` and its collection-time ``valuz_agent`` imports (which
# pull in dozens of modules). A session-scoped fixture would only snapshot at
# the first test's setup, i.e. AFTER those imports, folding any import- or
# session-start-time ``Path.home()`` write into the baseline as "pre-existing"
# and letting it leak silently — the exact class of bug this tripwire guards.
_REAL_HOME_BASELINE = _real_home_snapshot()


@pytest.fixture(scope="session", autouse=True)
def _real_home_leak_tripwire():
    yield
    after = _real_home_snapshot()
    leaks: list[str] = []
    for root, entries_after in after.items():
        entries_before = _REAL_HOME_BASELINE.get(root)
        added = sorted((entries_after or set()) - (entries_before or set()))
        if added:
            leaks.append(f"{root} gained: {added}")
    assert not leaks, (
        "Test suite leaked into the REAL home directory — a write path "
        "escaped the conftest home sandbox (check for a reloaded "
        "valuz_agent.infra.config, a hardcoded Path.home(), or a missing "
        f"env knob): {'; '.join(leaks)}"
    )
