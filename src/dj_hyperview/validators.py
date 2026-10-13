"""Django validators for text embedded in Hyperview XML."""

import re

from django.core.exceptions import ValidationError

# XML 1.0 Char production complement: C0 controls other than tab, line feed and
# carriage return, lone surrogates, and the noncharacters U+FFFE and U+FFFF.
_XML_ILLEGAL_CHARACTERS = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]"
)


def validate_xml_text(value: str) -> None:
    """Reject text that contains characters XML 1.0 cannot represent.

    Use it on model and form fields whose values are rendered into Hyperview
    markup. The error never repeats the rejected value.

    Args:
        value: Candidate text for a Hyperview element or attribute value.

    Raises:
        ValidationError: If the value contains a character outside the XML 1.0
            Char production.
    """
    if _XML_ILLEGAL_CHARACTERS.search(str(value)):
        raise ValidationError(
            "Enter text without characters that XML 1.0 cannot represent.",
            code="xml_characters",
        )
