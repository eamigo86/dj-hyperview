"""Invariants of the GitHub workflows, stated as relationships.

The workflows are read as data. Nothing here pins a step, a version or a
command, so ordinary workflow maintenance (an action bump, a new job) does not
touch these tests; they fail only when a safety property is lost.
"""

import re
import tomllib
from pathlib import Path

import pytest
import yaml
from packaging.version import Version

WORKFLOW_DIR = Path(__file__).parents[1] / ".github" / "workflows"
WORKFLOWS = sorted(WORKFLOW_DIR.glob("*.yml"))
# The only jobs allowed to hold a write scope, and exactly which ones.
PUBLISHING_SCOPES = {
    ("release.yml", "pypi"): {"id-token"},
    ("release.yml", "github-release"): {"contents"},
    ("release.yml", "pages"): {"pages", "id-token"},
}
FULL_SHA_PIN = re.compile(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}")
PROJECT_VERSION = tomllib.loads(
    (Path(__file__).parents[1] / "pyproject.toml").read_text()
)["project"]["version"]
# What a green quality and packaging job has to run, whatever its step layout.
CI_GATES = (
    "uv lock --check",
    "ruff check",
    "ruff format --check",
    "tools.package_guard",
    "validate_hxml",  # the wheel smoke outside the checkout
    "HyperviewAceWidget",  # the editor profile smoke
)


def _load(path: Path) -> dict:
    # BaseLoader keeps every scalar a string, so the `on` key is not a boolean.
    return yaml.load(path.read_text(), Loader=yaml.BaseLoader)


def _workflow(name: str) -> dict:
    return _load(WORKFLOW_DIR / name)


def _steps(workflow: dict) -> list[dict]:
    return [step for job in workflow["jobs"].values() for step in job.get("steps", [])]


def _needs(job: dict) -> list[str]:
    needs = job.get("needs", [])
    return [needs] if isinstance(needs, str) else needs


def _upstream(jobs: dict, name: str) -> set[str]:
    """Return every job that must finish before `name` may start."""
    found: set[str] = set()
    pending = _needs(jobs[name])
    while pending:
        current = pending.pop()
        if current not in found:
            found.add(current)
            pending.extend(_needs(jobs[current]))
    return found


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda path: path.name)
def test_workflow_reads_only_by_default_and_never_cancels_a_branch_run(
    path: Path,
) -> None:
    workflow = _load(path)

    assert workflow["permissions"] == {"contents": "read"}
    group = workflow["concurrency"]["group"]
    assert "github.run_id" in group
    # Only a pull request may supersede its own earlier run.
    assert workflow["concurrency"]["cancel-in-progress"] == (
        "${{ github.event_name == 'pull_request' }}"
    )


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda path: path.name)
def test_checkouts_do_not_leave_the_token_in_the_git_config(path: Path) -> None:
    checkouts = [
        step
        for step in _steps(_load(path))
        if step.get("uses", "").startswith("actions/checkout@")
    ]

    assert checkouts
    assert all(step["with"]["persist-credentials"] == "false" for step in checkouts)


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda path: path.name)
def test_every_external_action_is_pinned_to_a_full_commit(path: Path) -> None:
    workflow = _load(path)
    uses = [step["uses"] for step in _steps(workflow) if "uses" in step]
    uses += [job["uses"] for job in workflow["jobs"].values() if "uses" in job]
    external = [value for value in uses if not value.startswith("./")]

    assert external
    assert [value for value in external if not FULL_SHA_PIN.fullmatch(value)] == []


def test_only_the_publishing_jobs_hold_a_write_scope() -> None:
    granted = {}
    for path in WORKFLOWS:
        for name, job in _load(path)["jobs"].items():
            scopes = {
                scope
                for scope, level in job.get("permissions", {}).items()
                if level == "write"
            }
            if scopes:
                granted[(path.name, name)] = scopes

    assert granted == PUBLISHING_SCOPES


def test_a_called_workflow_does_not_share_its_callers_concurrency_group() -> None:
    """A called workflow sees the caller's name in github.workflow, so a group
    built from it would equal the caller's own and deadlock the run."""
    assert "github.workflow" not in _workflow("ci.yml")["concurrency"]["group"]


def test_release_starts_only_from_a_version_tag() -> None:
    assert _workflow("release.yml")["on"] == {"push": {"tags": ["v*.*.*"]}}


def test_nothing_is_published_before_the_tag_matches_the_project_version() -> None:
    jobs = _workflow("release.yml")["jobs"]
    gate = " ".join(step.get("run", "") for step in jobs["metadata"]["steps"])

    assert "tools.check_release_tag" in gate
    assert "GITHUB_REF_NAME" in gate
    for name in set(jobs) - {"metadata"}:
        assert "metadata" in _upstream(jobs, name), name


def test_publication_follows_the_staged_build_in_order() -> None:
    jobs = _workflow("release.yml")["jobs"]
    publish_inputs = [step.get("with", {}) for step in jobs["pypi"]["steps"]]

    assert {"ci", "stage"} <= _upstream(jobs, "pypi")
    assert "pypi" in _upstream(jobs, "github-release")
    assert "github-release" in _upstream(jobs, "pages")
    # Trusted Publishing only: no long-lived token reaches the upload.
    assert not any("password" in inputs for inputs in publish_inputs)


def test_release_calls_ci_with_inputs_and_secrets_it_declares() -> None:
    call = _workflow("release.yml")["jobs"]["ci"]
    declared = _workflow("ci.yml")["on"]["workflow_call"]

    assert (WORKFLOW_DIR.parent.parent / call["uses"]).is_file()
    assert set(call["with"]) <= set(declared["inputs"])
    assert set(call["secrets"]) <= set(declared["secrets"])


def test_release_downloads_only_artifacts_that_an_earlier_job_uploads() -> None:
    def names(action: str, *workflows: str) -> set[str]:
        return {
            step["with"]["name"]
            for workflow in workflows
            for step in _steps(_workflow(workflow))
            if step.get("uses", "").startswith(f"actions/{action}@")
        }

    downloaded = names("download-artifact", "release.yml")
    uploaded = names("upload-artifact", "ci.yml", "release.yml")

    assert downloaded
    assert downloaded <= uploaded


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda path: path.name)
def test_no_step_can_fail_without_failing_the_run(path: Path) -> None:
    jobs = _load(path)["jobs"].values()
    swallowed = [
        unit.get("name") or unit.get("run") or unit.get("uses")
        for job in jobs
        for unit in (job, *job.get("steps", []))
        if unit.get("continue-on-error") == "true"
        or re.search(r"\|\|\s*true\b", unit.get("run", ""))
    ]

    assert swallowed == []


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda path: path.name)
def test_no_workflow_triggers_on_pull_request_target(path: Path) -> None:
    assert "pull_request_target" not in str(_load(path)["on"])


def test_publishing_to_pypi_and_pages_waits_for_a_deployment_environment() -> None:
    jobs = _workflow("release.yml")["jobs"]

    assert all(jobs[name].get("environment") for name in ("pypi", "pages"))


def _marks_prerelease(tag: str) -> bool:
    """Evaluate the release's prerelease expression for a tag, as Actions does."""
    steps = _workflow("release.yml")["jobs"]["github-release"]["steps"]
    expression = next(
        step["with"]["prerelease"]
        for step in steps
        if "prerelease" in step.get("with", {})
    )
    needles = re.findall(r"contains\(github\.ref_name, '(\w+)'\)", expression)
    terms = [f"contains(github.ref_name, '{needle}')" for needle in needles]
    # Anything but a plain chain of contains() calls needs a new evaluator.
    assert expression == "${{ " + " || ".join(terms) + " }}"
    # contains() compares strings without regard to case.
    return any(needle.lower() in tag.lower() for needle in needles)


@pytest.mark.parametrize(
    "tag",
    [
        f"v{PROJECT_VERSION}",
        "v1.0.0a1",
        "v1.0.0b2",
        "v1.0.0rc1",
        "v1.0.0.dev1",
        "v1.0.0",
        "v1.2.3.post1",
    ],
)
def test_release_is_marked_prerelease_exactly_when_the_version_is(tag: str) -> None:
    assert _marks_prerelease(tag) == Version(tag.removeprefix("v")).is_prerelease


def test_ci_still_runs_the_quality_and_packaging_gates() -> None:
    commands = "\n".join(step.get("run", "") for step in _steps(_workflow("ci.yml")))

    assert [gate for gate in CI_GATES if gate not in commands] == []
