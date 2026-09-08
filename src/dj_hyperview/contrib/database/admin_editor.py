"""Optional Ace-powered HXML editor for the database template admin."""

from __future__ import annotations

from typing import Any

from django import forms
from django.urls import NoReverseMatch, reverse
from django.utils.html import format_html
from django.utils.safestring import SafeString
from django_ace import AceWidget


class HyperviewAceWidget(AceWidget):
    """Edit HXML with local Ace assets and project schema completions."""

    def __init__(self, attrs: dict[str, Any] | None = None) -> None:
        """Initialize an XML editor with strict CSP-compatible local assets.

        Args:
            attrs: Optional textarea HTML attributes.
        """
        super().__init__(
            attrs=attrs,
            mode="xml",
            theme="textmate",
            wordwrap=True,
            width="100%",
            height="32rem",
            showprintmargin=False,
            toolbar=True,
            useworker=False,
            extensions=("language_tools",),
            basicautocompletion=True,
            liveautocompletion=True,
            useStrictCSP=True,
        )

    @property
    def media(self) -> forms.Media:
        """Return django-ace plus package-owned local editor assets.

        Returns:
            CSS and JavaScript required by the enhanced editor.
        """
        package_media = forms.Media(
            css={
                "screen": (
                    "django_ace/ace/css/theme/monokai.css",
                    "dj_hyperview/admin/hxml_editor.css",
                )
            },
            js=(
                "django_ace/ace/theme-monokai.js",
                "dj_hyperview/admin/hxml_mode.js",
                "dj_hyperview/admin/hxml_editor.js",
                "dj_hyperview/admin/hxml_validation.js",
            ),
        )
        return super().media + package_media

    def render(
        self,
        name: str,
        value: Any,
        attrs: dict[str, Any] | None = None,
        renderer: Any = None,
    ) -> SafeString:
        """Render the editor, formatter control, and accessible status region.

        Args:
            name: Form field name.
            value: Current HXML source.
            attrs: Optional runtime HTML attributes.
            renderer: Active Django form renderer.

        Returns:
            Safe widget markup composed from trusted constants and base output.
        """
        resolved_attrs = dict(attrs or {})
        resolved_attrs["data-hyperview-editor"] = "true"
        try:
            catalog_url = reverse(
                "admin:dj_hyperview_database_hyperviewtemplate_hxml_catalog"
            )
        except NoReverseMatch:
            catalog_url = ""
        resolved_attrs["data-hyperview-catalog-url"] = catalog_url
        try:
            validation_url = reverse(
                "admin:dj_hyperview_database_hyperviewtemplate_hxml_validate"
            )
        except NoReverseMatch:
            validation_url = ""
        resolved_attrs["data-hyperview-validation-url"] = validation_url
        editor = super().render(name, value, resolved_attrs, renderer)
        return format_html(
            '{}<div class="djhv-editor-actions">'
            '<button type="button" class="button djhv-format-validate">'
            "Format and Validate</button>"
            '<span class="djhv-editor-status" role="status" aria-live="polite"></span>'
            '<span class="djhv-source-validation-status" role="status" '
            'aria-live="polite"></span>'
            '</div><div class="djhv-source-validation-diagnostics" '
            'aria-live="polite"></div>',
            editor,
        )
