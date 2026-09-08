"""Linear declaration scanning and the UTF-8 wire contract."""

import pytest

import dj_hyperview.validation as validation_module
from dj_hyperview.engine import HyperviewEngine
from dj_hyperview.exceptions import TemplateValidationError
from dj_hyperview.http import HyperviewResponse
from dj_hyperview.resolver import TemplateResolver
from dj_hyperview.validation import validate_hxml, validate_template_source

from .stubs import TemplateSource


class TrackedDocument(str):
    """Measure the actual search windows without imposing timing thresholds."""

    searched_characters = 0

    def find(self, sub: str, start: int = 0, end: int | None = None) -> int:
        """Count characters examined up to the first match or search boundary."""
        limit = len(self) if end is None else end
        found = super().find(sub, start, limit)
        self.searched_characters += (
            limit - start if found < 0 else found - start + len(sub)
        )
        return found


@pytest.mark.parametrize("validator", [validate_hxml, validate_template_source])
@pytest.mark.parametrize("opening", ["{%", "{#", "{% other ", "{% comment "])
@pytest.mark.parametrize("count", [1_000, 2_000])
def test_declaration_search_work_is_linear_for_unclosed_delimiters(
    validator, opening: str, count: int
) -> None:
    """An inert XML declaration must not trigger repeated suffix searches."""
    document = TrackedDocument(
        f"<view>{opening * count}<!-- <!DOCTYPE view> --></view>"
    )

    assert validator(document) == document
    assert document.searched_characters <= 12 * len(document)


def test_inline_comment_search_work_is_linear_across_a_later_line() -> None:
    """A distant closing marker cannot make failed inline comments quadratic."""
    document = TrackedDocument(
        "<view>" + "{#" * 2_000 + "\n#}<!-- <!DOCTYPE view> --></view>"
    )

    assert validate_template_source(document) == document
    assert document.searched_characters <= 12 * len(document)


@pytest.mark.parametrize(
    "source",
    [
        "<!-- <!DOCTYPE view> -->{% comment %}",
        "{% comment %}<!DOCTYPE view>{% ignored %}",
    ],
)
def test_unclosed_block_comment_at_end_is_deferred_to_compilation(source: str) -> None:
    """The source guard does not replace Django's missing-endcomment error."""
    assert validate_template_source(source) == source


@pytest.mark.parametrize(
    "prefix",
    [
        "{# <!DOCTYPE inert> #}{# second #}",
        "{% comment %}<!DOCTYPE inert>{% endcomment %}{% comment %}{% endcomment %}",
        "<!-- <!DOCTYPE inert> --><!-- second -->",
        "<![CDATA[<!DOCTYPE inert>]]><![CDATA[second]]>",
        "<?first <!DOCTYPE inert>?><?second ?>",
    ],
)
def test_scanner_rechecks_after_consuming_each_ignored_block(prefix: str) -> None:
    """Cached closing positions cannot hide an active later declaration."""
    with pytest.raises(TemplateValidationError) as captured:
        validate_template_source(f"{prefix}<!DOCTYPE view><view />")

    assert captured.value.code == "forbidden_declaration"


def test_public_engine_scans_autoescaped_context_as_xml_only(monkeypatch) -> None:
    """Literal Django delimiters in rendered data have no template semantics."""
    scan = validation_module._contains_forbidden_declaration
    documents = []

    def tracked_scan(document: str, **kwargs: object) -> bool:
        tracked = TrackedDocument(document)
        documents.append(tracked)
        return scan(tracked, **kwargs)

    monkeypatch.setattr(
        validation_module, "_contains_forbidden_declaration", tracked_scan
    )
    engine = HyperviewEngine(
        TemplateResolver(
            [
                TemplateSource(
                    content="<view>{{ payload }}<!-- <!DOCTYPE view> --></view>"
                )
            ]
        )
    )
    payload = "{%" * 2_000 + "<untrusted>"

    rendered = engine.render("screen.xml", {"payload": payload})

    assert "&lt;untrusted&gt;" in rendered
    assert len(documents) >= 2
    assert all(item.searched_characters <= 12 * len(item) for item in documents)


@pytest.mark.parametrize(
    "document",
    [
        "{# <!DOCTYPE view> #}<view />",
        "{% comment %}<!DOCTYPE view>{% endcomment %}<view />",
    ],
)
def test_rendered_django_comment_text_cannot_hide_active_xml_declarations(
    document: str,
) -> None:
    """Only XML comment syntax may hide a declaration after rendering."""
    assert validate_template_source(document) == document

    with pytest.raises(TemplateValidationError) as captured:
        validate_hxml(document)

    assert captured.value.code == "forbidden_declaration"


@pytest.mark.parametrize("validator", [validate_template_source, validate_hxml])
@pytest.mark.parametrize("encoding", ["ISO-8859-1", "UTF-16", "unknown-codec"])
def test_initial_bom_does_not_hide_non_utf8_declarations(
    validator, encoding: str
) -> None:
    """The declaration must agree with UTF-8 even when a BOM precedes it."""
    document = f'\ufeff<?xml version="1.0" encoding="{encoding}"?><view>café</view>'

    with pytest.raises(TemplateValidationError) as captured:
        validator(document)

    assert captured.value.code == "invalid_encoding"


@pytest.mark.parametrize("encoding", ["UTF-8", "utf8"])
def test_utf8_bom_is_preserved_through_validation_and_http(encoding: str) -> None:
    """Accept compatible encoding aliases without rewriting valid content."""
    document = f'\ufeff<?xml version="1.0" encoding="{encoding}"?><view>café</view>'

    assert validate_template_source(document) == document
    assert validate_hxml(document) == document
    assert HyperviewResponse(document).content == document.encode("utf-8")


def test_public_engine_rejects_bom_hidden_incompatible_encoding() -> None:
    """The complete rendering path enforces the same declaration contract."""
    document = '\ufeff<?xml version="1.0" encoding="ISO-8859-1"?><view>café</view>'
    engine = HyperviewEngine(TemplateResolver([TemplateSource(content=document)]))

    with pytest.raises(TemplateValidationError) as captured:
        engine.render("screen.xml")

    assert captured.value.code == "invalid_encoding"
