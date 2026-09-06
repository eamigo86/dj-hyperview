"""Dedicated Django template engine for Hyperview markup."""

from collections.abc import Mapping, Sequence
from functools import lru_cache
from typing import Any

from django.conf import settings as django_settings
from django.dispatch import receiver
from django.http import HttpRequest
from django.template import TemplateDoesNotExist
from django.template.backends.django import DjangoTemplates
from django.test.signals import setting_changed
from django.utils.module_loading import import_string

from .conf import _SETTING_DEPENDENCIES, ValidationSettings, get_settings
from .exceptions import InvalidTemplateName, TemplateNotFound
from .loaders import template_snapshot
from .resolver import TemplateResolver
from .validation import validate_rendered_hxml

_INHERITED_DJANGO_TEMPLATE_OPTIONS = frozenset(
    {"builtins", "context_processors", "libraries", "string_if_invalid"}
)
_ENGINE_SETTING_DEPENDENCIES = _SETTING_DEPENDENCIES | {"TEMPLATES"}


def _consumer_django_template_options() -> dict[str, Any]:
    for configured in getattr(django_settings, "TEMPLATES", ()):
        if not isinstance(configured, Mapping):
            continue
        backend_path = configured.get("BACKEND")
        try:
            backend_type = import_string(backend_path)
        except (ImportError, TypeError, ValueError):
            continue
        if not isinstance(backend_type, type) or not issubclass(
            backend_type, DjangoTemplates
        ):
            continue
        options = configured.get("OPTIONS", {})
        if not isinstance(options, Mapping):
            return {}
        return {
            key: value
            for key, value in options.items()
            if key in _INHERITED_DJANGO_TEMPLATE_OPTIONS
        }
    return {}


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
        if name.startswith("_"):
            raise AttributeError(name)
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
        """Initialize a dedicated Hyperview template engine.

        Args:
            resolver: Ordered template resolver, or None to load current settings.
            validation: Validation policy, or None to load current settings.
        """
        settings = get_settings()
        self.resolver = resolver or TemplateResolver.from_settings()
        self.validation = validation or settings.validation
        options = _consumer_django_template_options()
        options["loaders"] = [
            (
                "dj_hyperview.loaders.ResolverLoader",
                self.resolver,
                self.validation,
            )
        ]
        self.backend = DjangoTemplates(
            {
                "NAME": "dj_hyperview",
                "DIRS": [],
                "APP_DIRS": False,
                "OPTIONS": options,
            }
        )

    def get_template(self, name: str) -> _ValidatedTemplate:
        """Compile one named consumer template with rendered validation.

        Args:
            name: Canonical template name.

        Returns:
            The compiled template with rendered validation.

        Raises:
            InvalidTemplateName: If the name is unsafe.
            TemplateNotFound: If the named template does not exist.
        """
        try:
            template = self.backend.get_template(name)
        except TemplateDoesNotExist as error:
            raise TemplateNotFound(name) from error
        return _ValidatedTemplate(template, self.validation, self.resolver)

    def select_template(self, names: Sequence[str]) -> _ValidatedTemplate:
        """Compile the first available template from an ordered candidate list.

        Args:
            names: Ordered canonical template names.

        Returns:
            The first available compiled template.

        Raises:
            TemplateNotFound: If no candidate template exists.
        """
        for name in names:
            try:
                return self.get_template(name)
            except (InvalidTemplateName, TemplateDoesNotExist):
                continue
        raise TemplateNotFound(", ".join(names))

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


@lru_cache(maxsize=1)
def _default_engine() -> HyperviewEngine:
    return HyperviewEngine()


@receiver(
    setting_changed,
    dispatch_uid="dj_hyperview.clear_default_engine",
    weak=False,
)
def _clear_default_engine(*, setting: str, **kwargs: Any) -> None:
    del kwargs
    if setting in _ENGINE_SETTING_DEPENDENCIES:
        _default_engine.cache_clear()


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
    return _default_engine().render_hxml(name, context, request)
