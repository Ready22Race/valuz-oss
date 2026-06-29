"""Coverage for ``ParserPluginRegistry`` (PR-1 scope)."""

from __future__ import annotations

import logging

import pytest

import valuz_agent.modules.parser.registry as registry_mod
from valuz_agent.modules.parser.registry import (
    LIGHT_LOCAL_PLUGIN_ID,
    ParserPluginRegistry,
    UnknownPluginError,
    build_default_registry,
)


class TestBuildDefaultRegistry:
    def test_default_registry_contains_light_local(self) -> None:
        registry = build_default_registry()
        assert LIGHT_LOCAL_PLUGIN_ID in registry

    def test_missing_light_local_logs_actionable_root_cause(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Simulate the packaged-build failure (``plugins.parser`` not bundled →
        # ``ModuleNotFoundError`` → discovery returns nothing): no built-in
        # plugins load, so ``light_local`` (the universal fallback) is absent.
        # The registry must surface the ROOT CAUSE as a single ERROR rather than
        # leaving the router to raise cryptic ``UnknownPluginError``s downstream
        # (the "rescan: failed to snapshot routing for kinds" symptom).
        monkeypatch.setattr(registry_mod, "_discover_builtin_subpackages", lambda: [])
        monkeypatch.setattr(registry_mod, "_discover_entry_point_plugins", lambda: [])
        with caplog.at_level(logging.ERROR, logger="valuz_agent.modules.parser.registry"):
            registry = build_default_registry()
        assert LIGHT_LOCAL_PLUGIN_ID not in registry
        assert any(
            rec.levelno == logging.ERROR and "UNAVAILABLE" in rec.getMessage()
            for rec in caplog.records
        )

    def test_default_registry_without_scheduler_excludes_cloud_plugins(self) -> None:
        # Without a scheduler reference we get only LightLocal + Valuz
        # OCR placeholder. Both PaddleOCR and MinerU are async-poll and
        # need a running ``PollingScheduler`` — they're skipped when
        # the caller doesn't provide one (e.g. minimal test setups).
        registry = build_default_registry()
        assert len(registry) == 2
        assert "mineru" not in registry
        assert "paddleocr" not in registry
        assert "light_local" in registry
        assert "valuz_ocr" in registry

    def test_default_registry_with_scheduler_includes_all_four(self) -> None:
        from unittest.mock import MagicMock

        # The scheduler is only touched when a plugin's ``build`` runs,
        # so a stub object suffices for descriptor enumeration.
        registry = build_default_registry(scheduler=MagicMock())
        assert len(registry) == 4
        for plugin_id in ("light_local", "paddleocr", "mineru", "valuz_ocr"):
            assert plugin_id in registry

    def test_light_local_descriptor_has_all_supported_kinds(self) -> None:
        registry = build_default_registry()
        descriptor = registry.get(LIGHT_LOCAL_PLUGIN_ID).descriptor
        assert {"pdf", "image", "office", "spreadsheet", "web", "text"} == set(
            descriptor.supported_kinds
        )


class TestRegistryLookup:
    def test_get_returns_plugin_for_known_id(self) -> None:
        registry = build_default_registry()
        plugin = registry.get(LIGHT_LOCAL_PLUGIN_ID)
        assert plugin.descriptor.id == LIGHT_LOCAL_PLUGIN_ID

    def test_get_raises_for_unknown_id(self) -> None:
        registry = build_default_registry()
        with pytest.raises(UnknownPluginError):
            registry.get("does_not_exist")

    def test_try_get_returns_none_for_unknown_id(self) -> None:
        registry = build_default_registry()
        assert registry.try_get("does_not_exist") is None

    def test_duplicate_plugin_id_rejected_at_construction(self) -> None:
        from plugins.parser.light_local import LightLocalPlugin

        with pytest.raises(ValueError, match="duplicate"):
            ParserPluginRegistry(plugins=[LightLocalPlugin(), LightLocalPlugin()])
