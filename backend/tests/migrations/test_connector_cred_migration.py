"""Migration 0004's credential merge — plaintext + FileSecretStore → unified.

Tests the pure transform (``_unify_creds``) directly with an injected secret
reader, so no DB / filesystem is needed.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib

_MIG = (
    pathlib.Path(__file__).resolve().parents[2]
    / "alembic"
    / "host"
    / "versions"
    / "0004_connector_db_storage.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("mig0004", _MIG)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_unify_merges_plaintext_and_secrets():
    m = _load()
    store = {
        "connector/c1/cred/header.X-API-Key": "sk-1",
        "connector/c1/cred/param.token": "p1",
    }
    h, p = m._unify_creds(
        '{"X-Trace": "t1"}',
        None,
        '[{"target":"header","name":"X-API-Key","secret_ref":"connector/c1/cred/header.X-API-Key"},'
        '{"target":"param","name":"token","secret_ref":"connector/c1/cred/param.token"}]',
        lambda r: store.get(r),
    )
    assert json.loads(h) == {
        "X-Trace": {"value": "t1", "secret": False},
        "X-API-Key": {"value": "sk-1", "secret": True},
    }
    assert json.loads(p) == {"token": {"value": "p1", "secret": True}}


def test_unify_no_creds_returns_none():
    m = _load()
    assert m._unify_creds(None, None, None, lambda r: None) == (None, None)


def test_unify_missing_secret_file_is_skipped():
    m = _load()
    h, p = m._unify_creds(
        None,
        None,
        '[{"target":"header","name":"X","secret_ref":"connector/c1/cred/header.X"}]',
        lambda r: None,  # file gone
    )
    assert h is None and p is None
