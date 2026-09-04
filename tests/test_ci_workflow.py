"""Continuous-integration workflow contract tests."""

import re
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
            "quality: run contract is not approved",
        ),
        (
            lambda text: text.replace(
                "          uv lock --check\n", "          # uv lock --check\n", 1
            ),
            "quality: run contract is not approved",
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
            "quality: run contract is not approved",
        ),
        (
            lambda text: text.replace(
                "run: uv sync --locked",
                "run: command uv sync --locked --all-groups",
                1,
            ),
            "quality: run contract is not approved",
        ),
        (
            lambda text: text.replace(
                "        run: uv sync --locked\n",
                "        run: |\n"
                "          sync_all() { uv sync --locked --all-groups; }\n"
                "          sync_all\n",
                1,
            ),
            "quality: run contract is not approved",
        ),
        (
            lambda text: text.replace(
                "run: uv sync --locked",
                "run: bash -c 'uv sync --locked --all-groups'",
                1,
            ),
            "quality: run contract is not approved",
        ),
        (
            lambda text: (
                text + "\n  extra:\n"
                "    runs-on: ubuntu-latest\n"
                "    steps:\n"
                "      - run: uv sync --locked --all-groups\n"
            ),
            "extra: execution shape is not approved",
        ),
        (
            lambda text: text.replace("uv lock --check", "uv lock --check || true", 1),
            "quality: run contract is not approved",
        ),
        (
            lambda text: (
                text + "\n  metadata:\n"
                "    runs-on: ubuntu-latest\n"
                "    steps:\n"
                "      - run: uv run python -m tools.package_guard --help\n"
            ),
            "metadata: execution shape is not approved",
        ),
        (
            lambda text: (
                text + "\n  delegated:\n"
                "    uses: example/repository/.github/workflows/ci.yml@main\n"
            ),
            "delegated: execution shape is not approved",
        ),
    ],
)
def test_workflow_audit_rejects_ambiguous_command_grammar(
    mutate: Callable[[str], str], expected: str
) -> None:
    """Executable prefixes, wrappers, new jobs, and swallowed failures fail."""
    assert _audit_workflow(mutate(WORKFLOW.read_text())) == [expected]


@pytest.mark.parametrize(
    "replacement",
    [
        "run: |\n          UV=uv\n          $UV sync --locked --all-groups",
        (
            "run: |\n"
            "          COMMAND='uv sync --locked --all-groups'\n"
            "          $COMMAND"
        ),
        (
            "run: |\n"
            "          shopt -s expand_aliases\n"
            "          alias sync_all='uv sync --locked --all-groups'\n"
            "          sync_all"
        ),
    ],
)
def test_workflow_audit_rejects_shell_indirection(replacement: str) -> None:
    """Variables and aliases cannot hide an unapproved dependency sync."""
    mutated = WORKFLOW.read_text().replace("run: uv sync --locked", replacement, 1)

    assert _audit_workflow(mutated) == ["quality: run contract is not approved"]


@pytest.mark.parametrize(
    "mutation",
    [
        "        continue-on-error: true\n",
        "        if: false\n",
        "        shell: bash {0}\n",
        "        working-directory: scripts\n",
        "        env:\n          CI: false\n",
        "        uses: example/action@main\n",
    ],
)
def test_workflow_audit_rejects_step_execution_controls(mutation: str) -> None:
    """A gate cannot change failure, condition, or shell semantics."""
    mutated = WORKFLOW.read_text().replace(
        "      - name: Check dependency and style drift\n",
        "      - name: Check dependency and style drift\n" + mutation,
        1,
    )

    assert _audit_workflow(mutated) == ["quality: execution shape is not approved"]


@pytest.mark.parametrize(
    "mutation", ["    if: false\n", "    continue-on-error: true\n"]
)
def test_workflow_audit_rejects_job_execution_controls(mutation: str) -> None:
    """The quality job must remain blocking and unconditional."""
    mutated = WORKFLOW.read_text().replace("  quality:\n", "  quality:\n" + mutation, 1)

    assert _audit_workflow(mutated) == ["quality: execution shape is not approved"]


def test_workflow_audit_rejects_changed_redis_condition() -> None:
    """The Redis job keeps its exact reviewed opt-in condition."""
    mutated = WORKFLOW.read_text().replace(
        "if: ${{ inputs.redis == true }}", "if: ${{ always() }}", 1
    )

    assert _audit_workflow(mutated) == ["redis: execution shape is not approved"]


@pytest.mark.parametrize("candidate", ["", "[]", "{}"])
def test_workflow_audit_rejects_non_mapping_candidates(candidate: str) -> None:
    """Supplied non-workflows never fall back or leak parser-shaped errors."""
    assert _audit_workflow(candidate) == ["workflow: mapping contract is not approved"]


def test_workflow_audit_accepts_innocuous_full_line_comments() -> None:
    """Full-line comments do not change an approved command contract."""
    mutated = WORKFLOW.read_text().replace(
        "        run: uv sync --locked\n",
        "        run: |\n"
        "          # uv sync --all-groups is intentionally forbidden\n"
        "          uv sync --locked\n",
        1,
    )

    assert _audit_workflow(mutated) == []


def _workflow(text: str | None = None) -> object:
    """Load CI YAML without coercing its on key to a boolean.

    Args:
        text: Optional workflow source instead of the checked-in file.

    Returns:
        The workflow mapping with scalar values kept as strings.
    """
    source = WORKFLOW.read_text() if text is None else text
    return yaml.load(source, Loader=yaml.BaseLoader)


def _run_script(job: dict[str, object]) -> str:
    """Join every shell step in one job.

    Args:
        job: Parsed GitHub Actions job.

    Returns:
        Shell commands in execution order.
    """
    return "\n".join(step["run"] for step in job["steps"] if "run" in step)


def _normalize_run(script: object) -> str | None:
    """Normalize blanks and harmless full-line comments in a run script."""
    if not isinstance(script, str):
        return None
    return "\n".join(
        stripped
        for line in script.splitlines()
        if (stripped := line.strip()) and not stripped.startswith("#")
    )


APPROVED_RUN_CONTRACT = {
    "quality": (
        "uv sync --locked",
        "uv lock --check\nuv run ruff check .\nuv run ruff format --check .",
        (
            "PYTHONPATH=.:src uv run python -m django check --settings=tests.settings\n"
            "PYTHONPATH=.:src uv run python -m django check "
            "--settings=tests.settings_database\n"
            "PYTHONPATH=.:src uv run python -m django check "
            "--settings=tests.settings_database_admin\n"
            "PYTHONPATH=.:src uv run python -m django makemigrations "
            "dj_hyperview_database --check --dry-run "
            "--settings=tests.settings_database"
        ),
        (
            "uv run pytest -q tests/test_dependency_policy.py "
            "tests/test_package_boundary.py\n"
            "uv run pytest -q tests/test_public_api_quality.py"
        ),
    ),
    "compatibility": (
        (
            "uv run --locked --python ${{ matrix.python }} "
            "--with Django==${{ matrix.django }} python -m tools.test_matrix "
            "--django-version ${{ matrix.django }}"
        ),
    ),
    "redis": (
        "uv sync --locked --group redis",
        (
            "uv run --locked --group redis python -m tools.test_matrix --redis "
            "--django-version 6.1.1"
        ),
    ),
    "artifacts": (
        "uv sync --locked",
        "uv build\nuv run python -m tools.package_guard dist/*.whl",
        (
            'smoke_dir="$(mktemp -d)"\n'
            'uv venv "$smoke_dir/venv" --python 3.12\n'
            'uv pip install --python "$smoke_dir/venv/bin/python" dist/*.whl\n'
            "(\n"
            'cd "$smoke_dir"\n'
            "unset PYTHONPATH\n"
            '"$smoke_dir/venv/bin/python" -I -c \'from pathlib import Path; '
            "import dj_hyperview; assert Path(dj_hyperview.__file__).resolve()."
            'is_relative_to(Path.cwd() / "venv")\'\n'
            ")"
        ),
        "uv run zensical build --clean --strict -f zensical.yml",
    ),
}

APPROVED_JOB_KEYS = {
    "quality": {"runs-on", "timeout-minutes", "steps"},
    "compatibility": {"runs-on", "timeout-minutes", "strategy", "steps"},
    "redis": {"if", "needs", "runs-on", "timeout-minutes", "env", "services", "steps"},
    "artifacts": {"needs", "runs-on", "timeout-minutes", "steps"},
}

APPROVED_STEP_KEYS = {
    "quality": (
        {"uses"},
        {"uses", "with"},
        {"uses", "with"},
        {"name", "run"},
        {"name", "run"},
        {"name", "run"},
        {"name", "run"},
    ),
    "compatibility": (
        {"uses"},
        {"uses", "with"},
        {"uses", "with"},
        {"name", "run"},
    ),
    "redis": (
        {"uses"},
        {"uses", "with"},
        {"uses", "with"},
        {"run"},
        {"name", "run"},
    ),
    "artifacts": (
        {"uses"},
        {"uses", "with"},
        {"uses", "with"},
        {"run"},
        {"name", "run"},
        {"name", "run"},
        {"name", "run"},
        {"name", "uses", "with"},
    ),
}


def _run_contract(job: dict[str, object]) -> tuple[str, ...] | None:
    """Return normalized run steps or reject malformed workflow structure."""
    steps = job.get("steps")
    if not isinstance(steps, list):
        return None
    scripts = []
    for step in steps:
        if not isinstance(step, dict):
            return None
        if "run" not in step:
            continue
        script = _normalize_run(step["run"])
        if script is None:
            return None
        scripts.append(script)
    return tuple(scripts)


def _execution_shape_is_approved(name: str, job: dict[str, object]) -> bool:
    """Return whether job and step keys match the reviewed execution shape."""
    if name not in APPROVED_JOB_KEYS:
        return False
    if set(job) - {"permissions"} != APPROVED_JOB_KEYS[name]:
        return False
    if name == "redis" and job.get("if") != "${{ inputs.redis == true }}":
        return False
    steps = job.get("steps")
    if not isinstance(steps, list) or any(not isinstance(step, dict) for step in steps):
        return False
    return tuple(set(step) for step in steps) == APPROVED_STEP_KEYS[name]


@pytest.mark.parametrize(
    "job",
    [
        {"steps": "not-a-list"},
        {"steps": ["not-a-step"]},
        {"steps": [{"run": ["not", "a", "script"]}]},
    ],
)
def test_run_contract_rejects_malformed_step_shapes(job: dict[str, object]) -> None:
    """Malformed workflow step structures fail the closed run contract."""
    assert _run_contract(job) is None


def _audit_workflow(text: str) -> list[str]:
    """Return deterministic safety violations in CI workflow source."""
    workflow = _workflow(text)
    if not isinstance(workflow, dict):
        return ["workflow: mapping contract is not approved"]
    global_permissions = workflow.get("permissions")
    jobs = workflow.get("jobs")
    if not isinstance(global_permissions, dict) or not isinstance(jobs, dict):
        return ["workflow: mapping contract is not approved"]
    violations = []

    for name, job in jobs.items():
        if not isinstance(name, str) or not isinstance(job, dict):
            violations.append(f"{name}: execution shape is not approved")
            continue
        permissions = job.get("permissions", global_permissions)
        if not isinstance(permissions, dict) or (
            permissions.get("contents") != "read" or "write" in permissions.values()
        ):
            violations.append(f"{name}: effective permissions must remain read-only")

    for name, job in jobs.items():
        shape_approved = _execution_shape_is_approved(name, job)
        if not shape_approved:
            violations.append(f"{name}: execution shape is not approved")
        elif (
            name not in APPROVED_RUN_CONTRACT
            or _run_contract(job) != APPROVED_RUN_CONTRACT[name]
        ):
            violations.append(f"{name}: run contract is not approved")
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
