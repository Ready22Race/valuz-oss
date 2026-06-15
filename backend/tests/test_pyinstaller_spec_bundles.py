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
