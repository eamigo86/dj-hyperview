"""Minimal URL configuration for the package-owned Django consumer."""

from django.urls import URLPattern, URLResolver, path

from .views import (
    ConsumerFullDocumentView,
    consumer_form,
    consumer_fragment,
    consumer_source,
)

urlpatterns: list[URLPattern | URLResolver] = [
    path("documents/full/", ConsumerFullDocumentView.as_view(), name="consumer-full"),
    path("documents/fragment/", consumer_fragment, name="consumer-fragment"),
    path("documents/form/", consumer_form, name="consumer-form"),
    path("documents/source/", consumer_source, name="consumer-source"),
]
