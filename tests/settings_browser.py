"""Local-only Admin settings for opt-in Chromium acceptance tests."""

from . import settings_database_admin as admin

SECRET_KEY = admin.SECRET_KEY
INSTALLED_APPS = [*admin.INSTALLED_APPS, "django.contrib.staticfiles"]
DATABASES = admin.DATABASES
DEFAULT_AUTO_FIELD = admin.DEFAULT_AUTO_FIELD
ROOT_URLCONF = admin.ROOT_URLCONF
ALLOWED_HOSTS = admin.ALLOWED_HOSTS
MIDDLEWARE = admin.MIDDLEWARE
TEMPLATES = admin.TEMPLATES
PASSWORD_HASHERS = admin.PASSWORD_HASHERS
STATIC_URL = "/static/"
HYPERVIEW = {
    "ADMIN": {
        "EDITOR": True,
        "PREVIEW": {
            "ENABLED": True,
            "SCENARIOS": {
                "with_tasks": {
                    "LABEL": "With tasks",
                    "CONTEXT": {"tasks": [{"title": "Review A & B"}]},
                },
                "empty_list": {"LABEL": "Empty list", "CONTEXT": {"tasks": []}},
            },
        },
    },
}
