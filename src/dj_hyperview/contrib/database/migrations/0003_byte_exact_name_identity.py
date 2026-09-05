"""Add a portable byte-exact identity for template names."""

import hashlib
from typing import Any

from django.db import migrations, models

import dj_hyperview.contrib.database.validators


def populate_name_identities(apps: Any, schema_editor: Any) -> None:
    """Backfill name identities for rows created by earlier releases.

    Args:
        apps: Historical Django application registry.
        schema_editor: Active migration schema editor.
    """
    template = apps.get_model("dj_hyperview_database", "HyperviewTemplate")
    manager = template._base_manager.using(schema_editor.connection.alias)
    for primary_key, name in manager.values_list("pk", "name").iterator():
        identity = hashlib.sha256(
            str(name).encode("utf-8", errors="surrogatepass")
        ).hexdigest()
        manager.filter(pk=primary_key).update(name_identity=identity)


class Migration(migrations.Migration):
    """Replace collation-sensitive uniqueness with portable identities."""

    dependencies = [
        ("dj_hyperview_database", "0002_field_validators"),
    ]

    operations = [
        migrations.AddField(
            model_name="hyperviewtemplate",
            name="name_identity",
            field=models.CharField(editable=False, max_length=64, null=True),
        ),
        migrations.AlterField(
            model_name="hyperviewtemplate",
            name="name",
            field=models.CharField(
                max_length=255,
                validators=[
                    dj_hyperview.contrib.database.validators.validate_canonical_template_name
                ],
            ),
        ),
        migrations.RunPython(populate_name_identities, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="hyperviewtemplate",
            name="name_identity",
            field=models.CharField(editable=False, max_length=64, unique=True),
        ),
    ]
