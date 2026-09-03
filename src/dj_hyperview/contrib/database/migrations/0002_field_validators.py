import django.core.validators
from django.db import migrations, models

import dj_hyperview.contrib.database.validators


class Migration(migrations.Migration):
    dependencies = [("dj_hyperview_database", "0001_initial")]

    operations = [
        migrations.AlterField(
            model_name="hyperviewtemplate",
            name="content",
            field=models.TextField(
                validators=[
                    dj_hyperview.contrib.database.validators.validate_stored_template_source
                ]
            ),
        ),
        migrations.AlterField(
            model_name="hyperviewtemplate",
            name="name",
            field=models.CharField(
                max_length=255,
                unique=True,
                validators=[
                    dj_hyperview.contrib.database.validators.validate_canonical_template_name
                ],
            ),
        ),
        migrations.AlterField(
            model_name="hyperviewtemplate",
            name="revision",
            field=models.PositiveIntegerField(
                default=1, validators=[django.core.validators.MinValueValidator(1)]
            ),
        ),
    ]
