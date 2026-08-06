"""`generate_ui`'s `components` argument: which set one generation sees.

The split follows where a component comes from: `atoms` is everything this
repository ships (OpenUI's primitives and the built-in blocks), `edition` is
only what a vertical edition registered from outside it, `all` is both.

What these pin is that a scope never describes a component it does not offer —
a fallback the model was told to reach for but never shown is worse advice than
none — and that a scope with nothing in it widens rather than narrowing to
nothing.

The renderer is deliberately not narrowed: it keeps accepting everything, so a
narrow prompt can only ever produce a payload the client can draw.
"""

from __future__ import annotations

from valuz_agent.modules.genui.protocol import (
    GenUIComponentScope,
    a2ui_instructions,
    build_a2ui_catalog,
    build_a2ui_prompt,
    edition_catalog_text,
    normalize_component_scope,
    resolve_component_scope,
)
from valuz_agent.modules.genui.tools import _PARAMS

SCOPES: tuple[GenUIComponentScope, ...] = ("all", "atoms", "edition")

# One component from each half of what this repo ships, used to prove `atoms`
# carries both rather than only OpenUI's side.
_BLOCK = "MarketIndexGrid"
_PRIMITIVE = "SwitchGroup"


def test_default_is_the_whole_vocabulary():
    # An absent argument must not quietly narrow: a caller that says nothing
    # gets everything, which is the only default that cannot break an answer.
    assert normalize_component_scope(None) == "all"
    assert normalize_component_scope({}) == "all"


def test_unusable_values_widen_rather_than_fail():
    # This argument is written by a model. Costing the wider prompt is a far
    # better failure than losing the generation to a typo.
    assert normalize_component_scope("bogus") == "all"
    assert normalize_component_scope("") == "all"


def test_aliases_land_on_the_set_they_name():
    assert normalize_component_scope("Edition") == "edition"
    assert normalize_component_scope("vertical") == "edition"
    # "blocks" and "openui" both name halves of what this repo ships, and this
    # repo's set is one scope — neither is a set of its own.
    assert normalize_component_scope("blocks") == "atoms"
    assert normalize_component_scope("OpenUI") == "atoms"


def test_the_tool_advertises_the_argument():
    components = _PARAMS["properties"]["components"]
    assert components["enum"] == list(SCOPES)
    assert components["default"] == "all"
    # Not required: an agent that never learned about the argument keeps working.
    assert "components" not in _PARAMS["required"]


def test_atoms_carries_everything_this_repo_ships():
    # Both halves: OpenUI's primitives and the built-in blocks. Offering only
    # the primitives would drop the product vocabulary from the general set.
    atoms = build_a2ui_prompt("revenue dashboard", None, "atoms")
    assert _PRIMITIVE in atoms
    assert _BLOCK in atoms


def test_the_root_survives_every_scope():
    # Stack roots every document. A scope that dropped it would produce output
    # nothing can render — the one failure narrowing must never introduce.
    for scope in SCOPES:
        assert "Stack" in build_a2ui_prompt("chart", None, scope)


def test_an_empty_scope_widens_rather_than_offering_nothing():
    # No edition registers into OSS, so `edition` has no set of its own here.
    # Narrowing to the root alone would not make a smaller answer; it would
    # make none.
    assert edition_catalog_text() == ""
    assert resolve_component_scope("edition") == "all"
    assert build_a2ui_prompt("chart", None, "edition") == build_a2ui_prompt(
        "chart", None, "all"
    )


def test_the_catalog_and_the_instructions_agree_on_the_live_scope():
    # Both resolve the scope through one function, so a scope that widened
    # cannot leave the instructions describing the narrow set it asked for.
    for scope in SCOPES:
        resolved = resolve_component_scope(scope)
        assert build_a2ui_catalog(scope) == build_a2ui_catalog(resolved)
        assert a2ui_instructions(scope) == a2ui_instructions(resolved)


def test_instructions_never_recommend_a_component_the_scope_withheld():
    # The instructions name fallbacks to use when data has no chart series.
    # Naming one that is not in the catalog teaches the model to reach for
    # something it was never shown.
    for scope in SCOPES:
        catalog = build_a2ui_catalog(scope)
        instructions = a2ui_instructions(scope)
        if _BLOCK in instructions:
            assert _BLOCK in catalog


def test_the_a2ui_catalog_keeps_its_message_shape_in_every_scope():
    # The JSON examples are what make the protocol usable at all; they are also
    # full of braces, so a formatting mistake here silently truncates them.
    for scope in SCOPES:
        catalog = build_a2ui_catalog(scope)
        assert '{"id":"root","component":"Stack"' in catalog
        assert "{fallbacks}" not in catalog
