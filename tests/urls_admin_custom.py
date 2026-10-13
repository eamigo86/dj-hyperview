"""URL configuration that also mounts the template admin on a custom AdminSite."""

from django.contrib.admin import AdminSite
from django.urls import path

from dj_hyperview.contrib.database.admin import HyperviewTemplateAdmin
from dj_hyperview.contrib.database.models import HyperviewTemplate

from .urls_admin import urlpatterns as default_urlpatterns

custom_site = AdminSite(name="custom")
custom_site.register(HyperviewTemplate, HyperviewTemplateAdmin)

urlpatterns = [*default_urlpatterns, path("custom/", custom_site.urls)]
