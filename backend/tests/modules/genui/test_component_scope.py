"""`generate_ui`'s `components` argument: which vocabulary one generation sees.

What these pin is the pair of properties that make narrowing safe. A narrower
scope must actually cost less — otherwise the argument is decoration — and it
must never describe a component it does not offer, because a fallback the model
was told to reach for but never shown is worse advice than none.

The renderer is deliberately not narrowed: it keeps accepting everything, so a
narrow prompt can only ever produce a payload the client can draw.
"""

from __future__ import annotations

from valuz_agent.modules.genui.protocol import (
    GenUIComponentScope,
    a2ui_instructions,
    build_a2ui_catalog,
    build_a2ui_prompt,
    normalize_component_scope,
)
from valuz_agent.modules.genui.tools import _PARAMS

SCOPES: tuple[GenUIComponentScope, ...] = ("all", "edition", "atoms")

# Components that exist only in one layer, used to prove a scope really dropped
# the other one rather than merely reordering the catalog.
_BLOCK_ONLY = "MarketIndexGrid"
_ATOM_ONLY = "SwitchGroup"


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


def test_aliases_land_on_the_layer_they_name():
    assert normalize_component_scope("blocks") == "edition"
    assert normalize_component_scope("Semantic") == "edition"
    assert normalize_component_scope("OpenUI") == "atoms"
    assert normalize_component_scope("primitives") == "atoms"


def test_the_tool_advertises_the_argument():
    components = _PARAMS["properties"]["components"]
    assert components["enum"] == list(SCOPES)
    assert components["default"] == "all"
    # Not required: an agent that never learned about the argument keeps working.
    assert "components" not in _PARAMS["required"]


def test_each_scope_offers_its_own_layer_and_drops_the_other():
    full = build_a2ui_prompt("revenue dashboard", None, "all")
    edition = build_a2ui_prompt("revenue dashboard", None, "edition")
    atoms = build_a2ui_prompt("revenue dashboard", None, "atoms")

    assert _BLOCK_ONLY in full and _ATOM_ONLY in full

    assert _BLOCK_ONLY in edition
    assert _ATOM_ONLY not in edition

    assert _ATOM_ONLY in atoms
    assert _BLOCK_ONLY not in atoms


def test_the_root_survives_every_scope():
    # Stack roots every document. A scope that dropped it would produce output
    # nothing can render — the one failure narrowing must never introduce.
    for scope in SCOPES:
        assert "Stack" in build_a2ui_prompt("chart", None, scope)


def test_narrowing_actually_costs_less():
    sizes = {scope: len(build_a2ui_prompt("chart", None, scope)) for scope in SCOPES}
    assert sizes["edition"] < sizes["all"]
    # The primitives alone are a small fraction of the full catalog — the reason
    # the argument is worth having at all. `edition` saves far less, because the
    # blocks are what the catalog is mostly made of; it earns its keep by
    # steering the model to the house vocabulary, not by saving tokens.
    assert sizes["atoms"] < sizes["all"] / 10


def test_instructions_never_recommend_a_component_the_scope_withheld():
    # The instructions name fallbacks to use when data has no chart series.
    # Naming one that is not in the catalog teaches the model to emit something
    # it was never shown.
    atoms = a2ui_instructions("atoms")
    assert "Valuz semantic components" not in atoms
    assert _BLOCK_ONLY not in atoms

    edition = a2ui_instructions("edition")
    assert _BLOCK_ONLY in edition
    assert "Table" not in edition


def test_the_a2ui_catalog_keeps_its_message_shape_in_every_scope():
    # The JSON examples are what make the protocol usable at all; they are also
    # full of braces, so a formatting mistake here silently truncates them.
    for scope in SCOPES:
        catalog = build_a2ui_catalog(scope)
        assert '{"id":"root","component":"Stack"' in catalog
        assert "{fallbacks}" not in catalog
