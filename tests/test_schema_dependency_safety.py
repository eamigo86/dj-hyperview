"""Transitive local schema safety and shared validation/catalog freshness."""

import os
from pathlib import Path

import pytest
from django.test import override_settings

from dj_hyperview import validate_hyperview_schema
from dj_hyperview.conf import get_settings
from dj_hyperview.exceptions import HyperviewConfigurationError, TemplateValidationError
from dj_hyperview.schema import _guard_local_references, get_hyperview_catalog

XS = "http://www.w3.org/2001/XMLSchema"
APP = "https://example.test/dependency-audit"
HV = "https://hyperview.org/hyperview"


def schema_text(body: str) -> str:
    """Wrap one controlled dependency fixture in a namespaced schema."""
    return (
        f'<xs:schema xmlns:xs="{XS}" xmlns:app="{APP}" '
        f'targetNamespace="{APP}" elementFormDefault="qualified">{body}</xs:schema>'
    )


def write_graph(tmp_path: Path) -> tuple[Path, Path]:
    """Create a three-level graph so leaf-only changes must invalidate both caches."""
    root, middle, leaf = [
        tmp_path / name for name in ("root.xsd", "middle.xsd", "leaf.xsd")
    ]
    root.write_text(
        schema_text('<xs:include schemaLocation="middle.xsd"/>'), encoding="utf-8"
    )
    middle.write_text(
        schema_text('<xs:include schemaLocation="leaf.xsd"/>'), encoding="utf-8"
    )
    leaf.write_text(schema_text('<xs:element name="thing"/>'), encoding="utf-8")
    return root, leaf


@pytest.mark.parametrize("profile", ["upstream-0.110.0", "compatible-0.110.0"])
def test_leaf_changes_refresh_both_catalog_and_validation(
    tmp_path: Path, profile: str
) -> None:
    """The completion catalog cannot lag behind a transitive validation change."""
    root, leaf = write_graph(tmp_path)
    document = f'<view xmlns="{HV}" xmlns:app="{APP}"><app:thing/></view>'
    with override_settings(
        HYPERVIEW={"EXTRA_SCHEMAS": [root], "SCHEMA_PROFILE": profile}
    ):
        validate_hyperview_schema(document)
        assert (
            get_hyperview_catalog()["elements"][f"{{{APP}}}thing"]["attributes"] == {}
        )
        leaf.write_text(
            schema_text(
                '<xs:element name="thing"><xs:complexType>'
                '<xs:attribute name="required-id" use="required"/>'
                "</xs:complexType></xs:element>"
            ),
            encoding="utf-8",
        )
        with pytest.raises(TemplateValidationError, match="\\[schema\\]"):
            validate_hyperview_schema(document)
        assert (
            get_hyperview_catalog()["elements"][f"{{{APP}}}thing"]["attributes"][
                "required-id"
            ]["required"]
            is True
        )


@pytest.mark.parametrize(
    "location",
    ["nested.xsd", "../outside.xsd", "https://example.test/remote.xsd", None],
)
def test_arbitrary_extra_schema_overrides_are_rejected(
    tmp_path: Path, location: str | None
) -> None:
    """The trusted bundled override is never permission for consumer overrides."""
    root = tmp_path / "root.xsd"
    attribute = "" if location is None else f' schemaLocation="{location}"'
    root.write_text(schema_text(f"<xs:override{attribute}/>"), encoding="utf-8")
    with pytest.raises(TemplateValidationError) as error:
        _guard_local_references(root)
    assert error.value.code == "forbidden_schema_reference"
    assert str(tmp_path) not in str(error.value)


@pytest.mark.parametrize("consumer", ["catalog", "validation"])
@pytest.mark.parametrize("replacement", ["override", "remote", "traversal"])
def test_mutated_dependencies_are_guarded_before_cache_hits(
    tmp_path: Path, consumer: str, replacement: str
) -> None:
    """Settings and root fingerprints staying unchanged cannot bypass safety."""
    root, leaf = write_graph(tmp_path)
    document = f'<view xmlns="{HV}"/>'
    with override_settings(HYPERVIEW={"EXTRA_SCHEMAS": [root]}):
        get_settings()
        get_hyperview_catalog()
        validate_hyperview_schema(document)
        references = {
            "override": '<xs:override schemaLocation="root.xsd"/>',
            "remote": '<xs:include schemaLocation="https://example.test/remote.xsd"/>',
            "traversal": '<xs:include schemaLocation="../outside.xsd"/>',
        }
        leaf.write_text(schema_text(references[replacement]), encoding="utf-8")
        with pytest.raises(TemplateValidationError) as error:
            if consumer == "catalog":
                get_hyperview_catalog()
            else:
                validate_hyperview_schema(document)
        assert error.value.code == "forbidden_schema_reference"


def test_dependency_cycles_are_visited_once(tmp_path: Path) -> None:
    """A local include cycle has a finite deterministic dependency identity."""
    root, leaf = write_graph(tmp_path)
    leaf.write_text(
        schema_text('<xs:include schemaLocation="root.xsd"/>'), encoding="utf-8"
    )
    assert len(_guard_local_references(root)) == 3


def test_symlink_loop_has_a_redacted_typed_error(tmp_path: Path) -> None:
    """Filesystem loops do not leak RuntimeError or platform-dependent paths."""
    root = tmp_path / "loop.xsd"
    root.symlink_to(root)
    with pytest.raises(TemplateValidationError) as error:
        _guard_local_references(root)
    assert error.value.code == "schema_invalid"
    assert str(tmp_path) not in str(error.value)


def test_dependency_graph_has_a_finite_file_budget(tmp_path: Path) -> None:
    """Broad local include graphs stop at a documented fixed file limit."""
    for index in range(257):
        reference = (
            f'<xs:include schemaLocation="{index + 1}.xsd"/>' if index < 256 else ""
        )
        (tmp_path / f"{index}.xsd").write_text(schema_text(reference), encoding="utf-8")
    with pytest.raises(TemplateValidationError) as error:
        _guard_local_references(tmp_path / "0.xsd")
    assert error.value.code == "schema_invalid"


@pytest.mark.parametrize("consumer", ["catalog", "validation"])
def test_safety_is_rechecked_when_leaf_fingerprint_is_unchanged(
    tmp_path: Path, consumer: str
) -> None:
    """A same-size same-mtime edit cannot reuse a cache hit to skip the guard."""
    root, leaf = write_graph(tmp_path)
    padded = leaf.read_text(encoding="utf-8") + " " * 512
    leaf.write_text(padded, encoding="utf-8")
    document = f'<view xmlns="{HV}"/>'
    with override_settings(HYPERVIEW={"EXTRA_SCHEMAS": [root]}):
        get_hyperview_catalog()
        validate_hyperview_schema(document)
        before = leaf.stat()
        forbidden = schema_text('<xs:override schemaLocation="root.xsd"/>')
        leaf.write_text(forbidden.ljust(len(padded)), encoding="utf-8")
        os.utime(leaf, ns=(before.st_atime_ns, before.st_mtime_ns))
        assert (leaf.stat().st_size, leaf.stat().st_mtime_ns) == (
            before.st_size,
            before.st_mtime_ns,
        )
        with pytest.raises(TemplateValidationError) as error:
            if consumer == "catalog":
                get_hyperview_catalog()
            else:
                validate_hyperview_schema(document)
        assert error.value.code == "forbidden_schema_reference"


@pytest.mark.parametrize("consumer", ["catalog", "validation"])
def test_symlink_dependency_escape_is_rechecked_after_a_cache_hit(
    tmp_path: Path, consumer: str
) -> None:
    """Replacing a previously safe dependency with an escaping link fails closed."""
    directory = tmp_path / "schema"
    directory.mkdir()
    root, leaf = write_graph(directory)
    outside = tmp_path / "outside.xsd"
    outside.write_text(schema_text('<xs:element name="thing"/>'), encoding="utf-8")
    document = f'<view xmlns="{HV}"/>'
    with override_settings(HYPERVIEW={"EXTRA_SCHEMAS": [root]}):
        get_hyperview_catalog()
        validate_hyperview_schema(document)
        leaf.unlink()
        leaf.symlink_to(outside)
        with pytest.raises(TemplateValidationError) as error:
            if consumer == "catalog":
                get_hyperview_catalog()
            else:
                validate_hyperview_schema(document)
        assert error.value.code == "forbidden_schema_reference"


def test_valid_dependency_cycle_compiles_and_catalogs(tmp_path: Path) -> None:
    """Cycle prevention does not prohibit XSD-supported local include cycles."""
    root, leaf = write_graph(tmp_path)
    leaf.write_text(
        schema_text(
            '<xs:include schemaLocation="root.xsd"/><xs:element name="thing"/>'
        ),
        encoding="utf-8",
    )
    with override_settings(HYPERVIEW={"EXTRA_SCHEMAS": [root]}):
        validate_hyperview_schema(f'<view xmlns="{HV}"/>')
        assert f"{{{APP}}}thing" in get_hyperview_catalog()["elements"]


def test_encoded_traversal_cannot_hide_behind_a_literal_local_decoy(
    tmp_path: Path,
) -> None:
    """URL-decoding by the XSD compiler cannot escape the Path-based guard."""
    safe = tmp_path / "safe"
    safe.mkdir()
    root = safe / "root.xsd"
    root.write_text(
        schema_text('<xs:include schemaLocation="%2e%2e/outside.xsd"/>'),
        encoding="utf-8",
    )
    (safe / "%2e%2e").mkdir()
    (safe / "%2e%2e" / "outside.xsd").write_text(
        schema_text('<xs:element name="decoy"/>'), encoding="utf-8"
    )
    (tmp_path / "outside.xsd").write_text(
        schema_text('<xs:element name="outside"/>'), encoding="utf-8"
    )
    with pytest.raises(TemplateValidationError) as error:
        _guard_local_references(root)
    assert error.value.code == "forbidden_schema_reference"


@pytest.mark.parametrize("on_include", [False, True])
def test_xml_base_does_not_redirect_dependency_resolution(
    tmp_path: Path, on_include: bool
) -> None:
    """The supported compiler and guard agree on XML base attribute behavior."""
    import xmlschema

    safe = tmp_path / "safe"
    safe.mkdir()
    root = safe / "root.xsd"
    include = '<xs:include schemaLocation="dependency.xsd"/>'
    source = schema_text(include)
    target = "<xs:include " if on_include else "<xs:schema "
    source = source.replace(target, target + 'xml:base="../" ')
    root.write_text(source, encoding="utf-8")
    dependency = safe / "dependency.xsd"
    dependency.write_text(schema_text('<xs:element name="inside"/>'), encoding="utf-8")
    (tmp_path / "dependency.xsd").write_text(
        schema_text('<xs:element name="outside"/>'), encoding="utf-8"
    )
    assert set(_guard_local_references(root)) == {root.resolve(), dependency.resolve()}
    compiled = xmlschema.XMLSchema11(root, allow="local", use_fallback=False)
    assert "inside" in compiled.elements and "outside" not in compiled.elements


def test_configured_root_boundary_is_preserved_for_entrypoint_symlinks(
    tmp_path: Path,
) -> None:
    """A root symlink cannot authorize dependencies outside its configured root."""
    configured = tmp_path / "configured"
    configured.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    entrypoint = target / "root.xsd"
    entrypoint.write_text(
        schema_text('<xs:include schemaLocation="leaf.xsd"/>'), encoding="utf-8"
    )
    (target / "leaf.xsd").write_text(
        schema_text('<xs:element name="thing"/>'), encoding="utf-8"
    )
    link = configured / "linked.xsd"
    link.symlink_to(entrypoint)
    with pytest.raises(TemplateValidationError) as error:
        _guard_local_references(link)
    assert error.value.code == "forbidden_schema_reference"


def hidden_reference_schema(reference: str = "override") -> str:
    """Build a DTD entity that would expand into an unguarded schema reference."""
    return (
        f'<!DOCTYPE xs:schema [<!ENTITY hidden \'<xs:{reference} xmlns:xs="{XS}" '
        'schemaLocation="../outside.xsd"/>\'>]>' + schema_text("&hidden;")
    )


@pytest.mark.parametrize("reference", ["override", "include", "redefine"])
def test_dtd_entities_cannot_hide_schema_references(
    tmp_path: Path, reference: str
) -> None:
    """A disabled entity resolver must not hide markup from the dependency guard."""
    root = tmp_path / "root.xsd"
    root.write_text(hidden_reference_schema(reference), encoding="utf-8")
    with pytest.raises(TemplateValidationError) as error:
        _guard_local_references(root)
    assert error.value.code == "forbidden_declaration"


@pytest.mark.parametrize("consumer", ["catalog", "validation"])
def test_public_schema_consumers_reject_initial_entity_references(
    tmp_path: Path, consumer: str
) -> None:
    """A hidden external declaration cannot enter either public registry API."""
    safe = tmp_path / "safe"
    safe.mkdir()
    root = safe / "root.xsd"
    root.write_text(hidden_reference_schema(), encoding="utf-8")
    (tmp_path / "outside.xsd").write_text(
        schema_text('<xs:element name="outside"><xs:complexType/></xs:element>'),
        encoding="utf-8",
    )
    with override_settings(HYPERVIEW={"EXTRA_SCHEMAS": [root]}):
        with pytest.raises(HyperviewConfigurationError, match="dj_hyperview.E012"):
            if consumer == "catalog":
                get_hyperview_catalog()
            else:
                validate_hyperview_schema(f'<view xmlns="{HV}"/>')


@pytest.mark.parametrize("consumer", ["catalog", "validation"])
@pytest.mark.parametrize("target", ["root", "leaf"])
def test_entity_guards_precede_cached_public_schema_results(
    tmp_path: Path, consumer: str, target: str
) -> None:
    """A root or dependency edit is guarded even when its cache fingerprint matches."""
    root, leaf = write_graph(tmp_path)
    mutated = root if target == "root" else leaf
    padded = mutated.read_text(encoding="utf-8") + " " * 512
    mutated.write_text(padded, encoding="utf-8")
    with override_settings(HYPERVIEW={"EXTRA_SCHEMAS": [root]}):
        get_hyperview_catalog()
        validate_hyperview_schema(f'<view xmlns="{HV}"/>')
        before = mutated.stat()
        mutated.write_text(
            hidden_reference_schema().ljust(len(padded)), encoding="utf-8"
        )
        os.utime(mutated, ns=(before.st_atime_ns, before.st_mtime_ns))
        assert (mutated.stat().st_size, mutated.stat().st_mtime_ns) == (
            before.st_size,
            before.st_mtime_ns,
        )
        with pytest.raises(TemplateValidationError) as error:
            if consumer == "catalog":
                get_hyperview_catalog()
            else:
                validate_hyperview_schema(f'<view xmlns="{HV}"/>')
        assert error.value.code == "forbidden_declaration"


@pytest.mark.parametrize("registry", [False, True])
def test_compilers_forbid_entities_independently_of_reference_guard(
    tmp_path: Path, registry: bool
) -> None:
    """Compiler-level entity defusing is independent defense against parser drift."""
    from dj_hyperview.schema import (
        _compile_registry,
        _compile_schema,
        _SchemaDependencies,
    )

    safe = tmp_path / "safe"
    safe.mkdir()
    root = safe / "root.xsd"
    root.write_text(hidden_reference_schema(), encoding="utf-8")
    (tmp_path / "outside.xsd").write_text(
        schema_text('<xs:element name="outside"><xs:complexType/></xs:element>'),
        encoding="utf-8",
    )
    with pytest.raises(TemplateValidationError) as error:
        if registry:
            _compile_registry("upstream-0.110.0", (_SchemaDependencies(str(root), ()),))
        else:
            _compile_schema(root)
    assert error.value.code == "schema_invalid"


def test_doctype_without_entities_is_forbidden_but_comment_text_is_inert(
    tmp_path: Path,
) -> None:
    """The declaration rule is structural, not a substring ban on documentation."""
    root = tmp_path / "root.xsd"
    root.write_text("<!DOCTYPE xs:schema>" + schema_text(""), encoding="utf-8")
    with pytest.raises(TemplateValidationError) as error:
        _guard_local_references(root)
    assert error.value.code == "forbidden_declaration"
    root.write_text(schema_text("<!-- <!DOCTYPE xs:schema> -->"), encoding="utf-8")
    assert _guard_local_references(root) == (root.resolve(),)


@pytest.mark.parametrize(
    "location", [" ../outside.xsd", " ../outside.xsd ", r"..\outside.xsd"]
)
def test_normalized_reference_cannot_hide_behind_a_literal_local_decoy(
    tmp_path: Path, location: str
) -> None:
    """Whitespace and backslash normalization cannot change the inspected file."""
    safe = tmp_path / "safe"
    safe.mkdir()
    root = safe / "root.xsd"
    root.write_text(
        schema_text(f'<xs:include schemaLocation="{location}"/>'), encoding="utf-8"
    )
    decoy = safe / location
    decoy.parent.mkdir(parents=True, exist_ok=True)
    decoy.write_text(schema_text(""), encoding="utf-8")
    (tmp_path / "outside.xsd").write_text(
        schema_text('<xs:element name="outside"><xs:complexType/></xs:element>'),
        encoding="utf-8",
    )
    with pytest.raises(TemplateValidationError) as error:
        _guard_local_references(root)
    assert error.value.code == "forbidden_schema_reference"


@pytest.mark.parametrize("registry", [False, True])
@pytest.mark.parametrize("nested", [False, True])
def test_compilation_warnings_cannot_silently_omit_schema_dependencies(
    tmp_path: Path, registry: bool, nested: bool
) -> None:
    """Both compiler paths reject incomplete root and transitive schema results."""
    from dj_hyperview.schema import (
        _compile_registry,
        _compile_schema,
        _SchemaDependencies,
    )

    root = tmp_path / "root.xsd"
    missing = '<xs:include schemaLocation="missing.xsd"/>'
    if nested:
        root.write_text(
            schema_text('<xs:include schemaLocation="leaf.xsd"/>'), encoding="utf-8"
        )
        (tmp_path / "leaf.xsd").write_text(schema_text(missing), encoding="utf-8")
    else:
        root.write_text(schema_text(missing), encoding="utf-8")

    with pytest.raises(TemplateValidationError) as error:
        if registry:
            _compile_registry("upstream-0.110.0", (_SchemaDependencies(str(root), ()),))
        else:
            _compile_schema(root)
    assert error.value.code == "schema_invalid"
    assert str(tmp_path) not in str(error.value)
