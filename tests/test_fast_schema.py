"""The fast XSD registry changes the work done, never the verdict.

Registries that validate rendered documents are built with an xmlschema subclass
that precomputes per attribute group what every element instance asks for again,
selects generated behavior types by dictionary and remembers attribute values that
were already accepted. The tests compare it with a plain XMLSchema11 registry
through the package's own validation path, and pin the safety rules of the memo.
"""

from __future__ import annotations

import copy
import random
import sys
import threading
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
import xmlschema
from django.test import override_settings
from lxml import etree
from xmlschema.validators.attributes import XsdAttributeGroup
from xmlschema.validators.elements import Xsd11Element, XsdAlternative, XsdElement
from xmlschema.validators.validation import ValidationContext

from dj_hyperview import schema
from dj_hyperview._schema_extensions import _EMPTY_EXTENSIONS
from dj_hyperview.conf import get_settings
from dj_hyperview.exceptions import TemplateValidationError

HV = "https://hyperview.org/hyperview"
VIEW = f"{{{HV}}}view"
BEHAVIOR = f"{{{HV}}}behavior"
CONTRACT = Path(__file__).parent / "contracts" / "hyperview" / "0.110.0"
EXTENSIONS = {
    "BEHAVIORS": {
        "show-snackbar": {
            "ATTRIBUTES": {
                "message": {"TYPE": "string", "REQUIRED": True},
                "tone": {"TYPE": "string", "ENUM": ["success", "error"]},
            }
        },
        "store-token": {"ATTRIBUTES": {"token": {"TYPE": "string"}}},
        "notify": {
            "ATTRIBUTES": {
                "count": {"TYPE": "integer"},
                "silent": {"TYPE": "boolean"},
            }
        },
    },
    "ELEMENT_ATTRIBUTES": {
        "image": {"variant": {"TYPE": "string", "ENUM": ["face", "fingerprint"]}}
    },
}
CUSTOM_ACTIONS = ("show-snackbar", "store-token", "notify")
CUSTOM_ATTRIBUTES = ("message", "tone", "token", "count", "silent", "variant")
SEEDS = (
    f'<doc xmlns="{HV}"><screen id="main"><styles><style id="title" fontSize="20" '
    f'color="#333333"/></styles><body style="Body"><header><text>Title</text>'
    f'</header><view id="row" style="Row" hide="false" href="/next" action="push" '
    f'verb="get" trigger="press"><text style="title">Hello</text><image '
    f'style="Logo" source="/logo.png"/></view><view id="load" href="/data" '
    f'trigger="load" action="replace" target="row" delay="100"/></body></screen>'
    f"</doc>",
    f'<form xmlns="{HV}" id="profile"><text-field id="email" name="email" '
    f'value="a@b.test" placeholder="Email" keyboard-type="email-address"/>'
    f'<select-single name="plan"><option value="a" selected="true"><text>A</text>'
    f'</option><option value="b"><text>B</text></option></select-single>'
    f'<switch name="news" value="on"/><view id="go" href="/save" action="replace" '
    f'target="out" verb="post" trigger="press"><text>Save</text></view>'
    f'<view id="out"/></form>',
    f'<doc xmlns="{HV}"><screen><body><list id="l"><items><item key="1"><text>x'
    f'</text></item><item key="2"><text>y</text></item></items></list><section-list '
    f'id="s"><section><section-title><text>T</text></section-title><items><item '
    f'key="3"><text>z</text></item></items></section></section-list></body>'
    f"</screen></doc>",
    f'<view xmlns="{HV}" id="v"><behavior trigger="load" action="reload"/><behavior '
    f'trigger="press" action="alert" xmlns:alert="https://hyperview.org/hyperview-'
    f'alert" alert:title="T" alert:message="M"/><behavior trigger="press" '
    f'action="toggle" target="v"/></view>',
)
EXTENDED_SEEDS = (
    f'<view xmlns="{HV}" id="e"><behavior trigger="load" action="show-snackbar" '
    f'message="Hi" tone="error"/><behavior trigger="press" action="store-token" '
    f'token="t"/><behavior trigger="press" action="notify" count="3" silent="true"/>'
    f'<behavior trigger="load" action="reload"/><image style="Logo" source="/a.png" '
    f'variant="face"/></view>',
)
GENERIC_VALUES = (
    "",
    " ",
    "0",
    "1",
    "-1",
    "2.5",
    "true",
    "false",
    "none",
    "10",
    "#ff0000",
    "50%",
    "x y",
    "ÄÖ",
    "٣",
)
ACTIONS = (*CUSTOM_ACTIONS, "reload", "push", "unknown", "", "Show-Snackbar", " notify")
EX = "urn:dj-hyperview:test-extra"
EXTRA_SCHEMA = f"""<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"
    xmlns:ex="{EX}" targetNamespace="{EX}" elementFormDefault="qualified"
    version="1.1">
  <xs:simpleType name="level"><xs:restriction base="xs:string">
    <xs:enumeration value="low"/><xs:enumeration value="high"/>
  </xs:restriction></xs:simpleType>
  <xs:simpleType name="percent"><xs:restriction base="xs:integer">
    <xs:minInclusive value="0"/><xs:maxInclusive value="100"/>
  </xs:restriction></xs:simpleType>
  <xs:element name="card-list"><xs:complexType><xs:sequence>
    <xs:element name="card" maxOccurs="unbounded"><xs:complexType>
      <xs:attribute name="id" type="xs:ID"/>
      <xs:attribute name="ref" type="xs:IDREF"/>
      <xs:attribute name="level" type="ex:level"/>
      <xs:attribute name="percent" type="ex:percent"/>
      <xs:attribute name="rank" type="xs:integer" fixed="1"/>
      <xs:attribute name="weight" type="xs:integer" default="5"/>
      <xs:attribute name="title" type="xs:string" use="required"/>
    </xs:complexType></xs:element>
  </xs:sequence></xs:complexType></xs:element>
</xs:schema>"""
EXTRA_SEEDS = (
    f'<ex:card-list xmlns:ex="{EX}"><ex:card id="a" title="A" level="low" '
    f'percent="10"/><ex:card id="b" ref="a" title="B" level="high" percent="100" '
    f'rank="1" weight="7"/><ex:card id="c" title="C" percent="0" weight="5"/>'
    f"</ex:card-list>",
    f'<ex:card-list xmlns:ex="{EX}"><ex:card title="T" rank="01"/></ex:card-list>',
    f'<ex:card-list xmlns:ex="{EX}"><ex:card id="x1" ref="x2" title=" " percent="50"'
    f' weight="-3"/><ex:card id="x2" ref="x1" title="" level="low"/></ex:card-list>',
)


def build(extensions, *, plain, state=()):
    """Compile one registry through the package, with or without the fast class."""
    with pytest.MonkeyPatch.context() as patch:
        if plain:
            patch.setattr(schema, "_registry_class", lambda module: module.XMLSchema11)
        return schema._cached_registry.__wrapped__(state, extensions)


def verdict(root, registry):
    try:
        schema._validate_schema_root(root, registry)
    except TemplateValidationError as error:
        return (error.code, error.message, error.line, error.column)
    return None


def pools(registry, namespaces=(HV,)):
    names, values, tags = set(), set(GENERIC_VALUES), set()
    prefixes = tuple(f"{{{namespace}}}" for namespace in namespaces)

    def collect(attributes):
        for name, attribute in attributes.items():
            if name:
                names.add(name)
            values.update(map(str, getattr(attribute.type, "enumeration", None) or ()))

    for element in registry.maps.iter_components(XsdElement):
        if (element.name or "").startswith(prefixes):
            tags.add(element.name)
            collect({} if element.type.is_simple() else element.type.attributes)
    # The attributes of registered behaviors belong to generated types, which no
    # element names as its own.
    for qname, xsd_type in registry.maps.types.items():
        if qname.startswith(prefixes) and not xsd_type.is_simple():
            collect(xsd_type.attributes)
    return sorted(names), sorted(values), sorted(tags)


def mutate(root, rng, names, values, tags, actions):
    nodes = [node for node in root.iter() if isinstance(node.tag, str)]
    node = rng.choice(nodes)
    kind = rng.choice(
        ("set", "set", "set", "delete", "retag", "shuffle", "duplicate", "remove")
        + ("namespace", "action", "action", "id")
    )
    if kind == "set":
        node.set(rng.choice(names), rng.choice(values))
    elif kind == "delete" and node.attrib:
        del node.attrib[rng.choice(sorted(node.attrib))]
    elif kind == "retag":
        node.tag = rng.choice(tags)
    elif kind == "shuffle" and len(node) > 1:
        for child in rng.sample(list(node), len(node)):
            node.append(child)
    elif kind == "duplicate" and len(node):
        node.append(copy.deepcopy(rng.choice(list(node))))
    elif kind == "remove" and len(node):
        node.remove(rng.choice(list(node)))
    elif kind == "namespace":
        local = etree.QName(node).localname
        node.tag = rng.choice((local, f"{{urn:other}}{local}"))
    elif kind == "action":
        behaviors = [n for n in nodes if n.tag == BEHAVIOR] or [node]
        target = rng.choice(behaviors)
        if rng.random() < 0.2:
            target.attrib.pop("action", None)
        else:
            target.set("action", rng.choice(actions))
    elif kind == "id":
        ids = [n for n in nodes if n.get("id") is not None]
        if len(ids) > 1:
            first, second = rng.sample(ids, 2)
            second.set("id", first.get("id"))


def parse(data):
    return etree.fromstring(data, parser=etree.XMLParser(resolve_entities=False))


def corpus(seeds, registry, count, *, seed, namespaces=(HV,), fixtures=True):
    names, values, tags = pools(registry, namespaces)
    actions = (*ACTIONS, *GENERIC_VALUES)
    rng = random.Random(seed)
    trees = [parse(document) for document in seeds]
    if fixtures:
        trees += [parse(path.read_bytes()) for path in sorted(CONTRACT.glob("*.xml"))]
    documents = [etree.tostring(tree, pretty_print=True) for tree in trees]
    for _ in range(count):
        root = copy.deepcopy(rng.choice(trees))
        for _step in range(rng.choice((1, 1, 2, 3))):
            mutate(root, rng, names, values, tags, actions)
        documents.append(etree.tostring(root, pretty_print=True))
    return [parse(document) for document in documents]


@pytest.fixture(scope="module")
def extensions():
    with override_settings(HYPERVIEW={"SCHEMA_EXTENSIONS": EXTENSIONS}):
        return get_settings().schema_extensions


@pytest.fixture(scope="module")
def extra_state(tmp_path_factory):
    path = tmp_path_factory.mktemp("extra") / "cards.xsd"
    path.write_text(EXTRA_SCHEMA, encoding="utf-8")
    return schema._registry_state([path])


@pytest.fixture(scope="module")
def registries(extensions, extra_state):
    return {
        "default": (
            build(_EMPTY_EXTENSIONS, plain=True),
            build(_EMPTY_EXTENSIONS, plain=False),
        ),
        "extended": (build(extensions, plain=True), build(extensions, plain=False)),
        "extra": (
            build(_EMPTY_EXTENSIONS, plain=True, state=extra_state),
            build(_EMPTY_EXTENSIONS, plain=False, state=extra_state),
        ),
    }


@pytest.mark.parametrize("profile", ["default", "extended", "extra"])
def test_fast_registry_gives_the_verdicts_of_plain_xmlschema(registries, profile):
    plain, fast = registries[profile]
    if profile == "extra":
        # Only the extra schema's own declarations, which the fast classes also see.
        documents = corpus(
            EXTRA_SEEDS, plain, 800, seed=20261019, namespaces=(EX,), fixtures=False
        )
    else:
        seeds = SEEDS + (EXTENDED_SEEDS if profile == "extended" else ())
        documents = corpus(seeds, plain, 1200, seed=20261019)

    expected = [verdict(document, plain) for document in documents]

    # The second pass runs with the memo filled by the first one.
    assert [verdict(document, fast) for document in documents] == expected
    assert [verdict(document, fast) for document in documents] == expected
    rejected = {outcome for outcome in expected if outcome is not None}
    assert expected.count(None) >= 40
    assert len(rejected) >= (15 if profile == "extra" else 25)
    assert {outcome[0] for outcome in rejected} == {"schema"}
    messages = [outcome[1] for outcome in rejected]
    if profile == "extended":
        assert any("behavior" in message for message in messages)
        assert set(CUSTOM_ATTRIBUTES) <= set(pools(plain)[0])
    if profile == "extra":
        assert any('attribute "id" on element "card"' in m for m in messages)
        assert any("required attribute" in message for message in messages)
        card = next(iter(fast.maps.elements[f"{{{EX}}}card-list"].type.content))
        memos = {name: a._memo for name, a in card.type.attributes.items()}
        # The corpus filled the memo of the types that allow one and left the
        # others (ID, IDREF, a fixed value, a default, a plain string) without.
        assert {name for name, memo in memos.items() if memo} == {"level", "percent"}
        assert {name for name, memo in memos.items() if memo is None} == {
            "id",
            "ref",
            "rank",
            "weight",
            "title",
        }


def test_documents_of_the_contract_fixtures_are_valid_for_both_registries(registries):
    for plain, fast in registries.values():
        for path in sorted(CONTRACT.glob("*.xml")):
            root = parse(path.read_bytes())
            assert verdict(root, plain) is None
            assert verdict(root, fast) is None


def _fast_schema():
    from dj_hyperview import _fast_schema

    return _fast_schema


@pytest.mark.parametrize("profile", ["default", "extended", "extra"])
def test_registries_use_the_fast_components_with_the_locked_xmlschema(
    registries, profile
):
    fast_schema = _fast_schema()
    registry = registries[profile][1]

    assert isinstance(registry, fast_schema.FastXMLSchema11)
    attributes = registry.maps.elements[VIEW].type.attributes
    assert isinstance(attributes, fast_schema.FastAttributeGroup)
    assert any(isinstance(a, fast_schema.FastAttribute) for a in attributes.values())
    assert isinstance(registry.maps.elements[BEHAVIOR], fast_schema.FastElement)


def test_the_locked_xmlschema_is_a_tested_release():
    # A new locked release has to pass the differential tests before this changes.
    fast_schema = _fast_schema()

    assert schema._registry_class(xmlschema) is fast_schema.FastXMLSchema11
    assert fast_schema.FastXMLSchema11.__base__ is xmlschema.XMLSchema11


@pytest.mark.parametrize("version", ["4.3.2", "4.3.9", "4.3.99", "4.3.2.post1"])
def test_registry_class_uses_the_fast_class_from_the_tested_release_on(
    monkeypatch, version
):
    monkeypatch.setattr(xmlschema, "__version__", version)

    assert schema._registry_class(xmlschema) is _fast_schema().FastXMLSchema11


@pytest.mark.parametrize(
    "version",
    [
        # Releases of the 4.3 series before the tested one, and its candidates.
        "4.3.0",
        "4.3.1",
        "4.3.0rc1",
        "4.3.2rc1",
        # Parts that are missing or not numbers.
        "4.3",
        "4.3.x",
        "4",
        "",
        # Other series.
        "4.2.9",
        "4.4.0",
        "4.4.0.dev0",
        "4.30.0",
        "4.13.0",
        "5.0",
        "5.0.0",
        "3.3.0",
    ],
)
def test_registry_class_keeps_plain_xmlschema_for_an_untested_release(
    monkeypatch, version
):
    monkeypatch.setattr(xmlschema, "__version__", version)

    assert schema._registry_class(xmlschema) is xmlschema.XMLSchema11


def test_an_untested_release_compiles_the_registry_with_plain_xmlschema(monkeypatch):
    monkeypatch.setattr(xmlschema, "__version__", "4.4.0")

    registry = schema._cached_registry.__wrapped__((), _EMPTY_EXTENSIONS)

    assert type(registry) is xmlschema.XMLSchema11
    assert verdict(parse(SEEDS[2].encode()), registry) is None


def test_registry_class_keeps_plain_xmlschema_when_the_release_is_unknown(monkeypatch):
    monkeypatch.delattr(xmlschema, "__version__")

    assert schema._registry_class(xmlschema) is xmlschema.XMLSchema11


def test_registry_class_keeps_a_substituted_xmlschema_module_unchanged():
    def compiler(*args, **kwargs):
        raise AssertionError

    assert schema._registry_class(SimpleNamespace(XMLSchema11=compiler)) is compiler
    tested = SimpleNamespace(XMLSchema11=compiler, __version__=xmlschema.__version__)
    assert schema._registry_class(tested) is compiler


@pytest.mark.parametrize(
    "breakage", ["missing module", "incompatible builders", "unexpected failure"]
)
def test_registry_class_falls_back_to_plain_xmlschema(monkeypatch, breakage):
    monkeypatch.delitem(sys.modules, "dj_hyperview._fast_schema", raising=False)
    if breakage == "missing module":
        monkeypatch.setitem(sys.modules, "xmlschema.validators.builders", None)
    else:
        failure = TypeError if breakage == "incompatible builders" else RuntimeError

        class Incompatible:
            def __init__(self, *args, **kwargs):
                raise failure("unsupported builders")

        monkeypatch.setattr("xmlschema.validators.builders.XsdBuilders", Incompatible)

    assert schema._registry_class(xmlschema) is xmlschema.XMLSchema11

    registry = schema._cached_registry.__wrapped__((), _EMPTY_EXTENSIONS)
    assert type(registry) is xmlschema.XMLSchema11
    valid = parse(SEEDS[2].encode())
    assert verdict(valid, registry) is None
    invalid = parse(f'<view xmlns="{HV}" bogus="1"/>'.encode())
    assert verdict(invalid, registry) == (
        "schema",
        'attribute "bogus" is not allowed on element "view"',
        1,
        None,
    )


def assert_groups_precompute_what_the_base_class_iterates(groups):
    for group in groups:
        assert list(group.iter_required()) == list(
            XsdAttributeGroup.iter_required(group)
        )
        for use_defaults in (True, False):
            assert list(group.iter_value_constraints(use_defaults)) == list(
                XsdAttributeGroup.iter_value_constraints(group, use_defaults)
            )


def test_attribute_groups_precompute_what_the_base_class_iterates(registries):
    fast_schema = _fast_schema()
    registry = registries["extended"][1]
    groups = list(registry.maps.iter_components(fast_schema.FastAttributeGroup))

    assert len(groups) > 20
    assert any(list(group.iter_required()) for group in groups)
    assert_groups_precompute_what_the_base_class_iterates(groups)


def test_attribute_groups_precompute_fixed_and_default_values_separately():
    group = attributes_of(_fast_schema().FastXMLSchema11(SAFETY))

    assert dict(group.iter_value_constraints(True)) == {"fixed": "1", "default": "2"}
    assert dict(group.iter_value_constraints(False)) == {"fixed": "1"}
    assert dict(group.iter_value_constraints()) == dict(
        group.iter_value_constraints(True)
    )
    assert_groups_precompute_what_the_base_class_iterates([group])


def alternative_declarations(registry):
    return [
        component
        for component in registry.maps.iter_components(XsdElement)
        if component.alternatives and component.name == BEHAVIOR
    ]


@pytest.mark.parametrize("action", [*ACTIONS, None])
def test_generated_action_alternatives_select_the_type_xpath_selects(
    registries, monkeypatch, action
):
    registry = registries["extended"][1]
    declarations = alternative_declarations(registry)
    assert declarations
    element = etree.Element(BEHAVIOR)
    if action is not None:
        element.set("action", action)
    expected = [
        Xsd11Element.get_alternative_type(declaration, element)
        for declaration in declarations
    ]

    def forbidden(self, elem):
        raise AssertionError("XPath was evaluated")

    monkeypatch.setattr(XsdAlternative, "test", forbidden)

    assert [d.get_alternative_type(element) for d in declarations] == expected
    selected = expected[0].name or ""
    if action in CUSTOM_ACTIONS:
        assert selected.startswith(f"{{{HV}}}_djhvRegisteredBehavior")
    else:
        assert selected == f"{{{HV}}}behavior"


def test_alternatives_inherited_from_ancestors_keep_the_xpath_path(registries):
    registry = registries["extended"][1]
    declaration = alternative_declarations(registry)[0]
    element = etree.Element(BEHAVIOR, action="notify")

    selected = declaration.get_alternative_type(element, {"action": "store-token"})

    assert selected is Xsd11Element.get_alternative_type(
        declaration, element, {"action": "store-token"}
    )


def test_attributes_inherited_from_ancestors_reach_the_alternative_tests():
    registry = _fast_schema().FastXMLSchema11(ALTERNATIVES)
    declaration = registry.elements["generated"]
    assert declaration._types_by_action is not None

    for own, inherited in (
        ({}, {"action": "b"}),
        ({}, {"action": "c"}),
        ({}, {"action": "other"}),
        ({"action": "c"}, {"action": "b"}),
        ({"action": "d"}, {"action": "b"}),
        ({"kind": "b"}, {"action": "b"}),
    ):
        element = etree.Element("generated", own)
        selected = declaration.get_alternative_type(element, inherited)

        assert selected is Xsd11Element.get_alternative_type(
            declaration, element, inherited
        )
    # No action of its own: only the inherited one can select the alternative.
    bare = etree.Element("generated")
    assert declaration.get_alternative_type(bare, {"action": "b"}).name == "B"
    assert declaration.get_alternative_type(bare).name == "D"


ALTERNATIVES = """<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" version="1.1">
  <xs:complexType name="A">
    <xs:attribute name="action"/><xs:attribute name="kind"/><xs:attribute name="x"/>
  </xs:complexType>
  <xs:complexType name="B">
    <xs:complexContent><xs:extension base="A">
      <xs:attribute name="extra" type="xs:integer"/>
    </xs:extension></xs:complexContent>
  </xs:complexType>
  <xs:complexType name="C">
    <xs:complexContent><xs:extension base="A"/></xs:complexContent>
  </xs:complexType>
  <xs:complexType name="D">
    <xs:complexContent><xs:extension base="A"/></xs:complexContent>
  </xs:complexType>
  <xs:element name="generated" type="A">
    <xs:alternative test="@action = 'b'" type="B"/>
    <xs:alternative test="@action = 'c'" type="C"/>
    <xs:alternative test="@action = 'b'" type="C"/>
    <xs:alternative type="D"/>
  </xs:element>
  <xs:element name="empty" type="A">
    <xs:alternative test="@action = ''" type="B"/>
    <xs:alternative type="D"/>
  </xs:element>
  <xs:element name="unconditional" type="A">
    <xs:alternative test="@action = 'b'" type="B"/>
  </xs:element>
  <xs:element name="other-attribute" type="A">
    <xs:alternative test="@kind = 'b'" type="B"/>
    <xs:alternative type="D"/>
  </xs:element>
  <xs:element name="mixed" type="A">
    <xs:alternative test="@action = 'b'" type="B"/>
    <xs:alternative test="@kind = 'c'" type="C"/>
    <xs:alternative type="D"/>
  </xs:element>
  <xs:element name="compound" type="A">
    <xs:alternative test="@action = 'b' or @action = 'c'" type="B"/>
    <xs:alternative type="D"/>
  </xs:element>
  <xs:element name="numeric" type="A">
    <xs:alternative test="@action = 5" type="B"/>
    <xs:alternative type="D"/>
  </xs:element>
  <xs:element name="spaced" type="A">
    <xs:alternative test="@action='b'" type="B"/>
    <xs:alternative type="D"/>
  </xs:element>
</xs:schema>"""


@pytest.mark.parametrize(
    ("name", "indexed"),
    [
        ("generated", True),
        ("empty", True),
        ("unconditional", True),
        ("other-attribute", False),
        ("mixed", False),
        ("compound", False),
        ("numeric", False),
        ("spaced", False),
    ],
)
def test_only_the_generated_alternative_form_is_selected_by_dictionary(name, indexed):
    registry = _fast_schema().FastXMLSchema11(ALTERNATIVES)
    declaration = registry.elements[name]

    assert (declaration._types_by_action is not None) is indexed
    for attributes in (
        {},
        {"action": "b"},
        {"action": "c"},
        {"action": "5"},
        {"action": "d"},
        {"action": ""},
        {"kind": "b"},
        {"kind": "c"},
        {"action": "c", "kind": "b"},
    ):
        element = etree.Element(name, attributes)
        assert declaration.get_alternative_type(
            element
        ) is Xsd11Element.get_alternative_type(declaration, element)


def test_encoding_selects_the_alternative_type_like_plain_xmlschema():
    plain = xmlschema.XMLSchema11(ALTERNATIVES)
    fast = _fast_schema().FastXMLSchema11(ALTERNATIVES)

    for data in ({"@action": "b", "@extra": 7}, {"@action": "c"}, {"@kind": "x"}):
        expected = ET.tostring(plain.encode(data, path="generated"))
        assert ET.tostring(fast.encode(data, path="generated")) == expected, data
    # Only the type selected for action "b" declares the attribute "extra".
    encoded = fast.encode({"@action": "b", "@extra": 7}, path="generated")
    assert encoded.get("extra") == "7"
    for data in ({"@extra": 7}, {"@action": "c", "@extra": 7}):
        for registry in (plain, fast):
            with pytest.raises(xmlschema.XMLSchemaValidationError):
                registry.encode(data, path="generated")


SAFETY = """<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" version="1.1">
  <xs:notation name="n1" public="n1"/>
  <xs:simpleType name="notation"><xs:restriction base="xs:NOTATION">
    <xs:enumeration value="n1"/></xs:restriction></xs:simpleType>
  <xs:simpleType name="derivedId"><xs:restriction base="xs:ID">
    <xs:pattern value="d[0-9]+"/></xs:restriction></xs:simpleType>
  <xs:simpleType name="idList"><xs:list itemType="xs:IDREF"/></xs:simpleType>
  <xs:simpleType name="unionWithName">
    <xs:union memberTypes="xs:integer xs:QName"/>
  </xs:simpleType>
  <xs:simpleType name="unionOfNumbers">
    <xs:union memberTypes="xs:integer xs:boolean"/>
  </xs:simpleType>
  <xs:simpleType name="digits"><xs:restriction>
    <xs:simpleType><xs:union memberTypes="xs:integer xs:decimal"/></xs:simpleType>
    <xs:pattern value="[0-9]+"/></xs:restriction></xs:simpleType>
  <xs:element name="root"><xs:complexType><xs:sequence>
    <xs:element name="item" minOccurs="0" maxOccurs="unbounded"><xs:complexType>
      <xs:attribute name="number" type="xs:integer"/>
      <xs:attribute name="flag" type="xs:boolean"/>
      <xs:attribute name="mixed" type="unionOfNumbers"/>
      <xs:attribute name="digits" type="digits"/>
      <xs:attribute name="id" type="xs:ID"/>
      <xs:attribute name="derived" type="derivedId"/>
      <xs:attribute name="ref" type="xs:IDREF"/>
      <xs:attribute name="refs" type="xs:IDREFS"/>
      <xs:attribute name="list" type="idList"/>
      <xs:attribute name="entity" type="xs:ENTITY"/>
      <xs:attribute name="entities" type="xs:ENTITIES"/>
      <xs:attribute name="name" type="xs:QName"/>
      <xs:attribute name="either" type="unionWithName"/>
      <xs:attribute name="notation" type="notation"/>
      <xs:attribute name="fixed" type="xs:integer" fixed="1"/>
      <xs:attribute name="default" type="xs:integer" default="2"/>
      <xs:attribute name="string" type="xs:string"/>
      <xs:attribute name="token" type="xs:token"/>
      <xs:attribute name="normalized" type="xs:normalizedString"/>
    </xs:complexType></xs:element>
  </xs:sequence></xs:complexType>
  <xs:unique name="uniqueNumber">
    <xs:selector xpath="item"/><xs:field xpath="@number"/>
  </xs:unique></xs:element>
</xs:schema>"""
MEMOIZED = {"number", "flag", "mixed", "digits"}


def attributes_of(registry):
    item = next(iter(registry.elements["root"].type.content))
    return item.type.attributes


def document(**attributes):
    rendered = " ".join(f'{name}="{value}"' for name, value in attributes.items())
    return f"<root><item {rendered}/></root>"


@pytest.fixture
def fast():
    return _fast_schema().FastXMLSchema11(SAFETY)


def test_only_context_free_attribute_types_are_memoized(fast):
    attributes = attributes_of(fast)

    assert {name for name, a in attributes.items() if a._memo is not None} == MEMOIZED


def test_invalid_values_are_never_memoized(fast):
    number = attributes_of(fast)["number"]

    for value in ("x", "1.5", "", "1e3", "9" * 3 + "z"):
        assert not fast.is_valid(document(number=value))
        assert [e for e in fast.iter_errors(document(number=value))]
        with pytest.raises(xmlschema.XMLSchemaValidationError):
            fast.validate(document(number=value))
    assert number._memo == set()

    fast.validate(document(number="7"))
    assert number._memo == {"7"}
    with pytest.raises(xmlschema.XMLSchemaValidationError):
        fast.validate(document(number="x"))
    assert number._memo == {"7"}


def test_a_lax_pass_memoizes_only_the_values_that_passed(fast):
    two_items = "<root><item number='1'/><item number='x'/><item number='2'/></root>"

    assert len(list(fast.iter_errors(two_items))) == 1

    assert attributes_of(fast)["number"]._memo == {"1", "2"}


def test_id_values_are_validated_every_time(fast):
    once = '<root><item id="a"/></root>'
    twice = '<root><item id="a"/><item id="a"/></root>'

    for _ in range(3):
        fast.validate(once)
        with pytest.raises(xmlschema.XMLSchemaValidationError):
            fast.validate(twice)
    assert attributes_of(fast)["id"]._memo is None


def test_identity_constraints_still_see_a_remembered_value(fast):
    duplicated = "<root><item number='7'/><item number='7'/></root>"
    fast.validate(document(number="7"))
    assert "7" in attributes_of(fast)["number"]._memo

    for _ in range(2):
        with pytest.raises(xmlschema.XMLSchemaValidationError):
            fast.validate(duplicated)


def test_a_skipped_validation_memoizes_nothing(fast):
    number = attributes_of(fast)["number"]

    fast.validate(document(number="x"), validation_hook=lambda elem, xsd: "skip")

    assert number._memo == set()
    with pytest.raises(xmlschema.XMLSchemaValidationError):
        fast.validate(document(number="x"))


def test_values_are_not_memoized_while_patterns_are_pushed(fast):
    number = attributes_of(fast)["number"]
    context = ValidationContext(xmlschema.XMLResource("<root/>"))
    context.patterns = []

    number.raw_decode("7", "strict", context)

    assert number._memo == set()
    number._memo.add("x")
    with pytest.raises(xmlschema.XMLSchemaValidationError):
        number.raw_decode("x", "strict", context)


def test_attribute_values_that_are_not_text_are_left_to_xmlschema(fast):
    # ElementTree trees can carry any object as a value; lxml, which the package
    # validates, cannot. xmlschema accepts 5, True and b"5" for an integer.
    plain = xmlschema.XMLSchema11(SAFETY)
    number = attributes_of(fast)["number"]

    for value, valid in (
        (5, True),
        (True, True),
        (b"5", True),
        (5.0, False),
        (None, False),
    ):
        root = ET.Element("root")
        ET.SubElement(root, "item", {"number": value})
        for _ in range(2):
            assert plain.is_valid(root) is valid, value
            assert fast.is_valid(root) is valid, value
    assert number._memo == set()


def test_a_repeated_valid_value_is_served_from_the_memo(fast):
    number = attributes_of(fast)["number"]
    fast.validate(document(number="7"))
    assert "7" in number._memo

    number._memo.add("not a number")
    fast.validate(document(number="not a number"))

    number._memo.discard("not a number")
    with pytest.raises(xmlschema.XMLSchemaValidationError):
        fast.validate(document(number="not a number"))


def test_decoding_does_not_consult_the_memo(fast):
    number = attributes_of(fast)["number"]
    fast.validate(document(number="7"))

    assert fast.decode(document(number="7"))["item"][0]["@number"] == 7
    number._memo.add("bad")
    with pytest.raises(xmlschema.XMLSchemaValidationError):
        fast.decode(document(number="bad"))


def test_the_memo_holds_at_most_the_documented_bound_per_attribute(fast):
    number = attributes_of(fast)["number"]
    limit = _fast_schema().MEMO_LIMIT
    assert limit == 128

    for value in range(limit * 2):
        fast.validate(document(number=str(value)))

    assert len(number._memo) == limit
    fast.validate(document(number=str(limit * 3)))
    assert len(number._memo) == limit
    with pytest.raises(xmlschema.XMLSchemaValidationError):
        fast.validate(document(number="x"))


def test_long_values_are_validated_every_time_and_never_kept(fast):
    number = attributes_of(fast)["number"]
    limit = _fast_schema().MEMO_VALUE_LIMIT
    assert limit == 64
    kept, long = "1" * limit, "1" * (limit + 1)

    for _ in range(2):
        fast.validate(document(number=kept))
        fast.validate(document(number=long))

    assert number._memo == {kept}


def test_the_memo_agrees_with_plain_xmlschema_on_unusual_values(fast):
    plain = xmlschema.XMLSchema11(SAFETY)
    values = [
        ("number", ["1", " 1 ", "+1", "-0", "01", "1.0", "٣", "", "x", "1 2"]),
        ("flag", ["true", "1", "TRUE", " false ", "yes", ""]),
        ("mixed", ["5", "true", "5 true", "", "x", "0"]),
        ("digits", ["12", "1.5", "-1", "", "a", "007", "٣"]),
        ("either", ["5", "a:b", "b", "", "x y"]),
        ("fixed", ["1", " 1 ", "2", "01"]),
        ("default", ["2", "3", ""]),
    ]
    for _ in range(2):
        for name, candidates in values:
            for value in candidates:
                xml = document(**{name: value})
                assert fast.is_valid(xml) == plain.is_valid(xml), (name, value)
                assert [str(e.reason) for e in fast.iter_errors(xml)] == [
                    str(e.reason) for e in plain.iter_errors(xml)
                ], (name, value)


def test_concurrent_validation_never_changes_a_verdict(fast):
    plain = xmlschema.XMLSchema11(SAFETY)
    number = attributes_of(fast)["number"]
    values = [str(n) for n in range(300)] + ["x", "1.5", "", " 3 ", "٣", "0x1"]
    expected = {value: plain.is_valid(document(number=value)) for value in values}
    barrier = threading.Barrier(8)

    def work(seed):
        rng = random.Random(seed)
        barrier.wait()
        return all(
            fast.is_valid(document(number=value)) == expected[value]
            for value in (rng.choice(values) for _ in range(500))
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert all(pool.map(work, range(8)))
    # Racing writers may each pass the bound check once before the first insert.
    assert len(number._memo) <= _fast_schema().MEMO_LIMIT + 8
    assert number._memo <= {value for value in values if expected[value]}
