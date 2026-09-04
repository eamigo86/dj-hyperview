"""Database settings for the package-owned Django consumer."""

import os

from . import settings_filesystem as filesystem

SECRET_KEY = filesystem.SECRET_KEY
DEFAULT_AUTO_FIELD = filesystem.DEFAULT_AUTO_FIELD
ROOT_URLCONF = filesystem.ROOT_URLCONF
INSTALLED_APPS = [*filesystem.INSTALLED_APPS, "dj_hyperview.contrib.database"]
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("DJ_HYPERVIEW_CONSUMER_DB", ":memory:"),
    }
}
HYPERVIEW = {
    **filesystem.HYPERVIEW,
    "SOURCES": [
        {
            "BACKEND": "dj_hyperview.contrib.database.sources.DatabaseSource",
            "OPTIONS": {"using": "default"},
        },
        *filesystem.HYPERVIEW["SOURCES"],
    ],
}
