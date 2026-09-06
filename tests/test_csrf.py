from xml.etree import ElementTree

import pytest
from django.conf import settings
from django.http import HttpResponse
from django.middleware.csrf import CsrfViewMiddleware
from django.template import Context, RequestContext, Template
from django.test import RequestFactory, override_settings

from dj_hyperview.templatetags import dj_hyperview as hyperview_tags

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
    }
]


@override_settings(TEMPLATES=TEMPLATES)
def test_csrf_tag_uses_django_token_in_hidden_hyperview_field():
    request = RequestFactory().get("/form")
    template = Template("{% load dj_hyperview %}{% hv_csrf_token %}")

    element = ElementTree.fromstring(template.render(RequestContext(request)))

    assert element.tag == "text-field"
    assert element.attrib["hide"] == "true"
    assert element.attrib["name"] == "csrfmiddlewaretoken"
    assert element.attrib["value"].isalnum()
    assert request.META["CSRF_COOKIE"].isalnum()
    assert request.META["CSRF_COOKIE_NEEDS_UPDATE"] is True

    mutation = RequestFactory().post(
        "/mutate",
        {"csrfmiddlewaretoken": element.attrib["value"]},
    )
    mutation.COOKIES[settings.CSRF_COOKIE_NAME] = request.META["CSRF_COOKIE"]
    csrf_middleware = CsrfViewMiddleware(lambda incoming: HttpResponse())

    assert (
        csrf_middleware.process_view(
            mutation,
            lambda incoming: HttpResponse(),
            (),
            {},
        )
        is None
    )


@override_settings(TEMPLATES=TEMPLATES)
def test_csrf_tag_escapes_untrusted_token_for_xml(monkeypatch):
    request = RequestFactory().get("/form")
    monkeypatch.setattr(
        hyperview_tags,
        "get_token",
        lambda incoming_request: 'unsafe" & <token>',
    )
    template = Template("{% load dj_hyperview %}{% hv_csrf_token %}")

    rendered = template.render(RequestContext(request))

    assert rendered == (
        '<text-field hide="true" name="csrfmiddlewaretoken" '
        'value="unsafe&quot; &amp; &lt;token&gt;" />'
    )


@override_settings(TEMPLATES=TEMPLATES)
def test_csrf_tag_without_a_request_renders_nothing() -> None:
    """Programmatic rendering without a request remains valid."""
    template = Template("{% load dj_hyperview %}{% hv_csrf_token %}")

    assert template.render(Context()) == ""


@override_settings(TEMPLATES=TEMPLATES, DEBUG=True)
def test_csrf_tag_without_a_request_warns_in_debug_mode() -> None:
    """Debug rendering explains why the fail-closed field is missing."""
    template = Template("{% load dj_hyperview %}{% hv_csrf_token %}")

    with pytest.warns(UserWarning, match="context did not provide a request"):
        assert template.render(Context()) == ""
