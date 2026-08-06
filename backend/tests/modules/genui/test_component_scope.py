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
    build_prompt_for_protocol,
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
    for protocol in ("openui", "a2ui"):
        full = build_prompt_for_protocol(protocol, "revenue dashboard", None, "all")
        edition = build_prompt_for_protocol(protocol, "revenue dashboard", None, "edition")
        atoms = build_prompt_for_protocol(protocol, "revenue dashboard", None, "atoms")

        assert _BLOCK_ONLY in full and _ATOM_ONLY in full

        assert _BLOCK_ONLY in edition, protocol
        assert _ATOM_ONLY not in edition, protocol

        assert _ATOM_ONLY in atoms, protocol
        assert _BLOCK_ONLY not in atoms, protocol


def test_the_root_survives_every_scope():
    # Stack roots every document. A scope that dropped it would produce output
    # nothing can render — the one failure narrowing must never introduce.
    for scope in SCOPES:
        for protocol in ("openui", "a2ui"):
            assert "Stack" in build_prompt_for_protocol(protocol, "chart", None, scope)


def test_narrowing_actually_costs_less():
    for protocol in ("openui", "a2ui"):
        sizes = {
            scope: len(build_prompt_for_protocol(protocol, "chart", None, scope))
            for scope in SCOPES
        }
        assert sizes["atoms"] < sizes["all"], protocol
        assert sizes["edition"] <= sizes["all"], protocol
    # The primitives alone are a fraction of the full catalog — the reason the
    # argument is worth having at all.
    assert len(build_prompt_for_protocol("openui", "chart", None, "atoms")) < len(
        build_prompt_for_protocol("openui", "chart", None, "all")
    ) / 2


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
