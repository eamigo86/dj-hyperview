"""Browser acceptance remains opt-in, locked, and development-only."""

import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]


def test_browser_runner_is_a_pinned_development_group():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert metadata["dependency-groups"]["browser"] == ["playwright==1.60.0"]
    assert all(
        "playwright" not in requirement
        for requirement in metadata["project"]["dependencies"]
    )
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    assert (
        next(package for package in lock["package"] if package["name"] == "playwright")[
            "version"
        ]
        == "1.60.0"
    )


def test_ci_installs_matching_chromium_and_runs_isolated_admin_acceptance():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    browser = workflow["jobs"]["browser"]
    commands = "\n".join(step.get("run", "") for step in browser["steps"])
    assert "uv sync --locked --group browser --no-build" in commands
    assert "python -m playwright install --with-deps chromium" in commands
    assert "DJHV_TEST_BROWSER=1" in commands
    assert "--ds=tests.settings_browser" in commands
    assert "tests/browser" in commands
    assert "uv build" not in commands
