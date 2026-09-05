"""Release artifact-staging workflow contract tests."""

from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

from tests.workflow_contract import audit_workflow, load_workflow

ROOT = Path(__file__).parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
CONTRACT = ROOT / "tests" / "fixtures" / "release_contract.yml"
VIOLATION = "release: semantic contract is not approved"
PINS = {
    "actions/checkout": "3d3c42e5aac5ba805825da76410c181273ba90b1",
    "actions/setup-python": "5fda3b95a4ea91299a34e894583c3862153e4b97",
    "actions/download-artifact": "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
    "actions/upload-artifact": "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
    "actions/upload-pages-artifact": "fc324d3547104276b827a68afc52ff2a11cc49c9",
    "actions/configure-pages": "45bfe0192ca1faeb007ade9deae92b16b8254a0d",
    "actions/deploy-pages": "368f82528645a54fb793d4d04e342629a3f51346",
    "pypa/gh-action-pypi-publish": "dc37677b2e1c63e2034f94d8a5b11f265b73ba33",
}


def _audit(text: str) -> list[str]:
    """Audit candidate source against the readable release contract."""
    return audit_workflow(text, CONTRACT.read_text(), label="release")


def test_release_workflow_matches_the_reviewed_contract() -> None:
    """The checked-in release workflow satisfies its closed contract."""
    assert _audit(WORKFLOW.read_text()) == []


def test_release_starts_only_for_version_tags_and_is_read_only() -> None:
    """Only version-tag pushes start the unprivileged staging workflow."""
    workflow = load_workflow(WORKFLOW.read_text())

    assert workflow["on"] == {"push": {"tags": ["v*.*.*"]}}
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["jobs"]) == {"metadata", "ci", "stage", "pypi", "pages"}


def test_release_validates_metadata_before_one_reusable_ci_build() -> None:
    """Tag identity gates the one reusable CI and Redis build path."""
    workflow = load_workflow(WORKFLOW.read_text())
    metadata = workflow["jobs"]["metadata"]
    ci = workflow["jobs"]["ci"]
    commands = [step["run"] for step in metadata["steps"] if "run" in step]

    assert commands == ['python -m tools.check_release_tag "${GITHUB_REF_NAME}"']
    assert ci == {
        "needs": "metadata",
        "permissions": {"contents": "read"},
        "uses": "./.github/workflows/ci.yml",
        "with": {"redis": "true"},
    }
    text = WORKFLOW.read_text()
    assert "uv build" not in text
    assert "zensical build" not in text


def test_stage_consumes_one_candidate_and_separates_immutable_artifacts() -> None:
    """Staging validates one candidate before dist and Pages uploads."""
    workflow = load_workflow(WORKFLOW.read_text())
    stage = workflow["jobs"]["stage"]
    uses = [step["uses"] for step in stage["steps"] if "uses" in step]
    download, distributions, pages = [step for step in stage["steps"] if "uses" in step]
    script = next(step["run"] for step in stage["steps"] if "run" in step)

    assert stage["needs"] == "ci"
    assert stage["permissions"] == {"contents": "read"}
    assert uses == [
        f"actions/download-artifact@{PINS['actions/download-artifact']}",
        f"actions/upload-artifact@{PINS['actions/upload-artifact']}",
        f"actions/upload-pages-artifact@{PINS['actions/upload-pages-artifact']}",
    ]
    assert download["with"] == {
        "name": "release-candidate-${{ github.sha }}",
        "path": "candidate",
    }
    assert "test -f candidate/site/index.html" in script
    assert "*.whl" in script and "*.tar.gz" in script
    assert "sha256sum > candidate/SHA256SUMS" in script
    assert distributions["with"] == {
        "name": "release-distributions-${{ github.sha }}",
        "path": "candidate/dist/\ncandidate/SHA256SUMS\n",
        "if-no-files-found": "error",
        "retention-days": "30",
    }
    assert pages["with"] == {"path": "candidate/site/"}


def test_pypi_uses_only_oidc_and_the_immutable_distribution() -> None:
    """Trusted Publishing consumes the staged distribution without secrets."""
    job = load_workflow(WORKFLOW.read_text())["jobs"]["pypi"]
    publish_action = (
        "pypa/gh-action-pypi-publish@" + PINS["pypa/gh-action-pypi-publish"]
    )

    assert job["needs"] == "stage"
    assert job["environment"] == {"name": "pypi"}
    assert job["permissions"] == {"contents": "read", "id-token": "write"}
    assert job["steps"] == [
        {
            "uses": f"actions/download-artifact@{PINS['actions/download-artifact']}",
            "with": {
                "name": "release-distributions-${{ github.sha }}",
                "path": "candidate",
            },
        },
        {
            "name": "Publish with PyPI Trusted Publishing",
            "uses": publish_action,
            "with": {"packages-dir": "candidate/dist/"},
        },
    ]
    assert "secrets" not in job


def test_pages_deploys_only_after_successful_pypi_publication() -> None:
    """Pages deploys the prebuilt artifact only after the PyPI job succeeds."""
    job = load_workflow(WORKFLOW.read_text())["jobs"]["pages"]

    assert job["needs"] == "pypi"
    assert job["environment"] == {
        "name": "github-pages",
        "url": "${{ steps.deployment.outputs.page_url }}",
    }
    assert job["permissions"] == {
        "contents": "read",
        "pages": "write",
        "id-token": "write",
    }
    assert job["steps"] == [
        {"uses": f"actions/configure-pages@{PINS['actions/configure-pages']}"},
        {
            "name": "Deploy published documentation",
            "id": "deployment",
            "uses": f"actions/deploy-pages@{PINS['actions/deploy-pages']}",
        },
    ]
    assert all("run" not in step for step in job["steps"])


@pytest.mark.parametrize(
    "mutate",
    [
        lambda text: text.replace("needs: stage", "needs: ci", 1),
        lambda text: text.replace("name: pypi", "name: production", 1),
        lambda text: text.replace("id-token: write", "id-token: read", 1),
        lambda text: text.replace("needs: pypi", "needs: stage", 1),
        lambda text: text.replace("pages: write", "pages: read", 1),
        lambda text: text.replace(
            "packages-dir: candidate/dist/", "password: secret", 1
        ),
        lambda text: text.replace("actions/deploy-pages", "actions/upload-artifact", 1),
        lambda text: text.replace("contents: read", "contents: write", 4),
    ],
)
def test_release_audit_rejects_publish_and_deploy_drift(
    mutate: Callable[[str], str],
) -> None:
    """Identity, OIDC, ordering, actions, and permissions fail closed."""
    assert _audit(mutate(WORKFLOW.read_text())) == [VIOLATION]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda text: text.replace('tags: ["v*.*.*"]', "branches: [main]", 1),
        lambda text: text.replace("contents: read", "contents: write", 1),
        lambda text: text.replace("redis: true", 'redis: "true"', 1),
        lambda text: text.replace("needs: metadata", "needs: []", 1),
        lambda text: text.replace("tools.check_release_tag", "tools.test_matrix", 1),
        lambda text: text.replace("release-candidate-${{ github.sha }}", "latest", 1),
        lambda text: text.replace("sha256sum >", "sha256sum || true >", 1),
        lambda text: text.replace("path: candidate/site/", "path: candidate/dist/", 1),
        lambda text: text.replace(
            f"actions/checkout@{PINS['actions/checkout']}",
            f"example/evil@{'a' * 40}",
            1,
        ),
        lambda text: text.replace(
            "jobs:\n", "jobs:\n  publish:\n    uses: example/pypi.yml@main\n", 1
        ),
    ],
)
def test_release_audit_rejects_semantic_mutations(
    mutate: Callable[[str], str],
) -> None:
    """Trigger, identity, artifact, action, and publish drift fail closed."""
    assert _audit(mutate(WORKFLOW.read_text())) == [VIOLATION]


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        ("", "release: mapping contract is not approved"),
        ("jobs: [", "release: source is not valid YAML"),
        ("[]", "release: mapping contract is not approved"),
        (
            "on: {}\npermissions: {}\njobs: {stage: []}\n",
            "release: stage execution shape is not approved",
        ),
        (
            "on: {}\npermissions: {}\njobs: []\n",
            "release: mapping contract is not approved",
        ),
        (
            "on: {}\npermissions: {}\njobs: {stage: {steps: {}}}\n",
            "release: stage execution shape is not approved",
        ),
    ],
)
def test_release_audit_normalizes_malformed_inputs(
    candidate: str, expected: str
) -> None:
    """Malformed sources and execution shapes produce stable violations."""
    assert _audit(candidate) == [expected]


def test_release_audit_ignores_comments_and_scalar_string_style() -> None:
    """Comments, blanks, and equivalent string quotes remain formatting."""
    mutated = ("# tag releases only\n\n" + WORKFLOW.read_text()).replace(
        'python-version: "3.12"', "python-version: '3.12'", 1
    )

    assert _audit(mutated) == []


@pytest.mark.parametrize("node", [None, yaml.Node("!unexpected", "value", None, None)])
def test_release_audit_rejects_unexpected_yaml_nodes(
    monkeypatch: pytest.MonkeyPatch, node: yaml.Node | None
) -> None:
    """Missing and unsupported composed nodes fail closed."""
    monkeypatch.setattr(yaml, "compose", lambda *args, **kwargs: node)

    assert _audit(WORKFLOW.read_text()) == ["release: tagged contract is not approved"]
