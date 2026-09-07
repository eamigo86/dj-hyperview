"""Django settings for optional database-admin integration tests."""

from . import settings_database as database

SECRET_KEY = database.SECRET_KEY
INSTALLED_APPS = [
    "django_ace",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    *database.INSTALLED_APPS,
]
DATABASES = database.DATABASES
DEFAULT_AUTO_FIELD = database.DEFAULT_AUTO_FIELD
ROOT_URLCONF = "tests.urls_admin"
ALLOWED_HOSTS = ["testserver"]
MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
