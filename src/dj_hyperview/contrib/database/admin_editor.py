"""Optional Ace-powered HXML editor for the database template admin."""

from __future__ import annotations

from typing import Any

from django import forms
from django.urls import NoReverseMatch, reverse
from django.utils.html import format_html, format_html_join
from django.utils.safestring import SafeString
from django_ace import AceWidget


class HyperviewAceWidget(AceWidget):
    """Edit HXML with local Ace assets and project schema completions."""

    def __init__(
        self,
        attrs: dict[str, Any] | None = None,
        *,
        preview_url: str = "",
        draft_name: str = "",
        preview_scenarios: tuple[tuple[str, str], ...] = (),
    ) -> None:
        """Initialize an XML editor with strict CSP-compatible local assets.

        Args:
            attrs: Optional textarea HTML attributes.
            preview_url: Permission-bound preview endpoint, empty when disabled.
            draft_name: Logical name fallback for an Admin read-only name field.
            preview_scenarios: Configured scenario identifier and label pairs.
        """
        self.draft_name = draft_name
        self.preview_url = preview_url
        self.preview_scenarios = preview_scenarios
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
            ),
        )
        if self.preview_url:
            package_media += forms.Media(
                css={"screen": ("dj_hyperview/admin/hxml_preview.css",)},
                js=(
                    "dj_hyperview/admin/hxml_preview_renderer.js",
                    "dj_hyperview/admin/hxml_preview.js",
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
        if self.preview_url:
            resolved_attrs["data-hyperview-preview-url"] = self.preview_url
            resolved_attrs["data-hyperview-template-name"] = self.draft_name
        editor = super().render(name, value, resolved_attrs, renderer)
        if self.preview_url:
            options = format_html_join(
                "", '<option value="{}">{}</option>', self.preview_scenarios
            )
            return format_html(
                '<div class="djhv-preview-workspace"><div class="djhv-preview-source">'
                '{}<div class="djhv-editor-actions">'
                '<button type="button" class="button djhv-format-hxml">'
                "Format HXML</button>"
                '<label>Example data <select class="djhv-preview-scenario">'
                "{}</select></label>"
                '<button type="button" class="button djhv-preview-hxml">'
                "Preview</button>"
                '<span class="djhv-editor-status" role="status" '
                'aria-live="polite"></span>'
                '</div><div class="djhv-preview-diagnostics" aria-live="polite"></div>'
                '</div><section class="djhv-preview-panel" '
                'aria-label="Template preview">'
                '<h3>Preview <span class="djhv-preview-state" role="status" '
                'aria-live="polite">Not previewed</span></h3>'
                '<p class="djhv-preview-notice">'
                "Static approximation, not the native app. "
                "Images and actions are disabled.</p>"
                '<label class="djhv-preview-screen-label" hidden>Screen '
                '<select class="djhv-preview-screen"></select></label>'
                '<div class="djhv-preview-frame"></div><details>'
                "<summary>Rendered HXML</summary>"
                '<pre class="djhv-preview-output" tabindex="0"></pre></details>'
                "</section></div>",
                editor,
                options,
            )
        return format_html(
            '{}<div class="djhv-editor-actions">'
            '<button type="button" class="button djhv-format-hxml">Format HXML</button>'
            '<span class="djhv-editor-status" role="status" aria-live="polite"></span>'
            "</div>",
            editor,
        )
