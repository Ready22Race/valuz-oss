"""The vendored OpenUI prompt assets are present and loadable as package resources."""

import pytest

from valuz_agent.modules.genui.prompts import _load_library_prompt


@pytest.mark.parametrize("scope", ["all", "edition", "atoms"])
def test_library_prompt_asset_is_nonempty(scope):
    # One asset per scope, all written by the same generator run. A missing one
    # is a hard failure at generation time rather than a degraded prompt, which
    # is the right trade — but only if something notices before release.
    text = _load_library_prompt(scope)
    assert len(text) > 200, (
        f"vendored OpenUI prompt for scope {scope!r} looks empty — run gen:openui-prompt"
    )
