"""Minimal URL configuration for the package-owned Django consumer."""

from django.urls import URLPattern, URLResolver, path

from .views import ConsumerFullDocumentView, consumer_fragment

urlpatterns: list[URLPattern | URLResolver] = [
    path("documents/full/", ConsumerFullDocumentView.as_view(), name="consumer-full"),
    path("documents/fragment/", consumer_fragment, name="consumer-fragment"),
]
