"""The flat child groups keep the language of the nested upstream groups.

The active ``hyperview.xsd`` overrides ``viewChildren`` and ``nonListViewChildren``
with a hand-written flat copy of the nested upstream groups. Every container that
uses them must keep the upstream language, so this compares the compiled models.
"""

import pytest
from django.test import override_settings

from dj_hyperview._schema_revision import ACTIVE_SCHEMA_ROOT
from dj_hyperview.schema import _compile_schema, _get_compiled_registry

HV = "https://hyperview.org/hyperview"
UPSTREAM_CHAIN = ACTIVE_SCHEMA_ROOT.parent / "compatibility" / "hyperview.xsd"
CONTAINERS = [
    "body",
    "header",
    "view",
    "form",
    "select-single",
    "select-multiple",
    "item",
    "section-title",
]


@pytest.fixture(scope="module")
def active():
    with override_settings(HYPERVIEW={}):
        return _get_compiled_registry()


@pytest.fixture(scope="module")
def upstream():
    return _compile_schema(UPSTREAM_CHAIN)


def _occurs(particle):
    return particle.min_occurs, particle.max_occurs  # maxOccurs unbounded is None


def _times(outer, inner):
    unbounded = outer[1] is None or inner[1] is None
    return outer[0] * inner[0], None if unbounded else outer[1] * inner[1]


def _matches(particle):
    if particle.name:
        return particle.name
    return (
        "wildcard",
        tuple(sorted(particle.namespace)),
        particle.process_contents,
        tuple(sorted(particle.not_namespace)),
        tuple(sorted(particle.not_qname)),
    )


def _leaves(group, outer=(1, 1)):
    """List what each element and wildcard matches and how often it can repeat.

    A leaf carries its own occurrence, the kind and occurrence of its group and its
    effective occurrence, the product over every group around it.
    """
    here = _times(outer, _occurs(group))
    leaves = []
    for particle in group:
        if hasattr(particle, "model"):
            leaves += _leaves(particle, here)
            continue
        own = _occurs(particle)
        leaf = (_matches(particle), own, group.model, _occurs(group), _times(here, own))
        leaves.append(leaf)
    return sorted(leaves, key=repr)


def _depth(particle):
    nested = (_depth(child) for child in particle if hasattr(child, "model"))
    return 1 + max(nested, default=0)


@pytest.mark.parametrize("name", CONTAINERS)
def test_flat_groups_keep_the_language_of_the_upstream_groups(name, active, upstream):
    flat = active.maps.elements[f"{{{HV}}}{name}"].type.content
    nested = upstream.maps.elements[f"{{{HV}}}{name}"].type.content
    assert _leaves(flat) == _leaves(nested)
    assert flat.model == nested.model
    assert _depth(flat) <= _depth(nested)
