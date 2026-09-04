"""Filesystem settings for the package-owned Django consumer."""

from pathlib import Path

from . import settings_base as base

SECRET_KEY = base.SECRET_KEY
INSTALLED_APPS = base.INSTALLED_APPS
ROOT_URLCONF = base.ROOT_URLCONF
DEFAULT_AUTO_FIELD = base.DEFAULT_AUTO_FIELD
MIDDLEWARE = [
    "dj_hyperview.middleware.HyperviewMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
]

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "consumer_project"
PRIMARY_TEMPLATE_DIR = FIXTURE_ROOT / "primary"
SECONDARY_TEMPLATE_DIR = FIXTURE_ROOT / "secondary"
HYPERVIEW: dict[str, object] = {
    "TEMPLATE_DIRS": [PRIMARY_TEMPLATE_DIR, SECONDARY_TEMPLATE_DIR],
    "SOURCES": [{"BACKEND": "dj_hyperview.sources.FileSystemSource"}],
}
