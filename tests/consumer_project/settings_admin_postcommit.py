"""Admin and LocMem settings for transactional consumer acceptance."""

from . import settings_admin as admin

SECRET_KEY = admin.SECRET_KEY
DEFAULT_AUTO_FIELD = admin.DEFAULT_AUTO_FIELD
DATABASES = admin.DATABASES
INSTALLED_APPS = admin.INSTALLED_APPS
MIDDLEWARE = admin.MIDDLEWARE
TEMPLATES = admin.TEMPLATES
ROOT_URLCONF = "tests.consumer_project.urls_admin_postcommit"
ALLOWED_HOSTS = ["testserver"]
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "dj-hyperview-consumer-admin",
    }
}
HYPERVIEW = {
    **admin.HYPERVIEW,
    "CACHE": {
        "ALIAS": "default",
        "NAMESPACE": "consumer-admin",
        "TTL": 300,
        "NEGATIVE_TTL": 30,
    },
}
