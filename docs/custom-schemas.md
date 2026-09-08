# Add custom HXML elements

Extend validation and editor autocomplete with project-owned XML Schema 1.1
files. A custom schema describes the elements understood by your mobile client;
it does not implement their React Native behavior.

## Quick path

1. Choose a permanent namespace URI for the project's custom elements.
2. Define those elements in a local XSD 1.1 file.
3. Register the file through `EXTRA_SCHEMAS`.
4. Declare a prefix for the same namespace in each HXML document.
5. Run Django's system checks before opening the editor or serving HXML.

Install validation alone:

```bash
uv add "dj-hyperview[schema]"
```

Install validation plus admin autocomplete and formatting:

```bash
uv add "dj-hyperview[editor]"
```

The editor profile includes the schema dependency. It also requires
`django_ace` in `INSTALLED_APPS`.

## 1. Choose a namespace

Use a URI controlled by the project and keep it stable. It identifies the
vocabulary; it does not need to return a web page.

This guide uses:

```text
https://example.com/hypertodo
```

The prefix `app` is only a local abbreviation. The namespace URI, rather than
the prefix, is the identity that must match between the XSD and HXML.

## 2. Define the custom elements

Create `schema/hypertodo.xsd` in the Django project:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<xs:schema
  xmlns:xs="http://www.w3.org/2001/XMLSchema"
  xmlns:app="https://example.com/hypertodo"
  targetNamespace="https://example.com/hypertodo"
  elementFormDefault="qualified"
  version="1.1"
>
  <xs:element name="swipe-row">
    <xs:complexType>
      <xs:sequence>
        <xs:element
          ref="app:swipe-action"
          minOccurs="1"
          maxOccurs="unbounded"
        />
        <xs:any
          namespace="https://hyperview.org/hyperview"
          processContents="lax"
        />
      </xs:sequence>
      <xs:attribute name="id" type="xs:ID" />
      <xs:attribute name="direction" use="required">
        <xs:simpleType>
          <xs:restriction base="xs:string">
            <xs:enumeration value="left" />
            <xs:enumeration value="right" />
          </xs:restriction>
        </xs:simpleType>
      </xs:attribute>
    </xs:complexType>
  </xs:element>

  <xs:element name="swipe-action">
    <xs:complexType>
      <xs:attribute name="label" type="xs:string" use="required" />
      <xs:attribute name="href" type="xs:anyURI" use="required" />
      <xs:attribute name="verb" default="post">
        <xs:simpleType>
          <xs:restriction base="xs:string">
            <xs:enumeration value="delete" />
            <xs:enumeration value="patch" />
            <xs:enumeration value="post" />
          </xs:restriction>
        </xs:simpleType>
      </xs:attribute>
    </xs:complexType>
  </xs:element>
</xs:schema>
```

This definition gives the editor enough information to suggest:

- `app:swipe-action` inside `app:swipe-row`;
- unused attributes for each element;
- `left` and `right` for `direction`;
- `delete`, `patch`, and `post` for `verb`;
- required-attribute metadata for validation.

The checked-in [complete example](examples/hypertodo.xsd) can be copied as a
starting point.

## 3. Register the schema

Configure the local path and activate the bundled Hyperview validator:

```python
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

INSTALLED_APPS = [
    "django.contrib.admin",
    "django_ace",
    "dj_hyperview",
    "dj_hyperview.contrib.database",
]

HYPERVIEW = {
    "ADMIN": {
        "EDITOR": True,
    },
    "EXTRA_SCHEMAS": [
        BASE_DIR / "schema" / "hypertodo.xsd",
    ],
    "VALIDATION": {
        "SCHEMA": "dj_hyperview.validate_hyperview_schema",
    },
}
```

`EXTRA_SCHEMAS` accepts multiple local root files. Give each independent custom
vocabulary its own namespace. The package merges their declarations with the
bundled Hyperview 0.110.0 registry lazily.

## 4. Use the namespace in HXML

Declare the namespace on the document and use its chosen prefix:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<doc
  xmlns="https://hyperview.org/hyperview"
  xmlns:app="https://example.com/hypertodo"
>
  <screen id="tasks">
    <body>
      <app:swipe-row id="task-42" direction="left">
        <app:swipe-action
          label="Delete"
          href="/hv/tasks/42/"
          verb="delete"
        />
        <view>
          <text>Review custom schema support</text>
        </view>
      </app:swipe-row>
    </body>
  </screen>
</doc>
```

The same schema continues to work if the document uses another prefix such as
`todo`, provided that prefix maps to `https://example.com/hypertodo`.

## 5. Verify the integration

Run checks from the Django project directory:

```console
python manage.py check
```

The check rejects missing files, invalid XSD 1.1, remote references, and local
references that escape the schema root. Then exercise at least one valid and
one invalid rendered response in the consumer project's test suite.

With the editor enabled, open a database template in Django Admin and verify
that autocomplete proposes the qualified custom element, contextual children,
unused attributes, and enumeration values. The catalog endpoint is private and
requires the template model's view permission.

## Split larger schemas safely

An extra schema may use `xs:include`, `xs:import`, or `xs:redefine` only for
local files that remain under the root XSD's directory:

```text
schema/
├── hypertodo.xsd
└── components/
    └── swipe.xsd
```

```xml
<xs:include schemaLocation="components/swipe.xsd" />
```

URLs, network retrieval, and paths such as `../shared.xsd` are rejected.
`schemaLocation` must use a plain local path: percent encoding, backslashes,
and surrounding whitespace are rejected because URI normalization could make
the compiler load a different file from the one inspected by the safety guard. Symlinks cannot authorize
dependencies outside the configured root directory. Each root is limited to
256 distinct referenced files, including itself; local include cycles are
visited once rather than recursively expanded forever.

The registry and custom completion caches share the complete transitive graph
of resolved paths, sizes, and nanosecond modification times, keyed by profile.
They inspect references before cache hits. Saving a deeply included schema
therefore refreshes both validation and autocomplete without restarting Django.

DTD and entity declarations are forbidden in root and included schemas. Both
compiler paths also disable entity expansion and reject compilation warnings,
including warnings on imported schemas, rather than silently dropping a
configured dependency and validating against an incomplete registry.

Do not use `xs:override` in `EXTRA_SCHEMAS`: it is rejected with the stable
`forbidden_schema_reference` validation code (`dj_hyperview.E012` during
configuration checks). The only allowed override belongs to the package's fixed
compatibility profile, not to arbitrary project schemas.

## Choose the bundled validation profile

`SCHEMA_PROFILE` defaults to `upstream-0.110.0`. The opt-in
`compatible-0.110.0` profile adds signed decimal percentage values only to the
nine Hyperview style margin attributes, preserving all other upstream types.
Custom elements still participate in either profile's validation and catalog.
See [percentage margin compatibility](configuration.md#opt-in-to-compatible-percentage-margins)
for configuration and the exact boundary.

Both profiles reuse the unchanged upstream completion catalog; runtime
catalog responses identify the selection in `schema_profile`. Arbitrary extra
schemas cannot replace the fixed overlay or broaden unrelated Hyperview types.

## Know the boundary

The schema validates rendered XML structure and feeds editor suggestions. It
does not implement a custom mobile component, register a Hyperview behavior,
or prove that the React Native client supports the element. The project must
implement and test that client-side contract separately.

Publication of a database template compiles Django template syntax but does not
render it. Values and branches that depend on runtime context are validated
when the final document or fragment is served.
