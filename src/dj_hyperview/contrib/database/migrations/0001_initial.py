from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = []

    operations = [
        migrations.CreateModel(
            name="HyperviewTemplate",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(max_length=255, unique=True)),
                ("content", models.TextField()),
                ("active", models.BooleanField(default=True)),
                ("revision", models.PositiveIntegerField(default=1)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Hyperview template",
                "verbose_name_plural": "Hyperview templates",
                "ordering": ("name",),
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(revision__gte=1),
                        name="djhv_template_revision_gte_1",
                    )
                ],
            },
        )
    ]
