from django.template.backends.django import DjangoTemplates


class CustomDjangoTemplates(DjangoTemplates):
    """Consumer Django backend subclass used by engine configuration tests."""


class TemplateSource:
    def __init__(self, *, content=None, revision="stub"):
        self.content = content
        self.revision = revision

    def resolve(self, name):
        if self.content is None:
            return None
        from dj_hyperview.sources import ResolvedTemplate

        return ResolvedTemplate(
            name=name,
            content=self.content,
            origin=f"stub:{name}",
            source="stub",
            revision=self.revision,
        )


def validate_schema(document: str) -> None:
    del document


def allow_template_admin(request) -> bool:
    """Allow authenticated staff in admin permission configuration tests."""
    return bool(request.user.is_staff)
