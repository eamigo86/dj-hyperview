"""Reusable offline validation for synthetic Hyperview contract fixtures."""

from __future__ import annotations

from collections import Counter

from django.http import HttpResponse
from lxml import etree

from dj_hyperview import HYPERVIEW_MEDIA_TYPE

_HYPERVIEW_NAMESPACE = "https://hyperview.org/hyperview"


class ContractValidationError(ValueError):
    """Report a stable failure in the test-only Hyperview contract."""


def validate_contract_document(
    document: etree._Element, *, schema: etree.XMLSchema
) -> None:
    """Validate schema conformance and exact target-to-id integrity.

    Args:
        document: Parsed Hyperview document or fragment.
        schema: Focused offline schema for the pinned contract.

    Raises:
        ContractValidationError: If schema validation or reference integrity
            fails.
    """
    try:
        schema_valid = schema.validate(document)
    except etree.LxmlError:
        schema_valid = False
    if not schema_valid:
        raise ContractValidationError("schema validation failed")

    identifiers = Counter(str(value) for value in document.xpath("//@id"))
    targets = (str(value) for value in document.xpath("//@target"))
    if any(identifiers[target] != 1 for target in targets):
        raise ContractValidationError("reference integrity failed")


def validate_contract_response(
    response: HttpResponse, *, schema: etree.XMLSchema, expected_root: str
) -> etree._Element:
    """Validate a rendered HTTP response against the focused contract.

    Args:
        response: Rendered Django response carrying Hyperview markup.
        schema: Focused offline schema for the pinned contract.
        expected_root: Required local name of the response root element.

    Returns:
        Parsed response document after all contract checks pass.

    Raises:
        ContractValidationError: If media type, encoding, XML, schema,
            references, or document shape violate the focused contract.
    """
    media_type = response.headers.get("Content-Type", "").partition(";")[0].strip()
    if media_type != HYPERVIEW_MEDIA_TYPE:
        raise ContractValidationError("HTTP media type mismatch")
    if response.charset.lower() != "utf-8":
        raise ContractValidationError("HTTP encoding mismatch")

    try:
        response.content.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        encoding_valid = False
    else:
        encoding_valid = True
    if not encoding_valid:
        raise ContractValidationError("HTTP encoding mismatch")

    parser = etree.XMLParser(
        resolve_entities=False,
        load_dtd=False,
        no_network=True,
        recover=False,
    )
    try:
        document = etree.fromstring(response.content, parser=parser)
    except etree.LxmlError:
        document = None
    if document is None:
        raise ContractValidationError("HTTP body parsing failed") from None

    document_info = document.getroottree().docinfo
    declared_encoding = (document_info.encoding or "UTF-8").replace("-", "")
    if declared_encoding.casefold() != "utf8":
        raise ContractValidationError("HTTP encoding mismatch")
    if document_info.doctype:
        raise ContractValidationError("HTTP XML declarations forbidden")

    validate_contract_document(document, schema=schema)
    root = etree.QName(document)
    if root.namespace != _HYPERVIEW_NAMESPACE or root.localname != expected_root:
        raise ContractValidationError("HTTP document shape failed")
    return document
