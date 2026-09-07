"""Migration-safe validators for stored Hyperview templates."""

from django.core.exceptions import ValidationError
from django.template import TemplateSyntaxError

from dj_hyperview.conf import get_settings
from dj_hyperview.engine import HyperviewEngine
from dj_hyperview.exceptions import TemplateValidationError
from dj_hyperview.validation import validate_template_source

from ._identity import _canonical_name_or_none


def validate_canonical_template_name(value: str) -> None:
    """Validate one canonical template name without exposing its value.

    Args:
        value: Candidate template name.

    Raises:
        ValidationError: If the candidate is unsafe or noncanonical.
    """
    if _canonical_name_or_none(value) is None:
        raise ValidationError("Enter a canonical template name.", code="invalid")


def validate_stored_template_source(value: str) -> None:
    """Apply safe publish-time checks to stored consumer source.

    Args:
        value: Consumer-owned template source.

    Raises:
        ValidationError: If validation rejects the template source.
    """
    config = get_settings().validation
    try:
        validate_template_source(value, config=config)
        HyperviewEngine(validation=config).backend.from_string(value)
    except TemplateSyntaxError as error:
        token = getattr(error, "token", None)
        line = getattr(token, "lineno", None)
        location = f" at line {line}" if line is not None else ""
        raise ValidationError(
            f"Invalid Django template syntax{location}.",
            code="django_syntax",
        ) from None
    except TemplateValidationError as error:
        raise ValidationError(
            f"Invalid Hyperview template source ({error.code}).",
            code=error.code,
        ) from None
