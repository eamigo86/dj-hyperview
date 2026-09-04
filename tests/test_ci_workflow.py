"""Continuous-integration workflow contract tests."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
ACTION_PINS = {
    "actions/checkout": "3d3c42e5aac5ba805825da76410c181273ba90b1",
    "actions/setup-python": "5fda3b95a4ea91299a34e894583c3862153e4b97",
    "actions/upload-artifact": "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
    "astral-sh/setup-uv": "20cfd1bf945f4377ade1205e4dbc17946fc9a30d",
}


def _workflow() -> dict[str, object]:
    """Load CI YAML without coercing its on key to a boolean.

    Returns:
        The workflow mapping with scalar values kept as strings.
    """
    return yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)


def _run_script(job: dict[str, object]) -> str:
    """Join every shell step in one job.

    Args:
        job: Parsed GitHub Actions job.

    Returns:
        Shell commands in execution order.
    """
    return "\n".join(step["run"] for step in job["steps"] if "run" in step)


def test_ci_is_reusable_and_defaults_to_read_only_permissions() -> None:
    """CI runs on changes or calls without write-capable credentials."""
    workflow = _workflow()

    assert set(workflow["on"]) == {
        "pull_request",
        "push",
        "workflow_call",
        "workflow_dispatch",
    }
    assert workflow["permissions"] == {"contents": "read"}
    assert {"quality", "compatibility"} <= set(workflow["jobs"])
    text = WORKFLOW.read_text()
    assert "id-token: write" not in text
    assert "pages: write" not in text
    assert "deploy-pages" not in text
    assert "pypi" not in text.lower()


def test_ci_actions_are_immutable_and_use_the_verified_versions() -> None:
    """Every external action is fixed to a full reviewed commit SHA."""
    workflow = _workflow()
    uses = [
        step["uses"]
        for job in workflow["jobs"].values()
        for step in job["steps"]
        if "uses" in step
    ]

    assert uses
    assert all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", value) for value in uses)
    for action, sha in ACTION_PINS.items():
        assert f"{action}@{sha}" in uses


def test_compatibility_matrix_runs_every_supported_runtime_with_coverage() -> None:
    """The matrix delegates all six cells to the canonical coverage gate."""
    job = _workflow()["jobs"]["compatibility"]
    matrix = job["strategy"]["matrix"]
    script = _run_script(job)

    assert job["strategy"]["fail-fast"] == "false"
    assert matrix == {
        "python": ["3.12", "3.13", "3.14"],
        "django": ["5.2.17", "6.1.1"],
    }
    assert "--python ${{ matrix.python }}" in script
    assert "Django==${{ matrix.django }}" in script
    assert "python -m tools.test_matrix" in script
    assert "--django-version ${{ matrix.django }}" in script


def test_quality_job_checks_lock_style_settings_migrations_and_boundaries() -> None:
    """The non-matrix job fails closed on repository and Django drift."""
    workflow = _workflow()
    scripts = {
        name: _run_script(workflow["jobs"][name])
        for name in ("quality", "compatibility")
    }
    quality = scripts["quality"]

    for command in (
        "uv lock --check",
        "uv run ruff check .",
        "uv run ruff format --check .",
        "--settings=tests.settings",
        "--settings=tests.settings_database",
        "--settings=tests.settings_database_admin",
        "makemigrations dj_hyperview_database --check --dry-run",
        "tests/test_package_boundary.py",
        "tests/test_public_api_quality.py",
    ):
        assert command in quality
    assert all("uv build" not in script for script in scripts.values())
    assert all("zensical build" not in script for script in scripts.values())


def test_artifact_job_builds_checks_and_smokes_one_immutable_candidate() -> None:
    """CI alone builds guarded distributions and docs, then tests the wheel."""
    workflow = _workflow()
    job = workflow["jobs"]["artifacts"]
    script = _run_script(job)
    upload = next(
        step
        for step in job["steps"]
        if "actions/upload-artifact" in step.get("uses", "")
    )

    assert job["needs"] == ["quality", "compatibility"]
    assert "uv build" in script
    assert "uv run python -m tools.package_guard dist/*.whl" in script
    assert "uv run zensical build --clean --strict -f zensical.yml" in script
    assert 'smoke_dir="$(mktemp -d)"' in script
    assert 'cd "$smoke_dir"' in script
    assert "unset PYTHONPATH" in script
    assert "uv pip install" in script and "dist/*.whl" in script
    assert "dj_hyperview.__file__" in script
    assert upload["with"] == {
        "name": "release-candidate-${{ github.sha }}",
        "path": "dist/*\nsite/\n",
        "if-no-files-found": "error",
        "retention-days": "7",
    }


def test_redis_job_is_versioned_real_and_strictly_opt_in() -> None:
    """Redis runs only for an explicit reusable or manual workflow input."""
    workflow = _workflow()
    redis_input = {
        "description": "Run live Redis acceptance",
        "type": "boolean",
        "default": "false",
    }
    job = workflow["jobs"]["redis"]
    script = _run_script(job)

    assert workflow["on"]["workflow_call"]["inputs"]["redis"] == redis_input
    assert workflow["on"]["workflow_dispatch"]["inputs"]["redis"] == redis_input
    assert job["if"] == "${{ inputs.redis == true }}"
    assert job["services"]["redis"]["image"] == "redis:8.2.9-alpine"
    assert job["env"]["DJHV_REDIS_URL"] == "redis://127.0.0.1:6379/15"
    assert "--group redis" in script
    assert "python -m tools.test_matrix --redis" in script
    assert all(
        "--group redis" not in _run_script(workflow["jobs"][name])
        for name in ("quality", "compatibility", "artifacts")
    )
