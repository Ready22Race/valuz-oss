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


def test_spec_collects_rapidocr_yaml_data(spec_source: str) -> None:
    """rapidocr reads two YAMLs out of its own package dir by
    ``Path(__file__).parent`` — a path that doesn't exist on disk in a frozen
    build. Without them every image parse dies with ``FileNotFoundError:
    .../rapidocr/config.yaml``, including for users who completed the model
    download (the config load happens before ``params`` overrides merge)."""
    assert '"rapidocr": ["config.yaml", "default_models.yaml"]' in spec_source, (
        "PyInstaller spec no longer collects rapidocr's config.yaml / "
        "default_models.yaml. The frozen build will raise FileNotFoundError on "
        "every image OCR parse."
    )


def test_rapidocr_reads_exactly_the_yamls_the_spec_collects() -> None:
    """Tie the spec's include list to what rapidocr actually resolves, so a
    layout/rename upstream fails here instead of in a packaged build."""
    pytest.importorskip("rapidocr")
    from rapidocr.inference_engine.base import MODEL_URL_PATH
    from rapidocr.main import DEFAULT_CFG_PATH

    collected = {"config.yaml", "default_models.yaml"}
    assert DEFAULT_CFG_PATH.name in collected, (
        f"rapidocr's default config is now {DEFAULT_CFG_PATH.name!r}; update "
        f"_filtered_data_pkgs in the spec."
    )
    assert MODEL_URL_PATH.name in collected, (
        f"rapidocr's model manifest is now {MODEL_URL_PATH.name!r}; update "
        f"_filtered_data_pkgs in the spec."
    )


def test_rapidocr_yamls_are_collectable_without_the_onnx_weights() -> None:
    """The filtered collection must yield the two YAMLs and NOT the ~16 MB of
    bundled .onnx weights (those ship via the user-authorized model download)."""
    pytest.importorskip("PyInstaller")
    pytest.importorskip("rapidocr")
    from PyInstaller.utils.hooks import collect_data_files

    collected = collect_data_files(
        "rapidocr",
        include_py_files=False,
        includes=["config.yaml", "default_models.yaml"],
    )
    names = sorted(Path(src).name for src, _dest in collected)
    assert names == ["config.yaml", "default_models.yaml"], (
        f"rapidocr filtered data collection changed shape: {names}"
    )
