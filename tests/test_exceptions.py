"""Stable behavior for public package exceptions."""

import copy
import pickle

import pytest

from dj_hyperview.exceptions import (
    HyperviewConfigurationError,
    InvalidTemplateName,
    SourceUnavailable,
    TemplateNotFound,
    TemplateValidationError,
)


@pytest.mark.parametrize(
    "error",
    [
        HyperviewConfigurationError(["first", "second"]),
        InvalidTemplateName("../private.xml"),
        TemplateNotFound("missing.xml"),
        TemplateValidationError("malformed_xml", "invalid XML"),
        SourceUnavailable("database", "read failed"),
    ],
)
def test_public_exceptions_survive_pickle_and_deepcopy(error: Exception) -> None:
    """Process and test-runner boundaries preserve exception semantics."""
    for cloned in (pickle.loads(pickle.dumps(error)), copy.deepcopy(error)):
        assert type(cloned) is type(error)
        assert vars(cloned) == vars(error)
        assert str(cloned) == str(error)
