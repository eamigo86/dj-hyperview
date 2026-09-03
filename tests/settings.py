SECRET_KEY = "test-only-key"
INSTALLED_APPS = ["dj_hyperview"]
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    },
    "replica": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    },
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
