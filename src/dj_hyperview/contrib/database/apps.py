from django.apps import AppConfig


class DjHyperviewDatabaseConfig(AppConfig):
    """Configuration for the optional database template app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "dj_hyperview.contrib.database"
    label = "dj_hyperview_database"
    verbose_name = "Hyperview database templates"
