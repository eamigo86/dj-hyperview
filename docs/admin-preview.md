# Preview unsaved templates

Preview renders the current Ace buffer beside the editor without saving it.
Choose configured example data, select **Preview**, and inspect the static view
and diagnostics. **Format HXML** remains separate: preview never changes the
source, selection, or undo history.

This is an authoring aid, **not the native Hyperview client**. It does not execute
behaviors, navigate, submit forms, or load images and other external resources.
Unsupported components appear as placeholders with warnings.

## Enable preview

Install the [editor extra and database Admin](database-admin.md), then opt in:

```python
HYPERVIEW = {
    "ADMIN": {
        "EDITOR": True,
        "PREVIEW": {
            "ENABLED": True,
            "SCENARIOS": {
                "with-tasks": {
                    "LABEL": "With tasks",
                    "CONTEXT": {
                        "tasks": [{"title": "Review the draft", "complete": False}],
                    },
                },
                "empty-list": {
                    "LABEL": "Empty list",
                    "CONTEXT": {"tasks": []},
                },
            },
        },
    },
}
```

Merge this configuration with your existing sources and other options. Preview
is disabled by default and requires `ADMIN.EDITOR`. Without configured scenarios,
it offers `empty` (No example data) and warns that missing variables may render
empty. It does not invent fixture data or detect every missing variable.

Only editable forms include preview controls and assets. Active staff users must
pass the same authoritative `ADMIN.PERMISSION` policy as add/change operations;
read-only access to the template or completion catalog is not sufficient. Change
preview also checks the stored object and its change permission. Custom
`AdminSite` registrations use their own preview endpoint namespace.

## Use a trusted provider or wrapper

`CONTEXT` accepts a mapping, callable, or dotted import path to a callable:

```python
# sample_app/preview.py


def task_examples(request, template_name):
    return {"tasks": [{"title": "Example task", "complete": False}]}
```

```python
PREVIEW_SCENARIOS = {
    "with-tasks": {
        "LABEL": "With tasks",
        "CONTEXT": "sample_app.preview.task_examples",
        "ROOT_TEMPLATE": "screens/tasks.xml",
    },
}
```

The optional `ROOT_TEMPLATE` must actually include or extend the draft under its
current name. Otherwise preview returns `wrapper_missing_draft` rather than
showing an unrelated screen. Other dependencies use configured source precedence;
the unsaved draft wins for its own name, including when the stored row is inactive
or the editor has renamed it. Extending the draft's own name does not retrieve an
older published version.

The provider receives the request and template name, but neither request, user,
session nor context processors enter the render context automatically. Use
explicit synthetic examples where possible. The browser sends only the name,
original content, and configured scenario ID—not context or provider paths.
Static mappings are copied for each preview request.

## Understand results

- **Valid**: source and rendered output passed validation; the visual view remains
  an approximation. Warnings identify unsupported visual features.
- **Preview failed**: inspect the first reliable diagnostic. Source positions can
  navigate to Ace only for the current draft; included-template positions name
  the dependency instead. Rendered positions refer to the read-only HXML output,
  not the original Django source. Bounded failed XML is shown as text, never as a
  visual preview.
- **Out of date**: content, name or scenario changed after preview. Old requests
  are cancelled and stale responses discarded. Preview again before trusting it.

Preview always validates rendered XML against the configured schema—or the
selected Hyperview XSD profile by default—including when
`VALIDATION.MODE="publish"` skips ordinary rendered-document validation. A screen
accepted by the native client can still fail XSD validation.
`compatible-0.110.0` only addresses the documented margin percentages; it does
not promise complete native-client compatibility or relax unrelated attributes.
This feature adds no schema exceptions.

## Static rendering support

The panel supports basic screen/body/header/view structure, text, lists, and
noninteractive form-field approximations. Multiple screens have a local selector.
Styles are resolved per screen without placing source IDs into the Admin DOM.
Supported style families are:

- Width/height and min/max dimensions, margin/padding, gap, border width/radius.
- Flex direction, wrapping, alignment, justification, growth/shrinkage.
- Foreground/background/border colors and opacity.
- Font size, line height, letter spacing, weight/style, text alignment/decoration,
  and generic serif/sans-serif/monospace families.

Only explicit safe values are translated. Arbitrary CSS, style modifiers,
content-container styles, native/custom components and media are not simulated.
Placeholders do not request their source URLs. A sandboxed iframe with no granted
permissions and a restrictive CSP isolates the generated view.

## Safety boundary

The endpoint is POST-only, CSRF-protected, permission-checked, bounded, and
non-cacheable. Package preview code uses a temporary render engine without the
shared template cache; it does not save rows, publish revisions, or invalidate
caches. No migration or app restart is performed by using preview.

**Trusted Django tags, filters, providers and sources are still executable project
code.** Preview is not a sandbox for their CPU use, database queries, external
requests, or other effects. Providers can intentionally query project data;
review that code before enabling it. A dependency snapshot is not an atomic
publication transaction, and a successful preview does not guarantee a later
save will pass publication checks.
