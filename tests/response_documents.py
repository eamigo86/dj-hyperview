"""The extra schema, documents and templates that the response tests share."""

HV = "https://hyperview.org/hyperview"
APP = "https://example.test/response"
XS = "http://www.w3.org/2001/XMLSchema"
EXTRA_XSD = (
    f'<xs:schema xmlns:xs="{XS}" targetNamespace="{APP}" '
    'elementFormDefault="qualified"><xs:element name="chip"/></xs:schema>'
)
# The declaration of `chip` that rejects DOCUMENT: it now requires an attribute.
STRICT_XSD = (
    f'<xs:schema xmlns:xs="{XS}" targetNamespace="{APP}" '
    'elementFormDefault="qualified"><xs:element name="chip"><xs:complexType>'
    '<xs:attribute name="required-id" use="required"/></xs:complexType>'
    "</xs:element></xs:schema>"
)
# Long enough for GZipMiddleware, which leaves a body under 200 bytes alone.
DOCUMENT = (
    f'<view xmlns="{HV}" xmlns:app="{APP}"><app:chip>{"ready " * 100}</app:chip></view>'
)
FRAGMENT = f'<text xmlns="{HV}">{"ready " * 100}</text>'
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "OPTIONS": {
            "loaders": [
                (
                    "django.template.loaders.locmem.Loader",
                    {"screen.xml": DOCUMENT, "fragment.xml": FRAGMENT},
                )
            ]
        },
    }
]
