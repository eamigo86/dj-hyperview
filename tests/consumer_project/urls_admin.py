"""URL configuration for the package-owned optional admin consumer."""

from django.contrib import admin
from django.urls import path

urlpatterns = [path("admin/", admin.site.urls)]
