from dataclasses import replace

from django.core.exceptions import ValidationError
from django.db import models

from dj_hyperview.conf import get_settings
from dj_hyperview.exceptions import InvalidTemplateName, TemplateValidationError
from dj_hyperview.sources import canonicalize_template_name
from dj_hyperview.validation import validate_hxml, validate_template_source


class HyperviewTemplate(models.Model):
    """An optional, validated source template stored by canonical name."""

    name = models.CharField(max_length=255, unique=True)
    content = models.TextField()
    active = models.BooleanField(default=True)
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "dj_hyperview_database"
        ordering = ("name",)
        verbose_name = "Hyperview template"
        verbose_name_plural = "Hyperview templates"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(revision__gte=1),
                name="djhv_template_revision_gte_1",
            )
        ]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        errors = {}
        try:
            canonicalize_template_name(self.name)
            if len(self.name) > self._meta.get_field("name").max_length:
                raise InvalidTemplateName(self.name)
        except InvalidTemplateName:
            errors["name"] = ValidationError(
                "Enter a canonical template name.", code="invalid"
            )

        if isinstance(self.content, str):
            config = get_settings().validation
            try:
                validate_template_source(self.content, config=config)
                if "{%" not in self.content and "{#" not in self.content:
                    validate_hxml(self.content, config=replace(config, schema=None))
            except TemplateValidationError as error:
                errors["content"] = ValidationError(
                    f"Invalid Hyperview template source ({error.code}).",
                    code=error.code,
                )
        if errors:
            raise ValidationError(errors)
