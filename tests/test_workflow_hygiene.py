"""Every workflow limits its token and its concurrency, whatever else it does."""

from pathlib import Path

import pytest
import yaml

WORKFLOWS = sorted((Path(__file__).parents[1] / ".github" / "workflows").glob("*.yml"))


def _load(path: Path) -> dict:
    return yaml.load(path.read_text(), Loader=yaml.BaseLoader)


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
        for job in _load(path)["jobs"].values()
        for step in job.get("steps", [])
        if step.get("uses", "").startswith("actions/checkout@")
    ]

    assert checkouts
    assert all(step["with"]["persist-credentials"] == "false" for step in checkouts)


def test_a_called_workflow_does_not_share_its_callers_concurrency_group() -> None:
    """A called workflow sees the caller's name in github.workflow, so a group
    built from it would equal the caller's own and deadlock the run."""
    called = _load(WORKFLOWS[0].parent / "ci.yml")

    assert "github.workflow" not in called["concurrency"]["group"]
