"""LocMem cache settings for the package-owned Django consumer."""

import os
from pathlib import Path

from . import settings_filesystem as filesystem

SECRET_KEY = filesystem.SECRET_KEY
DEFAULT_AUTO_FIELD = filesystem.DEFAULT_AUTO_FIELD
INSTALLED_APPS = filesystem.INSTALLED_APPS
ROOT_URLCONF = filesystem.ROOT_URLCONF
TEMPLATE_DIR = Path(
    os.environ.get("DJ_HYPERVIEW_CONSUMER_TEMPLATES", filesystem.PRIMARY_TEMPLATE_DIR)
)
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "dj-hyperview-consumer-tests",
    }
}
HYPERVIEW = {
    "TEMPLATE_DIRS": [TEMPLATE_DIR],
    "SOURCES": [{"BACKEND": "dj_hyperview.sources.FileSystemSource"}],
    "CACHE": {
        "ALIAS": "default",
        "NAMESPACE": "consumer-filesystem",
        "TTL": 300,
        "NEGATIVE_TTL": 30,
    },
}
