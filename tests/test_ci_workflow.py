"""Continuous-integration workflow contract tests."""

import re
import shlex
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
ACTION_PINS = {
    "actions/checkout": "3d3c42e5aac5ba805825da76410c181273ba90b1",
    "actions/setup-python": "5fda3b95a4ea91299a34e894583c3862153e4b97",
    "actions/upload-artifact": "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
    "astral-sh/setup-uv": "20cfd1bf945f4377ade1205e4dbc17946fc9a30d",
}


def test_workflow_audit_accepts_the_active_contract() -> None:
    """The checked-in workflow satisfies every semantic CI guard."""
    assert _audit_workflow(WORKFLOW.read_text()) == []


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            lambda text: text.replace(
                "  quality:\n",
                "  quality:\n    permissions:\n      contents: write\n",
                1,
            ),
            "quality: effective permissions must remain read-only",
        ),
        (
            lambda text: text.replace(
                "uv sync --locked\n", "uv sync --locked --all-groups\n", 1
            ),
            "quality: default sync must not install optional groups",
        ),
        (
            lambda text: text.replace(
                "          uv lock --check\n", "          # uv lock --check\n", 1
            ),
            "quality: active uv lock --check command is required",
        ),
    ],
)
def test_workflow_audit_rejects_fail_open_mutations(
    mutate: Callable[[str], str], expected: str
) -> None:
    """Unsafe permissions, eager extras, and disabled gates are rejected."""
    assert _audit_workflow(mutate(WORKFLOW.read_text())) == [expected]


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            lambda text: text.replace(
                "run: uv sync --locked",
                "run: true && uv sync --locked --all-groups",
                1,
            ),
            "quality: uv sync must be a direct approved command",
        ),
        (
            lambda text: text.replace(
                "run: uv sync --locked",
                "run: command uv sync --locked --all-groups",
                1,
            ),
            "quality: uv sync must be a direct approved command",
        ),
        (
            lambda text: text.replace(
                "        run: uv sync --locked\n",
                "        run: |\n"
                "          sync_all() { uv sync --locked --all-groups; }\n"
                "          sync_all\n",
                1,
            ),
            "quality: uv sync must be a direct approved command",
        ),
        (
            lambda text: text.replace(
                "run: uv sync --locked",
                "run: bash -c 'uv sync --locked --all-groups'",
                1,
            ),
            "quality: uv sync must be a direct approved command",
        ),
        (
            lambda text: (
                text + "\n  extra:\n"
                "    runs-on: ubuntu-latest\n"
                "    steps:\n"
                "      - run: uv sync --locked --all-groups\n"
            ),
            "extra: default sync must not install optional groups",
        ),
        (
            lambda text: text.replace("uv lock --check", "uv lock --check || true", 1),
            "quality: uv lock --check must be a direct blocking command",
        ),
    ],
)
def test_workflow_audit_rejects_ambiguous_command_grammar(
    mutate: Callable[[str], str], expected: str
) -> None:
    """Executable prefixes, wrappers, new jobs, and swallowed failures fail."""
    assert _audit_workflow(mutate(WORKFLOW.read_text())) == [expected]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda text: text.replace(
            "        run: uv sync --locked\n",
            "        run: |\n"
            "          # uv sync --all-groups is intentionally forbidden\n"
            "          uv sync --locked\n",
            1,
        ),
        lambda text: (
            text + "\n  metadata:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - run: uv run python -m tools.package_guard --help\n"
        ),
    ],
)
def test_workflow_audit_accepts_direct_commands_and_innocuous_comments(
    mutate: Callable[[str], str],
) -> None:
    """Direct multiline commands and irrelevant jobs remain supported."""
    assert _audit_workflow(mutate(WORKFLOW.read_text())) == []


def _workflow(text: str | None = None) -> dict[str, object]:
    """Load CI YAML without coercing its on key to a boolean.

    Args:
        text: Optional workflow source instead of the checked-in file.

    Returns:
        The workflow mapping with scalar values kept as strings.
    """
    return yaml.load(text or WORKFLOW.read_text(), Loader=yaml.BaseLoader)


def _run_script(job: dict[str, object]) -> str:
    """Join every shell step in one job.

    Args:
        job: Parsed GitHub Actions job.

    Returns:
        Shell commands in execution order.
    """
    return "\n".join(step["run"] for step in job["steps"] if "run" in step)


def _active_commands(job: dict[str, object]) -> list[list[str]]:
    """Parse active, top-level shell commands from a workflow job."""
    commands = []
    for step in job["steps"]:
        for line in step.get("run", "").splitlines():
            lexer = shlex.shlex(line, posix=True, punctuation_chars=";&|(){}")
            lexer.whitespace_split = True
            lexer.commenters = "#"
            tokens = list(lexer)
            if tokens:
                commands.append(tokens)
    return commands


def _installs_optional_groups(command: list[str]) -> bool:
    """Return whether a normal sync command installs optional groups."""
    if command[:2] != ["uv", "sync"]:
        return False
    return "--all-groups" in command or any(
        argument == "--group=redis"
        or (
            argument == "--group"
            and index + 1 < len(command)
            and command[index + 1] == "redis"
        )
        for index, argument in enumerate(command)
    )


def _contains_uv(command: list[str], subcommand: str) -> bool:
    """Return whether tokens contain a uv subcommand at any position."""
    source = " ".join(command)
    return (
        re.search(rf"(?:^|\s)uv\s+{re.escape(subcommand)}(?:\s|$)", source) is not None
    )


def _approved_sync(job: dict[str, object]) -> list[str]:
    """Return the sole direct sync command permitted for a job."""
    services = job.get("services", {})
    if (
        job.get("if") == "${{ inputs.redis == true }}"
        and isinstance(services, dict)
        and "redis" in services
    ):
        return ["uv", "sync", "--locked", "--group", "redis"]
    return ["uv", "sync", "--locked"]


def _audit_workflow(text: str) -> list[str]:
    """Return deterministic safety violations in CI workflow source."""
    workflow = _workflow(text)
    global_permissions = workflow["permissions"]
    jobs = workflow["jobs"]
    violations = []

    for name, job in jobs.items():
        permissions = job.get("permissions", global_permissions)
        if permissions.get("contents") != "read" or "write" in permissions.values():
            violations.append(f"{name}: effective permissions must remain read-only")

    valid_lock = False
    invalid_lock = False
    for name, job in jobs.items():
        commands = _active_commands(job)
        for command in commands:
            if _contains_uv(command, "sync") and command != _approved_sync(job):
                if command[:2] == ["uv", "sync"] and _installs_optional_groups(command):
                    message = f"{name}: default sync must not install optional groups"
                else:
                    message = f"{name}: uv sync must be a direct approved command"
                if message not in violations:
                    violations.append(message)

            if _contains_uv(command, "lock"):
                if name == "quality" and command == ["uv", "lock", "--check"]:
                    valid_lock = True
                else:
                    invalid_lock = True
                    message = (
                        f"{name}: uv lock --check must be a direct blocking command"
                    )
                    if message not in violations:
                        violations.append(message)

    if not valid_lock and not invalid_lock:
        violations.append("quality: active uv lock --check command is required")
    return violations


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
