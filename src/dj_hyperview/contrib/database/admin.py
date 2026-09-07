"""Standard Django admin integration for stored Hyperview templates."""

from collections.abc import Mapping
from typing import Any, cast

from django import forms
from django.contrib import admin, messages
from django.db import DEFAULT_DB_ALIAS, connections, models, router
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse
from django.template.response import TemplateResponse
from django.urls import path
from django.utils.datastructures import MultiValueDict

from dj_hyperview.conf import get_settings
from dj_hyperview.exceptions import InvalidTemplateName
from dj_hyperview.schema import get_hyperview_catalog

from ._identity import template_name_identity
from .models import HyperviewTemplate
from .services import (
    PublicationConflict,
    delete_template,
    publish_template,
    rename_template,
)

__all__ = ["HyperviewTemplateAdmin", "HyperviewTemplateAdminForm"]

_CONFLICT_MESSAGE = "Template changed; reload and retry."


def _submitted_revision(
    data: Mapping[str, Any], *, using: str = DEFAULT_DB_ALIAS
) -> int:
    """Parse one canonical revision within the selected database range.

    Args:
        data: Submitted form values.
        using: Database alias whose integer range constrains revisions.

    Returns:
        Canonical positive revision.

    Raises:
        PublicationConflict: If the token is ambiguous, malformed, or out of range.
    """
    values = (
        data.getlist("expected_revision")
        if isinstance(data, MultiValueDict)
        else [data.get("expected_revision")]
    )
    value = values[0] if len(values) == 1 else None
    maximum = connections[using].ops.integer_field_range("PositiveIntegerField")[1]
    maximum_text = str(maximum) if type(maximum) is int and maximum > 0 else ""
    if (
        type(value) is not str
        or not value
        or len(value) > len(maximum_text)
        or not value.isascii()
        or not value.isdecimal()
        or value.startswith("0")
        or (len(value) == len(maximum_text) and value > maximum_text)
    ):
        raise PublicationConflict
    return int(value)


class HyperviewTemplateAdminForm(forms.ModelForm):
    """Validate admin edits through the model's existing field boundary."""

    expected_revision = forms.IntegerField(
        required=False, min_value=1, widget=forms.HiddenInput
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize controls appropriate to a new or persisted template.

        Args:
            *args: Positional arguments forwarded to the model form.
            **kwargs: Keyword arguments forwarded to the model form.
        """
        instance = kwargs.get("instance")
        self._loaded_name = (
            instance.name
            if isinstance(instance, HyperviewTemplate) and instance.pk is not None
            else None
        )
        super().__init__(*args, **kwargs)
        if get_settings().admin.editor:
            from .admin_editor import HyperviewAceWidget

            self.fields["content"].widget = HyperviewAceWidget()
        if self.instance.pk is None:
            self.fields.pop("expected_revision", None)
        else:
            self.fields["expected_revision"].initial = self.instance.revision

    def clean(self) -> dict[str, Any]:
        """Reject edits based on a stale persisted revision.

        Returns:
            Values cleaned by the standard model form boundary.

        Raises:
            ValidationError: If the revision token is missing, ambiguous, or stale.
        """
        cleaned = super().clean()
        if self.instance.pk is None:
            return cleaned
        try:
            revision_matches = (
                _submitted_revision(
                    self.data, using=self.instance._state.db or DEFAULT_DB_ALIAS
                )
                == self.instance.revision
            )
        except PublicationConflict:
            revision_matches = False
        if not revision_matches:
            raise forms.ValidationError(_CONFLICT_MESSAGE, code="publication_conflict")
        return cleaned

    def clean_name(self) -> str:
        """Reject another row with the same byte-exact name identity.

        Returns:
            Validated canonical template name.

        Raises:
            ValidationError: If another row already owns the exact name.
        """
        name = cast(str, self.cleaned_data["name"])
        alias = (
            self.instance._state.db
            or router.db_for_write(self._meta.model)
            or DEFAULT_DB_ALIAS
        )
        matches = self._meta.model._default_manager.using(alias).filter(
            name_identity=template_name_identity(name)
        )
        if self.instance.pk is not None:
            matches = matches.exclude(pk=self.instance.pk)
        if matches.exists():
            raise forms.ValidationError(
                "A template with this name already exists.", code="unique"
            )
        return name

    class Meta:
        """Configure the model and editable admin fields."""

        model = HyperviewTemplate
        fields = ("name", "content", "active", "expected_revision")


@admin.register(HyperviewTemplate)
class HyperviewTemplateAdmin(admin.ModelAdmin):
    """Present stored templates with conflict-safe standard admin controls."""

    form = HyperviewTemplateAdminForm
    list_display = ("name", "active", "revision", "updated_at")
    list_filter = ("active",)
    search_fields = ("name",)
    ordering = ("name",)
    readonly_fields = ("revision", "created_at", "updated_at")
    delete_confirmation_max_display = getattr(
        admin.ModelAdmin, "delete_confirmation_max_display", None
    )
    delete_confirmation_template = (
        "admin/dj_hyperview_database/hyperviewtemplate/delete_confirmation.html"
    )

    def get_urls(self) -> list[Any]:
        """Add the permission-protected HXML completion catalog endpoint.

        Returns:
            Custom catalog route followed by standard model admin routes.
        """
        opts = self.model._meta
        name = f"{opts.app_label}_{opts.model_name}_hxml_catalog"
        custom = [
            path(
                "hxml-catalog/",
                self.admin_site.admin_view(self.hxml_catalog_view),
                name=name,
            )
        ]
        return custom + super().get_urls()

    def hxml_catalog_view(self, request: HttpRequest) -> JsonResponse:
        """Return completion metadata to authorized template editors.

        Args:
            request: Current authenticated admin request.

        Returns:
            Combined official and project-owned schema catalog.

        Raises:
            PermissionDenied: If the user cannot view stored templates.
        """
        from django.core.exceptions import PermissionDenied

        if not self.has_view_permission(request):
            raise PermissionDenied
        return JsonResponse(get_hyperview_catalog())

    def get_fields(
        self, request: HttpRequest, obj: HyperviewTemplate | None = None
    ) -> tuple[str, ...]:
        """Exclude revision input when no persisted object exists.

        Args:
            request: Current admin request.
            obj: Persisted object for a change form, or None for creation.

        Returns:
            Fields appropriate to the requested mutation.
        """
        fields = tuple(super().get_fields(request, obj))
        if obj is None:
            return tuple(field for field in fields if field != "expected_revision")
        return fields

    def get_queryset(self, request: HttpRequest) -> models.QuerySet:
        """Select the write database and lock change or delete POST rows.

        Args:
            request: Current admin request.

        Returns:
            Standard queryset, locked when the enclosing admin view is atomic.
        """
        queryset = super().get_queryset(request)
        match = request.resolver_match
        if (
            request.method == "POST"
            and match is not None
            and match.url_name is not None
            and match.url_name.endswith(("_change", "_delete"))
        ):
            alias = router.db_for_write(self.model) or DEFAULT_DB_ALIAS
            return queryset.using(alias).select_for_update()
        return queryset

    def save_model(
        self,
        request: HttpRequest,
        obj: HyperviewTemplate,
        form: forms.ModelForm,
        change: bool,
    ) -> None:
        """Persist an admin form through validated publication services.

        Args:
            request: Current admin request.
            obj: Unsaved form instance.
            form: Validated admin model form.
            change: Whether this edits an existing row.

        Raises:
            PublicationConflict: If the submitted revision is stale.
            ValidationError: If publication validation fails.
            DatabaseError: If persistence fails.
            SourceUnavailable: If post-commit invalidation fails.
        """
        alias = obj._state.db or router.db_for_write(self.model) or DEFAULT_DB_ALIAS
        publication_form = cast(HyperviewTemplateAdminForm, form)
        if change:
            result = rename_template(
                cast(str, publication_form._loaded_name),
                obj.name,
                content=obj.content,
                active=obj.active,
                expected_revision=cast(
                    int | None, form.cleaned_data.get("expected_revision")
                ),
                using=alias,
            )
        else:
            result = publish_template(
                obj.name, obj.content, active=obj.active, using=alias
            )
            if not result.created:
                raise PublicationConflict
        persisted = (
            self.model._default_manager.using(alias)
            .only("pk")
            .get(name_identity=template_name_identity(result.name))
        )
        obj.pk = persisted.pk
        obj.refresh_from_db(using=alias)
        obj._state.adding = False

    def delete_model(self, request: HttpRequest, obj: HyperviewTemplate) -> None:
        """Delete an admin object through the validated service.

        Args:
            request: Current admin request.
            obj: Locked template selected for deletion.

        Raises:
            PublicationConflict: If the submitted revision is stale.
            DatabaseError: If persistence fails.
            SourceUnavailable: If post-commit invalidation fails.
        """
        alias = obj._state.db or DEFAULT_DB_ALIAS
        expected_revision = _submitted_revision(request.POST, using=alias)
        try:
            delete_template(
                obj.name,
                expected_revision=expected_revision,
                using=obj._state.db,
            )
        except InvalidTemplateName:
            if obj.revision != expected_revision:
                raise PublicationConflict from None
            obj.delete(using=alias)

    def changeform_view(
        self,
        request: HttpRequest,
        object_id: str | None = None,
        form_url: str = "",
        extra_context: dict[str, Any] | None = None,
    ) -> HttpResponse:
        """Process a form and turn publication conflicts into admin feedback.

        Args:
            request: Current admin request.
            object_id: Optional serialized object identifier.
            form_url: Alternate form submission URL.
            extra_context: Additional template context.

        Returns:
            Standard form response or a safe conflict redirect.
        """
        try:
            return super().changeform_view(request, object_id, form_url, extra_context)
        except PublicationConflict:
            return self._conflict_response(request)

    def delete_view(
        self,
        request: HttpRequest,
        object_id: str,
        extra_context: dict[str, Any] | None = None,
    ) -> HttpResponse:
        """Process deletion and turn conflicts into admin feedback.

        Args:
            request: Current admin request.
            object_id: Serialized object identifier.
            extra_context: Additional template context.

        Returns:
            Standard delete response or a safe conflict redirect.
        """
        try:
            return super().delete_view(request, object_id, extra_context)
        except PublicationConflict:
            return self._conflict_response(request)

    def render_delete_form(
        self, request: HttpRequest, context: dict[str, Any]
    ) -> TemplateResponse:
        """Add a revision token to Django's standard delete confirmation.

        Args:
            request: Current admin request.
            context: Standard delete confirmation context.

        Returns:
            Standard template response augmented after rendering.
        """
        obj = cast(HyperviewTemplate, context["object"])
        return super().render_delete_form(
            request,
            {
                **context,
                "escaped_object": context.get("escaped_object", obj),
                "expected_revision": obj.revision,
                "delete_confirmation_max_display": (
                    self.delete_confirmation_max_display
                ),
            },
        )

    def _conflict_response(self, request: HttpRequest) -> HttpResponseRedirect:
        self.message_user(request, _CONFLICT_MESSAGE, level=messages.ERROR)
        return HttpResponseRedirect(request.get_full_path())
