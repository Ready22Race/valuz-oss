"""Regression guard: the PyInstaller spec must bundle the sibling top-level
packages the frozen build imports via ``sys.path``.

``valuz_agent`` / ``kernel`` / ``plugins`` are shipped as raw ``.py`` trees under
``_internal/`` (the entry script adds ``_internal`` to ``sys.path``). PyInstaller
collects them via explicit ``datas`` entries — its static analysis can't find
them on its own (``plugins.parser`` in particular is imported by a dynamic
``importlib.import_module`` string, so dropping its datas entry silently yields a
frozen build with ZERO parser plugins). These checks fail loudly if any entry is
removed, instead of waiting for a runtime ``ModuleNotFoundError`` in a packaged
build nobody runs in CI.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_SPEC = Path(__file__).resolve().parents[1] / "scripts" / "valuz_agent.spec"


@pytest.fixture(scope="module")
def spec_source() -> str:
    return _SPEC.read_text(encoding="utf-8")


@pytest.mark.parametrize("package", ["valuz_agent", "plugins", "kernel"])
def test_spec_bundles_sibling_package(spec_source: str, package: str) -> None:
    # The canonical raw-.py-via-sys.path datas entry: ``(str(HERE / "x"), "x")``.
    entry = f'(str(HERE / "{package}"), "{package}")'
    assert entry in spec_source, (
        f"PyInstaller spec no longer bundles the {package!r} package "
        f"(expected datas entry {entry!r}). The frozen build will fail to "
        f"import it."
    )


def test_spec_collects_magika_data_files(spec_source: str) -> None:
    """magika ships its ONNX model + config as PACKAGE DATA, loaded eagerly by
    ``MarkItDown.__init__``. If it's not in ``_data_pkgs`` (which drives
    ``collect_data_files``), the frozen build raises ``MagikaError: model not
    found`` and EVERY office parse (.docx/.xlsx/.pptx) fails while PDF/text
    keep working. No pyinstaller-hooks-contrib hook covers magika, so this
    explicit entry is the only thing that bundles it."""
    assert '"magika"' in spec_source, (
        "PyInstaller spec no longer lists 'magika' in _data_pkgs. The frozen "
        "build will ship MarkItDown without magika's model.onnx/config, so "
        "docx/xlsx/pptx parsing fails with 'model not found'."
    )


def test_magika_data_files_are_collectable() -> None:
    """Sanity-check the assumption behind the spec entry: magika really does
    expose its model + config via ``collect_data_files`` (and they include the
    .onnx model). Guards against a magika layout change silently breaking the
    bundle even while the spec still names it."""
    # PyInstaller is a build-only tool — skip when it isn't installed (the
    # static spec-source assertion above still guards the spec everywhere).
    pytest.importorskip("PyInstaller")
    from PyInstaller.utils.hooks import collect_data_files

    collected = collect_data_files("magika", include_py_files=False)
    sources = [src for src, _dest in collected]
    assert any(s.endswith("model.onnx") for s in sources), (
        f"magika no longer exposes model.onnx via collect_data_files; the "
        f"frozen office-parsing bundle may be incomplete. Collected: {sources}"
    )
