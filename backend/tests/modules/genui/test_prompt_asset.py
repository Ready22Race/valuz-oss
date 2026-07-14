"""The vendored OpenUI prompt asset is present and loadable as a package resource."""

from importlib import resources


def test_library_prompt_asset_is_nonempty():
    text = (
        resources.files("valuz_agent.modules.genui")
        .joinpath("openui_genui_lib_prompt.txt")
        .read_text(encoding="utf-8")
    )
    assert len(text) > 200, "vendored OpenUI prompt looks empty — run gen:openui-prompt"
