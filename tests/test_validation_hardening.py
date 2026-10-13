import random
import time

import pytest
from django.test import RequestFactory, override_settings
from lxml import etree

import dj_hyperview.validation as validation_module
from dj_hyperview.conf import ValidationSettings
from dj_hyperview.engine import HyperviewEngine
from dj_hyperview.exceptions import TemplateValidationError
from dj_hyperview.http import HyperviewTemplateResponse
from dj_hyperview.resolver import TemplateResolver
from dj_hyperview.validation import validate_hxml, validate_template_source

from .stubs import TemplateSource

MALFORMED_TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "OPTIONS": {
            "loaders": [
                (
                    "django.template.loaders.locmem.Loader",
                    {"screen.xml": "<view xmlns='https://hyperview.org/hyperview'>"},
                )
            ]
        },
    }
]


def assert_malformed(action):
    with pytest.raises(TemplateValidationError) as error:
        action()
    assert error.value.code == "malformed_xml"
    assert error.value.message == "invalid XML"


def test_public_engine_render_rejects_malformed_hxml():
    engine = HyperviewEngine(
        TemplateResolver(
            [TemplateSource(content="<view xmlns='https://hyperview.org/hyperview'>")]
        )
    )

    assert_malformed(lambda: engine.render("screen.xml"))


@pytest.mark.parametrize("using", ["django"])
@override_settings(TEMPLATES=MALFORMED_TEMPLATES, HYPERVIEW={})
def test_template_response_validates_django_fallbacks(using):
    response = HyperviewTemplateResponse(
        RequestFactory().get("/screen"), "screen.xml", using=using
    )

    assert response.is_rendered is False
    assert_malformed(response.render)
    assert response.is_rendered is False


def test_isolated_unicode_surrogate_is_a_typed_failure():
    with pytest.raises(TemplateValidationError) as error:
        validate_hxml("\ud800")

    assert error.value.code == "malformed_xml"
    assert error.value.message == (
        "document contains characters not allowed in XML 1.0"
    )


@pytest.mark.parametrize(
    "document",
    [
        "<text xmlns='https://hyperview.org/hyperview'>"
        "<!-- <!DOCTYPE view> --><text /></text>",
        "<text xmlns='https://hyperview.org/hyperview'><![CDATA[<!ENTITY xxe SYSTEM \"file:///etc/passwd\">]]></text>",
    ],
)
def test_declaration_text_is_allowed_in_xml_comments_and_cdata(document):
    assert validate_hxml(document) == document


@pytest.mark.parametrize(
    "comment",
    [
        "{# <!DOCTYPE view> #}",
        "{% comment %}<!ENTITY xxe SYSTEM 'file:///etc/passwd'>{% endcomment %}",
        '{% comment "reason" %}<!DOCTYPE view>{% endcomment %}',
    ],
)
def test_declaration_text_is_allowed_in_django_comments(comment):
    engine = HyperviewEngine(
        TemplateResolver(
            [
                TemplateSource(
                    content=f"<view xmlns='https://hyperview.org/hyperview'>{comment}<text>ok</text></view>"
                )
            ]
        )
    )

    assert (
        engine.render("screen.xml")
        == "<view xmlns='https://hyperview.org/hyperview'><text>ok</text></view>"
    )


def test_multiline_django_comment_syntax_cannot_hide_a_declaration() -> None:
    """The source guard mirrors Django's single-line comment lexer."""
    source = "{#\n<!DOCTYPE view [<!ENTITY x 'unsafe'>]>\n#}<view xmlns='https://hyperview.org/hyperview'>&x;</view>"

    with pytest.raises(TemplateValidationError) as captured:
        validate_template_source(source)

    assert captured.value.code == "forbidden_declaration"


def test_bare_carriage_return_remains_inside_an_inline_django_comment() -> None:
    """Declaration scanning mirrors Django's LF-only inline-comment boundary."""
    source = (
        "{# note\r<!DOCTYPE view> #}<view xmlns='https://hyperview.org/hyperview' />"
    )
    engine = HyperviewEngine(TemplateResolver([TemplateSource(content=source)]))

    assert (
        engine.render("screen.xml")
        == "<view xmlns='https://hyperview.org/hyperview' />"
    )


def test_csrf_tag_can_precede_an_xml_declaration_on_its_own_line() -> None:
    """Post-render normalization removes whitespace emitted by a load tag."""
    source = (
        "{% load dj_hyperview %}\n"
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<view xmlns='https://hyperview.org/hyperview'>{% hv_csrf_token %}</view>"
    )
    engine = HyperviewEngine(TemplateResolver([TemplateSource(content=source)]))

    rendered = engine.render("screen.xml", request=RequestFactory().get("/form/"))

    assert rendered.startswith("<?xml")
    assert 'name="csrfmiddlewaretoken"' in rendered


@pytest.mark.parametrize(
    "source",
    [
        "{% %}",
        "{% unfinished",
        "{% comment %}",
        "{% comment %}<!DOCTYPE view>",
        "{% comment %}{% unfinished <!ENTITY xxe>",
        "{% comment %}{% include 'ignored' %}<!DOCTYPE view>{% endcomment %}",
    ],
)
def test_source_guard_defers_malformed_django_comments_to_compilation(source):
    assert validate_template_source(source) == source


def test_active_and_post_render_constructed_declarations_remain_forbidden():
    with pytest.raises(TemplateValidationError) as source_error:
        validate_template_source(
            "<!DOCTYPE view><view xmlns='https://hyperview.org/hyperview' />"
        )

    engine = HyperviewEngine(
        TemplateResolver(
            [
                TemplateSource(
                    content=(
                        "{{ payload|safe }}"
                        "<view xmlns='https://hyperview.org/hyperview' />"
                    )
                )
            ]
        )
    )
    payload = '<!DOCTYPE view [<!ENTITY xxe SYSTEM "http://127.0.0.1/secret">]>'
    with pytest.raises(TemplateValidationError) as rendered_error:
        engine.render("screen.xml", {"payload": payload})

    assert source_error.value.code == "forbidden_declaration"
    assert rendered_error.value.code == "forbidden_declaration"


HYPERVIEW_XMLNS = "xmlns='https://hyperview.org/hyperview'"


def prefixed_namespaces(count: int, *, start: int = 0) -> str:
    return " ".join(f"xmlns:p{i}='urn:p{i}'" for i in range(start, start + count))


def assert_element_limit(document: str, fragment: str) -> None:
    with pytest.raises(TemplateValidationError) as error:
        validate_hxml(document)
    assert error.value.code == "max_attributes"
    assert fragment in error.value.message
    assert "p0" not in str(error.value)
    assert "a0" not in str(error.value)


def forbid_schema_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test if a document reaches the XSD walk, whatever its cost."""

    def forbidden_schema_walk(*args: object, **kwargs: object) -> None:
        raise AssertionError("schema validation must not run")

    monkeypatch.setattr(
        "dj_hyperview.schema._validate_schema_root", forbidden_schema_walk
    )


def test_attribute_flood_is_rejected_before_schema_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A per-element cap bounds super-linear XSD work without timing assertions."""
    forbid_schema_validation(monkeypatch)
    attributes = " ".join(f"a{i}='1'" for i in range(300))

    assert_element_limit(f"<view {HYPERVIEW_XMLNS} {attributes} />", "256 attributes")


def test_attribute_cap_counts_one_element_and_allows_its_exact_limit() -> None:
    """Many elements may carry attributes; only a single element is capped."""
    exact = " ".join(f"a{i}='1'" for i in range(256))
    spread = "".join(f"<text id='t{i}' style='s{i}'>{i}</text>" for i in range(300))

    with pytest.raises(TemplateValidationError) as exact_error:
        validate_hxml(f"<view {HYPERVIEW_XMLNS} {exact} />")
    validate_hxml(f"<view {HYPERVIEW_XMLNS}>{spread}</view>")

    assert exact_error.value.code == "schema"


def test_namespace_flood_is_rejected_on_the_declaring_element() -> None:
    """The default namespace counts, so 65 prefixes exceed 64 in-scope bindings."""
    assert_element_limit(
        f"<view {HYPERVIEW_XMLNS} {prefixed_namespaces(65)} />",
        "64 in-scope namespaces",
    )


def test_inherited_namespaces_count_toward_the_in_scope_cap() -> None:
    """No single element declares more than 64, but a descendant inherits them."""
    document = (
        f"<view {HYPERVIEW_XMLNS} {prefixed_namespaces(40)}>"
        f"<text {prefixed_namespaces(30, start=40)}>ok</text></view>"
    )

    assert_element_limit(document, "64 in-scope namespaces")


def test_namespace_cap_allows_its_exact_limit() -> None:
    """The default binding plus 63 prefixes is exactly 64 in-scope namespaces."""
    document = (
        f"<view {HYPERVIEW_XMLNS} {prefixed_namespaces(63)}><text>ok</text></view>"
    )

    assert validate_hxml(document) == document


def test_comments_and_processing_instructions_pass_the_element_caps() -> None:
    """Non-element nodes have no attributes or namespaces to count."""
    document = (
        f"<view {HYPERVIEW_XMLNS}><!-- note --><?audit data?><text>ok</text></view>"
    )

    assert validate_hxml(document) == document


def watch_maps(base: type, kind: str, accessed: set[tuple[str, str]]) -> type:
    """Subclass an lxml node class so reads of attrib and nsmap are recorded."""

    def recorded(name: str) -> property:
        inherited = getattr(base, name)

        def read(node: object) -> object:
            accessed.add((kind, name))
            return inherited.__get__(node)

        return property(read)

    members = {name: recorded(name) for name in ("attrib", "nsmap")}
    return type(f"Watched{kind}", (base,), members)


def test_element_caps_inspect_only_the_attributes_of_elements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No node evaluates nsmap, and comments and PIs never evaluate attrib.

    A processing instruction builds attrib by running a regular expression over
    its data, which is quadratic on long whitespace runs. nsmap walks every
    ancestor, so reading it per element costs depth times declarations.
    """
    accessed: set[tuple[str, str]] = set()
    parser_factory = validation_module._parser

    def watching_parser() -> etree.XMLParser:
        parser = parser_factory()
        parser.set_element_class_lookup(
            etree.ElementDefaultClassLookup(
                element=watch_maps(etree.ElementBase, "element", accessed),
                comment=watch_maps(etree.CommentBase, "comment", accessed),
                pi=watch_maps(etree.PIBase, "pi", accessed),
            )
        )
        return parser

    monkeypatch.setattr(validation_module, "_parser", watching_parser)
    document = (
        f"<view {HYPERVIEW_XMLNS} {prefixed_namespaces(3)}>"
        "<!-- note --><?audit data?><text>ok</text></view>"
    )

    validation_module._parse(document, ValidationSettings())

    assert accessed == {("element", "attrib")}


def test_long_whitespace_in_a_processing_instruction_validates_quickly() -> None:
    """PI data is never parsed into pseudo-attributes, which took 8 seconds here."""
    document = f"<view {HYPERVIEW_XMLNS}><?audit a{' ' * 40_000}x?></view>"

    started = time.perf_counter()
    assert validate_hxml(document) == document

    assert time.perf_counter() - started < 1.0


def test_namespace_declared_on_an_ancestor_counts_for_distant_descendants() -> None:
    """The default binding and 63 prefixes fill the cap for the whole subtree."""
    document = (
        f"<view {HYPERVIEW_XMLNS} {prefixed_namespaces(63)}>"
        "<view><view><text xmlns:extra='urn:extra'>ok</text></view></view></view>"
    )

    assert_element_limit(document, "64 in-scope namespaces")


def test_redeclaring_a_prefix_does_not_count_twice() -> None:
    """Descendants may rebind every in-scope prefix within both namespace caps.

    The default binding and 63 prefixes stay at 64 in scope however often they are
    rebound, so only the 256 declarations allowed on one path limit the chain.
    """
    rebound = prefixed_namespaces(63).replace("urn:p", "urn:rebound")
    chain = f"<view {rebound}>" * 3
    document = (
        f"<view {HYPERVIEW_XMLNS} {prefixed_namespaces(63)}>"
        f"{chain}<text>ok</text>{'</view>' * 3}</view>"
    )

    assert validate_hxml(document) == document


def test_namespaces_leave_scope_with_their_element() -> None:
    """Siblings never see each other's declarations, so only the path counts."""
    document = (
        f"<view {HYPERVIEW_XMLNS} {prefixed_namespaces(20)}>"
        f"<view {prefixed_namespaces(30, start=20)}><text>one</text></view>"
        f"<view {prefixed_namespaces(30, start=50)}><text>two</text></view>"
        "</view>"
    )

    assert validate_hxml(document) == document


def rebinding_path(declarations: int, *, per_level: int = 32, leaves: int = 1) -> str:
    """Nest views that rebind the same prefixes until one path holds `declarations`.

    The root also binds the default namespace. Rebinding keeps at most per_level
    distinct prefixes in scope, so only the declarations along the path grow.
    """
    opening: list[str] = []
    remaining = declarations
    while remaining > 0:
        level = len(opening)
        bindings = [] if level else [HYPERVIEW_XMLNS]
        count = min(per_level, remaining) - len(bindings)
        bindings += [f"xmlns:p{i}='urn:{level}:p{i}'" for i in range(count)]
        opening.append(f"<view {' '.join(bindings)}>")
        remaining -= len(bindings)
    return f"{''.join(opening)}{'<text>ok</text>' * leaves}{'</view>' * len(opening)}"


def test_rebinding_prefixes_along_a_deep_path_is_rejected_before_schema_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same prefixes rebound at every level cost the schema walk per element.

    Never more than 60 distinct prefixes are in scope, so the distinct cap passes,
    yet xmlschema reads every declaration on the ancestor path of each of the
    3,000 leaves: 3,600 declarations took about 1.5 seconds before this cap.
    """
    forbid_schema_validation(monkeypatch)
    document = rebinding_path(3_600, per_level=60, leaves=3_000)

    started = time.perf_counter()
    assert_element_limit(document, "256 namespace declarations")

    assert time.perf_counter() - started < 1.0


def test_namespace_declaration_cap_allows_its_exact_limit() -> None:
    """A path may hold 256 declarations, counting every rebinding of a prefix."""
    document = rebinding_path(256)

    assert validate_hxml(document) == document


def test_one_declaration_over_the_path_cap_is_rejected() -> None:
    """The 257th declaration on a path fails, however few prefixes are in scope."""
    assert_element_limit(rebinding_path(257), "256 namespace declarations")


def test_sibling_fragments_that_declare_namespaces_do_not_accumulate() -> None:
    """A screen composed of many fragments redeclares namespaces once per fragment.

    The declarations of a finished element leave the path, so 2,000 siblings add
    thousands of declarations in total but never more than three on any path.
    """
    fragment = f"<view {HYPERVIEW_XMLNS} xmlns:app='urn:app'><text>ok</text></view>"
    document = f"<view {HYPERVIEW_XMLNS}>{fragment * 2_000}</view>"

    assert validate_hxml(document) == document


def random_namespace_document(rng: random.Random) -> str:
    """Build a small tree that declares, rebinds and undeclares random prefixes."""

    def element(depth: int) -> str:
        names = rng.sample(["", "a", "b", "c", "d"], rng.randint(0, 3))
        declarations = " ".join(
            f"xmlns:{name}='urn:{rng.randint(1, 2)}'"
            if name
            else f"xmlns='{rng.choice(['', 'urn:0'])}'"
            for name in names
        )
        children = "".join(
            element(depth + 1) for _ in range(rng.randint(0, 3) if depth < 4 else 0)
        )
        return f"<e {declarations}>{children}</e>"

    return element(0)


def test_namespace_cap_matches_the_widest_nsmap_lxml_reports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Counting by scope reproduces nsmap, including rebinding and undeclaring."""
    rng = random.Random(20261013)
    for _ in range(150):
        document = random_namespace_document(rng)
        root = etree.fromstring(document)
        widest = max(len(node.nsmap) for node in root.iter(etree.Element))
        for cap in range(1, 6):
            monkeypatch.setattr(validation_module, "_MAX_NAMESPACES", cap)
            if widest <= cap:
                validation_module._parse(document, ValidationSettings())
                continue
            with pytest.raises(TemplateValidationError) as error:
                validation_module._parse(document, ValidationSettings())
            assert error.value.code == "max_attributes", document
            assert f"{cap} in-scope namespaces" in error.value.message
