import os
from functools import wraps

# The document parser offloads to a ``ProcessPoolExecutor`` in production
# (see ``valuz_agent.infra.parse_pool``). For the unit suite we force the
# in-thread / inline fallback so parse tests stay fast and don't spawn
# subprocesses (which re-import modules and slow CI). The dedicated offload
# regression test (``test_parse_pool_offload``) re-enables the pool explicitly.
os.environ.setdefault("VALUZ_PARSE_POOL_DISABLED", "1")

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
