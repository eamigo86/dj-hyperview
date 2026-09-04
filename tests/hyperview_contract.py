"""Reusable offline validation for synthetic Hyperview contract fixtures."""

from __future__ import annotations

from collections import Counter

from lxml import etree


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
    if not schema.validate(document):
        raise ContractValidationError("schema validation failed")

    identifiers = Counter(str(value) for value in document.xpath("//@id"))
    targets = (str(value) for value in document.xpath("//@target"))
    if any(identifiers[target] != 1 for target in targets):
        raise ContractValidationError("reference integrity failed")
