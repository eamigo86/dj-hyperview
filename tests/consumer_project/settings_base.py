"""Minimal settings for the package-owned Django consumer."""

SECRET_KEY = "consumer-tests-only"
INSTALLED_APPS = ["dj_hyperview"]
ROOT_URLCONF = "tests.consumer_project.urls"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
HYPERVIEW: dict[str, object] = {}
