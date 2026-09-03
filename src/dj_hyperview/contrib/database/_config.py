"""Private configuration rules for the optional database source."""

from collections.abc import Mapping

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def _database_alias_is_configured(using: object) -> bool:
    if using is None:
        return True
    if type(using) is not str or not using:
        return False
    try:
        databases = settings.DATABASES
    except ImproperlyConfigured:
        return False
    return isinstance(databases, Mapping) and using in databases
