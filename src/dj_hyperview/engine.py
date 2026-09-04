"""Dedicated Django template engine for Hyperview markup."""

from collections.abc import Sequence
from typing import Any

from django.http import HttpRequest
from django.template import TemplateDoesNotExist
from django.template.backends.django import DjangoTemplates

from .conf import ValidationSettings, get_settings
from .loaders import template_snapshot
from .resolver import TemplateResolver
from .validation import validate_rendered_hxml


class _ValidatedTemplate:
    def __init__(
        self,
        template,
        validation: ValidationSettings,
        resolver: TemplateResolver,
    ) -> None:
        self._template = template
        self._resolver = resolver
        self.validation = validation

    def __getattr__(self, name):
        return getattr(self._template, name)

    def render(self, context=None, request=None) -> str:
        with template_snapshot(self._resolver):
            rendered = self._template.render(context, request)
        return validate_rendered_hxml(rendered, config=self.validation)


class HyperviewEngine:
    """Compile and render consumer templates through the Hyperview resolver."""

    def __init__(
        self,
        resolver: TemplateResolver | None = None,
        *,
        validation: ValidationSettings | None = None,
    ) -> None:
        settings = get_settings()
        self.resolver = resolver or TemplateResolver.from_settings()
        self.validation = validation or settings.validation
        self.backend = DjangoTemplates(
            {
                "NAME": "dj_hyperview",
                "DIRS": [],
                "APP_DIRS": False,
                "OPTIONS": {
                    "loaders": [
                        (
                            "dj_hyperview.loaders.ResolverLoader",
                            self.resolver,
                            self.validation,
                        )
                    ]
                },
            }
        )

    def get_template(self, name: str) -> _ValidatedTemplate:
        """Compile one named consumer template with rendered validation.

        Args:
            name: Canonical template name.

        Returns:
            The compiled template with rendered validation.
        """
        return _ValidatedTemplate(
            self.backend.get_template(name), self.validation, self.resolver
        )

    def select_template(self, names: Sequence[str]) -> _ValidatedTemplate:
        """Compile the first available template from an ordered candidate list.

        Args:
            names: Ordered canonical template names.

        Returns:
            The first available compiled template.
        """
        chain = []
        for name in names:
            try:
                return self.get_template(name)
            except TemplateDoesNotExist as error:
                chain.append(error)
        raise TemplateDoesNotExist(", ".join(names), chain=chain)

    def render(
        self,
        name: str | Sequence[str],
        context: dict[str, Any] | None = None,
        request: HttpRequest | None = None,
    ) -> str:
        """Render a named template or ordered template selection.

        Args:
            name: Canonical name or ordered candidate names.
            context: Optional template context.
            request: Optional Django request.

        Returns:
            The rendered and validated Hyperview markup.
        """
        with template_snapshot(self.resolver):
            template = (
                self.get_template(name)
                if isinstance(name, str)
                else self.select_template(name)
            )
            return template.render(context, request)

    def render_hxml(
        self,
        name: str | Sequence[str],
        context: dict[str, Any] | None = None,
        request: HttpRequest | None = None,
    ) -> str:
        """Render and, when configured, validate consumer HXML.

        Args:
            name: Canonical name or ordered candidate names.
            context: Optional template context.
            request: Optional Django request.

        Returns:
            The rendered Hyperview markup.
        """
        return self.render(name, context, request)


def render_template(
    name: str,
    context: dict[str, Any] | None = None,
    request: HttpRequest | None = None,
) -> str:
    """Render a consumer template using current HYPERVIEW settings.

    Args:
        name: Template name or ordered candidate names.
        context: Optional template context.
        request: Optional Django request.

    Returns:
        The rendered Hyperview markup.
    """
    return HyperviewEngine().render_hxml(name, context, request)
