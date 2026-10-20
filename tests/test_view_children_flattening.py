"""The flat child groups keep the content model of the nested upstream groups.

The active ``hyperview.xsd`` overrides ``viewChildren`` and ``nonListViewChildren``
with a hand-written flat copy of the nested upstream groups. Every container that
uses them must keep the upstream content model, so this compares the compiled models
as canonical trees. A tree keeps the order and the grouping of a sequence, which a
list of leaves does not: ``(A|B)*`` and ``A* B*`` have the same leaves.
"""

import pytest
import xmlschema
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
ONCE, STAR = (1, 1), (0, None)


@pytest.fixture(scope="module")
def active():
    with override_settings(HYPERVIEW={}):
        return _get_compiled_registry()


@pytest.fixture(scope="module")
def upstream():
    return _compile_schema(UPSTREAM_CHAIN)


def _occurs(particle):
    return particle.min_occurs, particle.max_occurs  # maxOccurs unbounded is None


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


def _splices(child, outer):
    return child[0] == "choice" and (child[1] == ONCE or child[1] == outer == STAR)


def _canonical(particle):
    """Return a tree of the content model: what is matched, how often, in which group.

    An element or wildcard is ``("particle", matches, occurs)`` and a group is
    ``(model, occurs, children)``. Only rewrites that keep the accepted language apply:

    - a sequence keeps its children in order, a choice sorts them (a set);
    - a group at 1..1 around a single particle is that particle;
    - a choice at 1..1 in a choice, or at 0..unbounded in a choice at 0..unbounded,
      gives its alternatives to the outer choice.
    """
    occurs = _occurs(particle)
    if not hasattr(particle, "model"):
        return "particle", _matches(particle), occurs
    children = [_canonical(child) for child in particle]
    if particle.model == "choice":
        children = [
            leaf
            for child in children
            for leaf in (child[2] if _splices(child, occurs) else [child])
        ]
        children.sort(key=repr)
    if len(children) == 1 and occurs == ONCE:
        return children[0]
    return particle.model, occurs, tuple(children)


def _depth(particle):
    nested = (_depth(child) for child in particle if hasattr(child, "model"))
    return 1 + max(nested, default=0)


@pytest.mark.parametrize("name", CONTAINERS)
def test_flat_groups_keep_the_content_model_of_the_upstream_groups(
    name, active, upstream
):
    flat = active.maps.elements[f"{{{HV}}}{name}"].type.content
    nested = upstream.maps.elements[f"{{{HV}}}{name}"].type.content
    assert _canonical(flat) == _canonical(nested)
    assert flat.model == nested.model
    assert _depth(flat) <= _depth(nested)


def _star(*members):
    """A choice at 0..unbounded; a member is an element name or a nested group."""
    refs = "".join(
        member if member.startswith("<") else f'<xs:element ref="{member}"/>'
        for member in members
    )
    return f'<xs:choice minOccurs="0" maxOccurs="unbounded">{refs}</xs:choice>'


def _model(content):
    schema = (
        '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'
        '<xs:element name="a"/><xs:element name="b"/><xs:element name="r">'
        f"<xs:complexType><xs:sequence>{content}</xs:sequence></xs:complexType>"
        "</xs:element></xs:schema>"
    )
    return xmlschema.XMLSchema11(schema).elements["r"].type.content


@pytest.mark.parametrize(
    ("left", "right", "same"),
    [
        (_star("b", "a"), _star("a", "b"), True),  # order inside a choice
        (_star(_star("a"), "b"), _star("a", "b"), True),  # nested 0..unbounded choice
        (_star("a") + _star("b"), _star("a", "b"), False),  # A* B* vs (A|B)*
        (_star("a") + _star("b"), _star("b") + _star("a"), False),  # sequence order
        (_star("a"), _star("a", "b"), False),  # B dropped
    ],
)
def test_the_canonical_tree_keeps_what_a_list_of_leaves_loses(left, right, same):
    assert (_canonical(_model(left)) == _canonical(_model(right))) is same
