"""Dedicated Django template engine for Hyperview markup."""

from collections.abc import Sequence

from django.template import TemplateDoesNotExist
from django.template.backends.django import DjangoTemplates

from .conf import ValidationSettings, get_settings
from .loaders import template_snapshot
from .resolver import TemplateResolver
from .validation import validate_rendered_hxml


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

    def get_template(self, name: str):
        return self.backend.get_template(name)

    def select_template(self, names: Sequence[str]):
        chain = []
        for name in names:
            try:
                return self.get_template(name)
            except TemplateDoesNotExist as error:
                chain.append(error)
        raise TemplateDoesNotExist(", ".join(names), chain=chain)

    def render(self, name: str | Sequence[str], context=None, request=None) -> str:
        with template_snapshot():
            template = (
                self.get_template(name)
                if isinstance(name, str)
                else self.select_template(name)
            )
            rendered = template.render(context, request)
        return validate_rendered_hxml(rendered, config=self.validation)

    def render_hxml(self, name: str | Sequence[str], context=None, request=None) -> str:
        """Render and, when configured, validate consumer HXML."""
        return self.render(name, context, request)


def render_template(name: str, context=None, request=None) -> str:
    """Render a consumer template using current HYPERVIEW settings."""
    return HyperviewEngine().render_hxml(name, context, request)
