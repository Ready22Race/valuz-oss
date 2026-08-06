"""The backend half of dynamic block injection: ``ext.genui_blocks``.

The frontend registry (``registerBlocks``) is pinned by its own vitest suite;
these tests pin the prompt side — a registration reaches ``build_a2ui_catalog``
per call, collisions are refused by the same rules, and the scope split holds
against a real registration: ``atoms`` is what this repository ships,
``edition`` is what was installed from outside it, ``all`` is both.
"""

from __future__ import annotations

import pytest

from valuz_agent.modules.genui import protocol
from valuz_agent.ports.extensions import ext
from valuz_agent.ports.genui_blocks import GenUIBlockRegistry

FINANCE_ENTRIES = [
    ("SecurityHeader", "  - SecurityHeader(name: string) — security page header."),
    ("PriceChart", "  - PriceChart(candles: array) — candlestick chart."),
]


@pytest.fixture(autouse=True)
def fresh_registry(monkeypatch: pytest.MonkeyPatch) -> GenUIBlockRegistry:
    registry = GenUIBlockRegistry()
    monkeypatch.setattr(ext, "genui_blocks", registry)
    return registry


def _register(**kwargs: object) -> object:
    return ext.genui_blocks.register(
        kwargs.pop("layer", "distribution"),  # type: ignore[arg-type]
        group=kwargs.pop("group", "Finance"),  # type: ignore[arg-type]
        entries=kwargs.pop("entries", FINANCE_ENTRIES),  # type: ignore[arg-type]
        **kwargs,  # type: ignore[arg-type]
    )


def test_registered_blocks_reach_the_catalog_per_call() -> None:
    before = protocol.build_a2ui_catalog("all")
    assert "SecurityHeader" not in before

    result = _register(notes=("Every data-bearing block closes with source/asOf/basis.",))

    assert result.rejected == []  # type: ignore[attr-defined]
    after = protocol.build_a2ui_catalog("all")
    assert "- Finance blocks:" in after
    assert "SecurityHeader(name: string)" in after
    assert "source/asOf/basis" in after
    # `all` is the union: this repository's own set is still there.
    assert "MiniCard" in after


def test_the_scope_split_follows_where_a_component_came_from() -> None:
    protocol.build_a2ui_catalog("all")
    _register()

    edition = protocol.build_a2ui_catalog("edition")
    atoms = protocol.build_a2ui_catalog("atoms")

    # `edition` — only what was installed from outside, plus the root, which an
    # edition cannot supply for itself.
    assert "SecurityHeader" in edition
    assert "MiniCard" not in edition
    # BarChart was dropped from the offered vocabulary (GroupedBar is the
    # opinionated version); LineChart is the OpenUI sample that remains.
    assert "LineChart" not in edition
    assert "Stack" in edition
    # `atoms` — everything this repository ships, and nothing installed.
    assert "MiniCard" in atoms
    assert "LineChart" in atoms
    assert "SecurityHeader" not in atoms
    # The narrower scope has to actually cost less, or the argument is decoration.
    assert len(edition) < len(protocol.build_a2ui_catalog("all"))


def test_edition_scope_widens_when_nothing_is_installed() -> None:
    # Offering a root with no components under it produces no answer, not a
    # smaller one — so the scope widens rather than emptying.
    assert protocol.resolve_component_scope("edition") == "all"
    assert "MiniCard" in protocol.build_a2ui_catalog("edition")

    _register()

    assert protocol.resolve_component_scope("edition") == "edition"


def test_instructions_follow_the_resolved_scope() -> None:
    # A widened scope must not leave the instructions describing the narrow set
    # that was asked for.
    assert protocol.a2ui_instructions("edition") == protocol.a2ui_instructions("all")

    _register()

    narrowed = protocol.a2ui_instructions("edition")
    assert narrowed != protocol.a2ui_instructions("all")
    # A scope withholds consistently: no advice may name what it did not show.
    assert "MarketIndexGrid" not in narrowed


def test_collision_with_a_builtin_block_is_refused() -> None:
    protocol.build_a2ui_catalog("all")  # binds the baseline

    result = _register(entries=[("MiniCard", "  - MiniCard(label: string) — impostor.")])

    assert result.accepted == []  # type: ignore[attr-defined]
    assert result.rejected[0][0] == "MiniCard"  # type: ignore[attr-defined]


def test_collision_with_an_openui_component_is_refused() -> None:
    protocol.build_a2ui_catalog("all")

    result = _register(entries=[("Card", "  - Card(children: array) — impostor.")])

    assert result.rejected[0][0] == "Card"  # type: ignore[attr-defined]


def test_registration_before_baseline_bind_is_dropped_at_bind() -> None:
    # Overlay startup legally runs before this module assembles a prompt.
    result = _register(
        entries=[("MiniCard", "  - MiniCard(label) — impostor."), *FINANCE_ENTRIES]
    )
    assert result.rejected == []  # type: ignore[attr-defined]  # baseline not bound yet

    catalog = protocol.build_a2ui_catalog("all")  # binds and re-validates

    assert "impostor" not in catalog
    assert "SecurityHeader" in catalog
    assert ext.genui_blocks.rejected_at_bind()[0][0] == "MiniCard"


def test_earlier_layer_wins_across_layers_deterministically() -> None:
    protocol.build_a2ui_catalog("all")
    _register(layer="commercial", group="Commercial", entries=[FINANCE_ENTRIES[0]])

    result = _register()

    assert result.accepted == ["PriceChart"]  # type: ignore[attr-defined]
    assert result.rejected[0][0] == "SecurityHeader"  # type: ignore[attr-defined]


def test_reregistration_replaces_the_layer() -> None:
    protocol.build_a2ui_catalog("all")
    _register()
    _register(entries=[FINANCE_ENTRIES[0]])

    catalog = protocol.build_a2ui_catalog("all")

    assert "SecurityHeader" in catalog
    assert "PriceChart" not in catalog


def test_unregister_takes_the_group_out() -> None:
    protocol.build_a2ui_catalog("all")
    _register()
    ext.genui_blocks.unregister("distribution")

    assert "SecurityHeader" not in protocol.build_a2ui_catalog("all")
    # And the scope goes back to widening, since nothing is installed again.
    assert protocol.resolve_component_scope("edition") == "all"


def test_replace_suppresses_this_repository_in_every_scope() -> None:
    protocol.build_a2ui_catalog("all")
    result = _register(mode="replace")
    assert result.rejected == []  # type: ignore[attr-defined]

    for scope in ("all", "edition", "atoms"):
        catalog = protocol.build_a2ui_catalog(scope)  # type: ignore[arg-type]
        assert "SecurityHeader" in catalog, scope
        assert "MiniCard" not in catalog, scope
        assert "BarChart" not in catalog, scope
        # The root survives and is still described — a document with no
        # resolvable root renders nothing at all.
        assert "Stack" in catalog, scope
        # Nor may the fallback advice name what replace just removed.
        assert "MarketIndexGrid" not in catalog, scope


def test_replace_refuses_the_root_name_and_a_second_replacer() -> None:
    protocol.build_a2ui_catalog("all")

    rooted = _register(
        entries=[("Stack", "  - Stack(children) — impostor."), *FINANCE_ENTRIES],
        mode="replace",
    )
    assert rooted.rejected[0][0] == "Stack"  # type: ignore[attr-defined]
    assert set(rooted.accepted) == {"SecurityHeader", "PriceChart"}  # type: ignore[attr-defined]

    second = _register(
        layer="commercial",
        group="Commercial",
        entries=[("Other", "  - Other() — x.")],
        mode="replace",
    )
    assert second.accepted == []  # type: ignore[attr-defined]
    assert "already replaces" in second.rejected[0][1]  # type: ignore[attr-defined]


def test_duplicate_names_within_one_registration_are_refused() -> None:
    protocol.build_a2ui_catalog("all")

    result = _register(entries=[FINANCE_ENTRIES[0], FINANCE_ENTRIES[0]])

    assert result.accepted == ["SecurityHeader"]  # type: ignore[attr-defined]
    assert result.rejected[0][1] == "duplicate name within this registration"  # type: ignore[attr-defined]
