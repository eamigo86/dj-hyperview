"""Documentation examples are run, compiled and bound against the code they show."""

import ast
import inspect
import re
from pathlib import Path

import pytest

import dj_hyperview
from dj_hyperview import HyperviewEngine, TemplateResolver
from dj_hyperview.contrib.database import services

ROOT = Path(__file__).parents[1]
PAGES = sorted((ROOT / "docs").glob("*.md")) + [ROOT / "README.md"]


def _read_document(name: str) -> str:
    return (ROOT / "docs" / name).read_text()


def _python_blocks(text: str) -> list[str]:
    return re.findall(r"```python\n(.*?)```", text, flags=re.DOTALL)


def _documented_callables() -> dict[str, object]:
    """Map the public API and the database services to their callables."""
    names = {name: getattr(dj_hyperview, name) for name in dj_hyperview.__all__}
    names.update({name: getattr(services, name) for name in services.__all__})
    return {name: value for name, value in names.items() if callable(value)}


@pytest.mark.parametrize("page", PAGES, ids=lambda page: page.name)
def test_python_examples_compile(page: Path) -> None:
    for block in _python_blocks(page.read_text()):
        compile(block, str(page), "exec")


@pytest.mark.parametrize("page", PAGES, ids=lambda page: page.name)
def test_documented_calls_match_the_signatures_they_call(page: Path) -> None:
    """A call to a public name must bind to that name's real signature."""
    callables = _documented_callables()
    mismatches = []
    for block in _python_blocks(page.read_text()):
        for node in ast.walk(ast.parse(block)):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            target = callables.get(node.func.id)
            if target is None or any(
                isinstance(argument, ast.Starred) for argument in node.args
            ):
                continue
            if any(keyword.arg is None for keyword in node.keywords):
                continue
            try:
                inspect.signature(target).bind(
                    *[None] * len(node.args), **{k.arg: None for k in node.keywords}
                )
            except TypeError as error:
                mismatches.append(f"{node.func.id}: {error}")

    assert mismatches == []


def test_security_csrf_example_passes_automatic_render_validation(
    tmp_path: Path,
) -> None:
    """The documented CSRF form uses real HXML rather than HTML form attributes."""
    from django.test import RequestFactory, override_settings

    page = _read_document("security.md")
    blocks = re.findall(r"```xml\n(.*?)```", page, flags=re.DOTALL)
    source = next(block for block in blocks if "hv_csrf_token" in block)
    (tmp_path / "security.xml").write_text(source, encoding="utf-8")
    with override_settings(
        HYPERVIEW={
            "TEMPLATE_DIRS": [tmp_path],
            "SOURCES": [{"BACKEND": "dj_hyperview.sources.FileSystemSource"}],
        }
    ):
        rendered = HyperviewEngine(TemplateResolver.from_settings()).render_hxml(
            "security.xml", request=RequestFactory().get("/")
        )
    assert 'name="csrfmiddlewaretoken"' in rendered
    assert 'hide="true"' in rendered


def test_security_text_safety_example_cleans_and_escapes_rendered_values(
    tmp_path: Path,
) -> None:
    """The documented filters strip illegal characters and keep attribute newlines."""
    from types import SimpleNamespace

    from django.test import override_settings

    page = _read_document("security.md")
    blocks = re.findall(r"```xml\n(.*?)```", page, flags=re.DOTALL)
    source = next(block for block in blocks if "hv_text" in block)
    (tmp_path / "text.xml").write_text(source, encoding="utf-8")
    task = SimpleNamespace(title='Buy\x01 milk\n& "eggs"')
    with override_settings(
        HYPERVIEW={
            "TEMPLATE_DIRS": [tmp_path],
            "SOURCES": [{"BACKEND": "dj_hyperview.sources.FileSystemSource"}],
        }
    ):
        rendered = HyperviewEngine(TemplateResolver.from_settings()).render_hxml(
            "text.xml", {"task": task}
        )
    assert 'value="Buy milk&#10;&amp; &quot;eggs&quot;"' in rendered
    assert "<text>Buy milk\n&amp; &quot;eggs&quot;</text>" in rendered


def test_configuration_guide_documents_exactly_the_emitted_check_ids() -> None:
    """Every system check the package can emit has a row, and no row is stale."""
    source = (ROOT / "src/dj_hyperview/checks.py").read_text()
    emitted = set(re.findall(r"\b([EW]\d{3})\b", source))
    page = _read_document("configuration.md")
    section = page.split("## System check reference", 1)[1]
    documented = set(re.findall(r"^\| `dj_hyperview\.([EW]\d{3})` \|", section, re.M))

    assert emitted
    assert documented == emitted
