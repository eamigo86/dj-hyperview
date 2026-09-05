"""HTTP contract tests for synthetic Hyperview 0.110.0 fixtures."""

from hashlib import sha256
from pathlib import Path

import pytest
from django.http import HttpResponse
from django.test import Client, override_settings
from lxml import etree

from dj_hyperview import HYPERVIEW_MEDIA_TYPE, HyperviewResponse
from tests.consumer_project import settings_filesystem as filesystem
from tests.hyperview_contract import (
    ContractValidationError,
    validate_contract_response,
)

CONTRACT = Path(__file__).parent / "contracts" / "hyperview" / "0.110.0"
COMPATIBILITY_NOTE = Path(__file__).parents[1] / "docs" / "hyperview-0.110.0.md"
NAMESPACE = "https://hyperview.org/hyperview"
HTTP_SETTINGS = {
    "ROOT_URLCONF": "tests.consumer_project.urls",
    "HYPERVIEW": {
        "TEMPLATE_DIRS": [CONTRACT],
        "SOURCES": [{"BACKEND": "dj_hyperview.sources.FileSystemSource"}],
    },
}
CSRF_SETTINGS = {
    "ROOT_URLCONF": "tests.consumer_project.urls",
    "HYPERVIEW": filesystem.HYPERVIEW,
    "MIDDLEWARE": filesystem.MIDDLEWARE,
}


class _FailingSchema:
    def validate(self, document: etree._Element) -> bool:
        """Simulate a low-level schema engine failure.

        Args:
            document: Parsed document supplied by the contract helper.

        Raises:
            XMLSchemaValidateError: Always, with deliberately sensitive detail.
        """
        raise etree.XMLSchemaValidateError("file:///private/contract-secret.xsd")


def _schema() -> etree.XMLSchema:
    parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True)
    return etree.XMLSchema(etree.parse(CONTRACT / "focused-hyperview.xsd", parser))


@override_settings(**HTTP_SETTINGS)
def test_full_fixture_satisfies_public_http_contract(client: Client) -> None:
    """Serve and validate a full document through public package APIs."""
    response = client.get("/documents/source/", {"template": "full.xml"})

    document = validate_contract_response(
        response, schema=_schema(), expected_root="doc"
    )

    assert response.status_code == 200
    assert document.xpath("string(.//hv:text)", namespaces={"hv": NAMESPACE}) == "Hello"
    assert response.headers["X-Hyperview-Source"] == "filesystem"
    assert (
        response.headers["X-Hyperview-Revision"]
        == sha256((CONTRACT / "full.xml").read_bytes()).hexdigest()
    )


@pytest.mark.parametrize(
    ("name", "expected_root", "expected_text"),
    [("fragment.xml", "view", "Ready"), ("form.xml", "form", "Save")],
)
@override_settings(**HTTP_SETTINGS)
def test_partial_fixtures_satisfy_public_http_contract(
    client: Client, name: str, expected_root: str, expected_text: str
) -> None:
    """Serve versioned fragment forms with valid behavior references."""
    response = client.get("/documents/source/", {"template": name})

    document = validate_contract_response(
        response, schema=_schema(), expected_root=expected_root
    )

    assert response.status_code == 200
    assert document.xpath("string(.//hv:text)", namespaces={"hv": NAMESPACE}) == (
        expected_text
    )
    if name == "form.xml":
        behavior = document.xpath("./hv:view[@href]", namespaces={"hv": NAMESPACE})[0]
        assert behavior.attrib == {
            "id": "submit-control",
            "href": "/profiles",
            "action": "replace",
            "target": "form-result",
        }


@override_settings(**CSRF_SETTINGS)
def test_versioned_request_escapes_context_and_accepts_real_csrf() -> None:
    """Escape hostile context and enforce Django CSRF for a 0.110.0 request."""
    client = Client(enforce_csrf_checks=True)
    headers = {"X-Hyperview-Version": "0.110.0"}
    fragment = client.get(
        "/documents/fragment/", {"label": "Café & <unsafe>"}, headers=headers
    )
    form = client.get("/documents/form/", headers=headers)
    field = etree.fromstring(form.content).find(
        ".//text-field[@name='csrfmiddlewaretoken']"
    )

    assert fragment.headers["Content-Type"] == (
        f"{HYPERVIEW_MEDIA_TYPE}; charset=utf-8"
    )
    assert fragment.charset == "utf-8"
    assert fragment.content == (
        b"<view><text>Caf\xc3\xa9 &amp; &lt;unsafe&gt;</text></view>"
    )
    assert field is not None
    assert field.attrib["value"].isalnum()
    rejected = client.post(
        "/documents/form/", {"message": "not accepted"}, headers=headers
    )
    accepted = client.post(
        "/documents/form/",
        {
            "message": "Café & <accepted>",
            "csrfmiddlewaretoken": field.attrib["value"],
        },
        headers=headers,
    )
    assert rejected.status_code == 403
    assert accepted.status_code == 201
    assert accepted.headers["Content-Type"] == (
        f"{HYPERVIEW_MEDIA_TYPE}; charset=utf-8"
    )
    assert accepted.charset == "utf-8"
    assert b"Caf\xc3\xa9 &amp; &lt;accepted&gt;" in accepted.content


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("media", "HTTP media type mismatch"),
        ("encoding", "HTTP encoding mismatch"),
        ("xml", "HTTP body parsing failed"),
        ("shape", "HTTP document shape failed"),
        ("reference", "reference integrity failed"),
    ],
)
def test_http_contract_rejects_invalid_protocol_boundaries(
    case: str, message: str
) -> None:
    """Reject invalid media, encoding, XML, shape, and target references."""
    content = (CONTRACT / "fragment.xml").read_bytes()
    response: HttpResponse = HyperviewResponse(content)
    expected_root = "view"
    if case == "media":
        response = HttpResponse(content, content_type="application/xml")
    elif case == "encoding":
        response = HttpResponse(
            content,
            content_type=f"{HYPERVIEW_MEDIA_TYPE}; charset=iso-8859-1",
        )
    elif case == "xml":
        response = HyperviewResponse(f'<view xmlns="{NAMESPACE}">'.encode())
    elif case == "shape":
        expected_root = "doc"
    else:
        response = HyperviewResponse(
            f'<view xmlns="{NAMESPACE}" id="root" target="missing" />'.encode()
        )

    with pytest.raises(ContractValidationError, match=message):
        validate_contract_response(
            response, schema=_schema(), expected_root=expected_root
        )


@pytest.mark.parametrize(
    "content",
    [
        (
            b'<?xml version="1.0" encoding="ISO-8859-1"?>'
            b'<view xmlns="https://hyperview.org/hyperview">'
            b"<text>Caf\xe9</text></view>"
        ),
        (
            b'<?xml version="1.0" encoding="ISO-8859-1"?>'
            b'<view xmlns="https://hyperview.org/hyperview" />'
        ),
    ],
)
def test_http_contract_rejects_non_utf8_bytes_or_declaration(
    content: bytes,
) -> None:
    """Reject body bytes and XML declarations that contradict HTTP UTF-8."""
    response = HyperviewResponse(content)

    with pytest.raises(
        ContractValidationError, match="HTTP encoding mismatch"
    ) as captured:
        validate_contract_response(response, schema=_schema(), expected_root="view")

    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


@pytest.mark.parametrize(
    "body",
    [
        '<!DOCTYPE view [<!ELEMENT view ANY>]><view xmlns="{namespace}" />',
        '<!DOCTYPE view SYSTEM "file:///private/contract-secret.dtd">'
        '<view xmlns="{namespace}" />',
        '<!DOCTYPE view [<!ENTITY secret "private-value">]>'
        '<view xmlns="{namespace}" />',
        '<!DOCTYPE view [<!ENTITY secret "private-value">]>'
        '<view xmlns="{namespace}"><text>&secret;</text></view>',
        "<!DOCTYPE view [<!ENTITY secret SYSTEM "
        '"file:///private/contract-secret.txt">]>'
        '<view xmlns="{namespace}"><text>&secret;</text></view>',
        "<!DOCTYPE view [<!ENTITY secret SYSTEM "
        '"https://contract.invalid/private.txt">]>'
        '<view xmlns="{namespace}"><text>&secret;</text></view>',
    ],
)
def test_http_contract_rejects_dtd_and_entities_without_details(
    body: str,
) -> None:
    """Reject internal and external declarations without resolving resources."""
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>' + body.format(namespace=NAMESPACE)
    ).encode()
    response = HyperviewResponse(content)

    with pytest.raises(
        ContractValidationError, match="HTTP XML declarations forbidden"
    ) as captured:
        validate_contract_response(response, schema=_schema(), expected_root="view")

    rendered = repr(captured.value)
    assert "contract-secret" not in rendered
    assert "XMLSchemaValidateError" not in rendered
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_http_contract_accepts_utf8_body_without_xml_declaration() -> None:
    """Accept strict UTF-8 markup when the optional declaration is absent."""
    response = HyperviewResponse(
        f'<view xmlns="{NAMESPACE}"><text>Café</text></view>'.encode()
    )

    document = validate_contract_response(
        response, schema=_schema(), expected_root="view"
    )

    assert document.xpath("string(.//hv:text)", namespaces={"hv": NAMESPACE}) == (
        "Café"
    )


def test_http_contract_allows_declaration_text_inside_xml_comment() -> None:
    """Do not mistake inert comment text for an active DTD declaration."""
    response = HyperviewResponse(
        (
            f'<view xmlns="{NAMESPACE}"><!-- <!DOCTYPE view> -->'
            "<text>safe</text></view>"
        ).encode()
    )

    document = validate_contract_response(
        response, schema=_schema(), expected_root="view"
    )

    assert document.xpath("string(.//hv:text)", namespaces={"hv": NAMESPACE}) == (
        "safe"
    )


def test_http_contract_normalizes_schema_engine_failures() -> None:
    """Hide low-level schema failure details behind the contract error."""
    response = HyperviewResponse(f'<view xmlns="{NAMESPACE}" />'.encode())

    with pytest.raises(
        ContractValidationError, match="schema validation failed"
    ) as captured:
        validate_contract_response(
            response,
            schema=_FailingSchema(),  # type: ignore[arg-type]
            expected_root="view",
        )

    assert "contract-secret" not in repr(captured.value)
    assert "XMLSchemaValidateError" not in repr(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_versioned_compatibility_note_states_provenance_and_limits() -> None:
    """Document the precise server-produced compatibility claim."""
    note = COMPATIBILITY_NOTE.read_text(encoding="utf-8")

    assert "Hyperview 0.110.0" in note
    assert "https://www.npmjs.com/package/hyperview/v/0.110.0" in note
    assert "https://github.com/Instawork/hyperview" in note
    assert "https://hyperview.org" in note
    assert "does not execute the Hyperview client" in note
    assert "focused test-only contract" in note
