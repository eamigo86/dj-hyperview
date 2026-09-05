"""Manual documentation preview workflow contract tests."""

from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "docs-preview.yml"
WORKFLOW_CONTRACT = ROOT / "tests" / "fixtures" / "docs_preview_contract.yml"
ACTION_PINS = {
    "actions/checkout": "3d3c42e5aac5ba805825da76410c181273ba90b1",
    "actions/setup-python": "5fda3b95a4ea91299a34e894583c3862153e4b97",
    "astral-sh/setup-uv": "20cfd1bf945f4377ade1205e4dbc17946fc9a30d",
    "actions/upload-artifact": "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
}
SEMANTIC_VIOLATION = "preview: semantic contract is not approved"


def test_preview_workflow_matches_the_reviewed_contract() -> None:
    """The checked-in preview workflow satisfies its closed contract."""
    assert _audit_workflow(WORKFLOW.read_text()) == []


def test_preview_is_manual_and_read_only() -> None:
    """Preview can only be dispatched manually with read-only contents."""
    workflow = _workflow(WORKFLOW.read_text())

    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["jobs"]) == {"preview"}


def test_preview_uses_pinned_tools_and_uploads_the_site() -> None:
    """Preview builds strict docs and uploads one ordinary site artifact."""
    workflow = _workflow(WORKFLOW.read_text())
    job = workflow["jobs"]["preview"]
    uses = [step["uses"] for step in job["steps"] if "uses" in step]
    runs = [step["run"] for step in job["steps"] if "run" in step]
    upload = job["steps"][-1]

    assert uses == [f"{name}@{sha}" for name, sha in ACTION_PINS.items()]
    assert runs == [
        "uv sync --locked",
        "uv run zensical build --clean --strict -f zensical.yml",
    ]
    assert upload["with"] == {
        "name": "documentation-preview-${{ github.sha }}",
        "path": "site/",
        "if-no-files-found": "error",
        "retention-days": "7",
    }
    lowered = WORKFLOW.read_text().lower()
    assert "deploy-pages" not in lowered
    assert "upload-pages-artifact" not in lowered
    assert "pages: write" not in lowered
    assert "id-token: write" not in lowered
    assert "pypi" not in lowered
    assert "publish" not in lowered


@pytest.mark.parametrize(
    "mutate",
    [
        lambda text: text.replace("  workflow_dispatch:\n", "  push:\n", 1),
        lambda text: text.replace("contents: read", "contents: write", 1),
        lambda text: text.replace(
            f"actions/checkout@{ACTION_PINS['actions/checkout']}",
            f"example/evil@{'a' * 40}",
            1,
        ),
        lambda text: text.replace("uv sync --locked", "uv sync --all-groups", 1),
        lambda text: text.replace("run: uv sync --locked", "run: []", 1),
        lambda text: text.replace(
            "uv run zensical build --clean --strict -f zensical.yml",
            "uv run zensical build --clean --strict -f zensical.yml || true",
            1,
        ),
        lambda text: text.replace(
            "actions/upload-artifact", "actions/upload-pages-artifact", 1
        ),
        lambda text: text.replace("          path: site/", "          path: dist/", 1),
        lambda text: text.replace(
            "          if-no-files-found: error",
            "          if-no-files-found: warn",
            1,
        ),
        lambda text: text.replace(
            "          retention-days: 7", "          retention-days: 90", 1
        ),
        lambda text: text.replace(
            "jobs:\n", "jobs:\n  publish:\n    uses: example/pypi.yml@main\n", 1
        ),
    ],
)
def test_preview_audit_rejects_semantic_mutations(
    mutate: Callable[[str], str],
) -> None:
    """Unreviewed triggers, commands, permissions, and deploys fail closed."""
    assert _audit_workflow(mutate(WORKFLOW.read_text())) == [SEMANTIC_VIOLATION]


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        ("", "preview: mapping contract is not approved"),
        ("jobs: [", "preview: source is not valid YAML"),
        ("[]", "preview: mapping contract is not approved"),
        (
            "on: []\npermissions: {}\njobs: {}\n",
            "preview: mapping contract is not approved",
        ),
        (
            "on: {workflow_dispatch: null}\npermissions: {contents: read}\n"
            "jobs: {preview: []}\n",
            "preview: execution shape is not approved",
        ),
        (
            "on: {workflow_dispatch: null}\npermissions: {contents: read}\n"
            "jobs: {preview: {steps: {}}}\n",
            "preview: execution shape is not approved",
        ),
        (
            "on: {workflow_dispatch: null}\npermissions: {contents: read}\n"
            "jobs: {preview: {steps: [broken]}}\n",
            "preview: execution shape is not approved",
        ),
    ],
)
def test_preview_audit_normalizes_malformed_inputs(
    candidate: str, expected: str
) -> None:
    """Malformed YAML and workflow structures return stable violations."""
    assert _audit_workflow(candidate) == [expected]


def test_preview_audit_preserves_yaml_scalar_types() -> None:
    """A numeric retention policy cannot become a string silently."""
    mutated = WORKFLOW.read_text().replace("retention-days: 7", 'retention-days: "7"')

    assert _audit_workflow(mutated) == [SEMANTIC_VIOLATION]


def test_preview_audit_normalizes_unexpected_yaml_nodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unexpected composed nodes fail closed through a stable diagnostic."""
    monkeypatch.setattr(
        yaml,
        "compose",
        lambda *args, **kwargs: yaml.Node("!unexpected", "value", None, None),
    )

    assert _audit_workflow(WORKFLOW.read_text()) == [
        "preview: tagged contract is not approved"
    ]


def test_preview_audit_ignores_comments_blanks_and_string_style() -> None:
    """Formatting-only changes do not alter the approved workflow."""
    mutated = (
        ("# manual only\n\n" + WORKFLOW.read_text())
        .replace('python-version: "3.12"', "python-version: '3.12'", 1)
        .replace(
            "        run: uv sync --locked\n",
            "        run: |\n"
            "          # default groups only\n"
            "          uv sync --locked\n",
            1,
        )
    )

    assert _audit_workflow(mutated) == []


def _workflow(text: str) -> object:
    """Load workflow YAML while retaining the literal on key."""
    return yaml.load(text, Loader=yaml.BaseLoader)


def _normalize_run(script: object) -> str | None:
    """Normalize blank and full-line comment formatting in run scripts."""
    if not isinstance(script, str):
        return None
    return "\n".join(
        stripped
        for line in script.splitlines()
        if (stripped := line.strip()) and not stripped.startswith("#")
    )


def _semantic(value: object) -> object:
    """Normalize harmless shell formatting while preserving YAML values."""
    if isinstance(value, dict):
        return {
            key: _normalize_run(item) if key == "run" else _semantic(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_semantic(item) for item in value]
    return value


def _node_signature(
    node: yaml.Node, *, normalize_run: bool = False
) -> tuple[object, ...]:
    """Represent YAML nodes with order-aware sequences and scalar tags."""
    if isinstance(node, yaml.ScalarNode):
        value = _normalize_run(node.value) if normalize_run else node.value
        return ("scalar", node.tag, value)
    if isinstance(node, yaml.SequenceNode):
        return (
            "sequence",
            node.tag,
            tuple(_node_signature(item) for item in node.value),
        )
    if isinstance(node, yaml.MappingNode):
        pairs = (
            (
                _node_signature(key),
                _node_signature(
                    value,
                    normalize_run=isinstance(key, yaml.ScalarNode)
                    and key.value == "run",
                ),
            )
            for key, value in node.value
        )
        return ("mapping", node.tag, tuple(sorted(pairs, key=repr)))
    raise TypeError("unsupported YAML node")


def _tagged_signature(text: str) -> tuple[object, ...]:
    """Compose YAML into a canonical signature that retains scalar tags."""
    node = yaml.compose(text, Loader=yaml.SafeLoader)
    if node is None:
        raise TypeError("missing YAML node")
    return _node_signature(node)


def _audit_workflow(text: str) -> list[str]:
    """Return deterministic violations for a preview workflow candidate."""
    try:
        workflow = _workflow(text)
        contract_text = WORKFLOW_CONTRACT.read_text()
        contract = _workflow(contract_text)
    except yaml.YAMLError:
        return ["preview: source is not valid YAML"]
    if not isinstance(workflow, dict):
        return ["preview: mapping contract is not approved"]
    if not all(
        isinstance(workflow.get(key), dict) for key in ("on", "permissions", "jobs")
    ):
        return ["preview: mapping contract is not approved"]
    preview = workflow["jobs"].get("preview")
    if not isinstance(preview, dict) or not isinstance(preview.get("steps"), list):
        return ["preview: execution shape is not approved"]
    if any(not isinstance(step, dict) for step in preview["steps"]):
        return ["preview: execution shape is not approved"]
    try:
        tags_match = _tagged_signature(text) == _tagged_signature(contract_text)
    except yaml.YAMLError:
        return ["preview: source is not valid YAML"]
    except TypeError:
        return ["preview: tagged contract is not approved"]
    semantics_match = _semantic(workflow) == _semantic(contract)
    if not tags_match or not semantics_match:
        return [SEMANTIC_VIOLATION]
    return []
