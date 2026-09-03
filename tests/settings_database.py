import os

from . import settings as base

SECRET_KEY = base.SECRET_KEY
INSTALLED_APPS = [*base.INSTALLED_APPS, "dj_hyperview.contrib.database"]
DATABASES = {
    "default": {
        **base.DATABASES["default"],
        "NAME": os.environ.get("DJHV_TEST_DATABASE", ":memory:"),
    }
}
DEFAULT_AUTO_FIELD = base.DEFAULT_AUTO_FIELD
