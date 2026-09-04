"""Multi-database settings for package-owned consumer acceptance."""

from pathlib import Path

from . import settings_admin_postcommit as consumer

SECRET_KEY = consumer.SECRET_KEY
DEFAULT_AUTO_FIELD = consumer.DEFAULT_AUTO_FIELD
INSTALLED_APPS = consumer.INSTALLED_APPS
MIDDLEWARE = consumer.MIDDLEWARE
TEMPLATES = consumer.TEMPLATES
ROOT_URLCONF = consumer.ROOT_URLCONF
ALLOWED_HOSTS = consumer.ALLOWED_HOSTS
PASSWORD_HASHERS = consumer.PASSWORD_HASHERS
CACHES = consumer.CACHES

_default = consumer.DATABASES["default"]
_default_name = _default["NAME"]
_root = Path(_default_name).parent if _default_name != ":memory:" else Path(".")
DATABASES = {
    "default": _default,
    "replica": {**_default, "NAME": _root / "replica.sqlite3"},
    "broken": {**_default, "NAME": _root / "missing" / "broken.sqlite3"},
}
DATABASE_ROUTERS = ["tests.consumer_project.routing.ConsumerDatabaseRouter"]
HYPERVIEW = {
    "SOURCES": [{"BACKEND": "dj_hyperview.contrib.database.sources.DatabaseSource"}],
    "CACHE": {
        **consumer.HYPERVIEW["CACHE"],
        "NAMESPACE": "consumer-multidb",
    },
}
