"""Standard Django admin integration for stored Hyperview templates."""

from django import forms
from django.contrib import admin

from .models import HyperviewTemplate

__all__ = ["HyperviewTemplateAdmin", "HyperviewTemplateAdminForm"]


class HyperviewTemplateAdminForm(forms.ModelForm):
    """Validate admin edits through the model's existing field boundary."""

    class Meta:
        """Configure the model and editable admin fields."""

        model = HyperviewTemplate
        fields = ("name", "content", "active")


@admin.register(HyperviewTemplate)
class HyperviewTemplateAdmin(admin.ModelAdmin):
    """Present stored templates with standard Django admin controls."""

    form = HyperviewTemplateAdminForm
    list_display = ("name", "active", "revision", "updated_at")
    list_filter = ("active",)
    search_fields = ("name",)
    ordering = ("name",)
    readonly_fields = ("revision", "created_at", "updated_at")
