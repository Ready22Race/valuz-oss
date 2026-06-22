import os

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
