"""Current-run coverage artifacts reject stale or substituted reports."""

import importlib
import json
import time

import pytest


@pytest.fixture
def artifacts(monkeypatch):
    """Model the canonical producer independently of the pytest matrix cell."""
    module = importlib.import_module("tools.coverage_artifacts")
    version = module.metadata.version
    monkeypatch.setattr(module.platform, "python_version", lambda: "3.12.11")
    monkeypatch.setattr(
        module.metadata,
        "version",
        lambda name: "6.1.1" if name == "Django" else version(name),
    )
    return module


@pytest.fixture
def inputs(tmp_path):
    """Provide a bounded synthetic report and independent job identity."""
    context = {
        "repository": "eamigo86/dj-hyperview",
        "sha": "a" * 40,
        "run_id": "123",
        "run_attempt": "2",
    }
    report = tmp_path / "coverage.xml"
    report.write_text(
        f'<coverage timestamp="{int(time.time() * 1000)}" '
        'lines-covered="95" lines-valid="100" '
        'branches-covered="95" branches-valid="100">'
        f"<sources><source>{tmp_path}</source></sources>"
        '<packages><package><classes><class filename="src/dj_hyperview/conf.py"/>'
        "</classes></package></packages></coverage>"
    )
    return context, report, int(time.time()) - 1


def seal(artifacts, inputs, *, profile="default"):
    """Seal through the production helper with actual report bytes."""
    context, report, started_at = inputs
    directory = report.parent / "reports" / profile
    artifacts.seal(
        report,
        directory,
        context=context,
        profile=profile,
        started_at=started_at,
        checkout=report.parent,
    )
    return directory


@pytest.mark.parametrize("profiles", [("default",), ("default", "redis")])
def test_verifies_selected_current_reports_without_rewriting(
    artifacts, inputs, profiles
):
    """Both selected real XML files survive byte-for-byte with exact counters."""
    context, report, _ = inputs
    for profile in profiles:
        directory = seal(artifacts, inputs, profile=profile)
        assert (directory / "coverage.xml").read_bytes() == report.read_bytes()
        manifest = json.loads((directory / "manifest.json").read_text())
        assert manifest["counters"] == {
            "lines-covered": 95,
            "lines-valid": 100,
            "branches-covered": 95,
            "branches-valid": 100,
        }
    result = artifacts.verify(
        report.parent / "reports",
        context=context,
        profiles=profiles,
        checkout=report.parent,
    )
    assert len(result["files"].split(",")) == len(profiles)
    assert context["sha"] in result["name"]
    assert "123-2" in result["name"]


@pytest.mark.parametrize(
    "field",
    [
        "repository",
        "sha",
        "run_id",
        "run_attempt",
        "profile",
        "sha256",
        "counters",
        "python",
        "django",
        "extra",
    ],
)
def test_rejects_manifest_substitution(artifacts, inputs, field):
    """Upload identity and counters cannot be replaced by another report."""
    context, report, _ = inputs
    directory = seal(artifacts, inputs)
    path = directory / "manifest.json"
    value = json.loads(path.read_text())
    value[field] = "substituted"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        artifacts.verify(
            directory.parent,
            context=context,
            profiles=("default",),
            checkout=report.parent,
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "stale",
        "future",
        "low-lines",
        "low-branches",
        "zero",
        "escape",
        "foreign-source",
        "malformed",
    ],
)
def test_rejects_stale_unsafe_or_insufficient_xml(artifacts, inputs, mutation):
    """Do not seal old XML, foreign source paths, or failed coverage gates."""
    _, report, started_at = inputs
    text = report.read_text()
    replacements = {
        "stale": (
            text.split('timestamp="')[1].split('"')[0],
            str((started_at - 1) * 1000),
        ),
        "future": (
            text.split('timestamp="')[1].split('"')[0],
            str(int(time.time() * 1000) + 60000),
        ),
        "low-lines": ('lines-covered="95"', 'lines-covered="94"'),
        "low-branches": ('branches-covered="95"', 'branches-covered="94"'),
        "zero": ('lines-valid="100"', 'lines-valid="0"'),
        "escape": ("src/dj_hyperview/conf.py", "src/dj_hyperview/../../outside.py"),
        "foreign-source": (str(report.parent), "/foreign/checkout"),
        "malformed": ("<coverage ", "<invalid "),
    }
    report.write_text(text.replace(*replacements[mutation]))
    with pytest.raises(ValueError):
        seal(artifacts, inputs)
    assert not (report.parent / "reports/default/manifest.json").exists()


@pytest.mark.parametrize(
    "mutation",
    [
        "xml-bytes",
        "missing-redis",
        "extra-profile",
        "extra-file",
        "xml-symlink",
        "manifest-symlink",
        "directory-symlink",
        "duplicate-key",
    ],
)
def test_rejects_incomplete_or_unsafe_downloads(artifacts, inputs, mutation):
    """A directory of partial, aliased or unexpected downloads fails closed."""
    context, report, _ = inputs
    directory = seal(artifacts, inputs)
    profiles = ("default",)
    if mutation == "xml-bytes":
        (directory / "coverage.xml").write_text(report.read_text() + "\n")
    elif mutation == "missing-redis":
        profiles = ("default", "redis")
    elif mutation == "extra-profile":
        (directory.parent / "foreign").mkdir()
    elif mutation == "extra-file":
        (directory / "extra.xml").write_text("unexpected")
    elif mutation == "directory-symlink":
        moved = directory.with_name("saved")
        directory.rename(moved)
        directory.symlink_to(moved, target_is_directory=True)
    elif mutation == "duplicate-key":
        path = directory / "manifest.json"
        path.write_text(path.read_text().replace("{", '{"schema":1,', 1))
    else:
        name = "coverage.xml" if mutation == "xml-symlink" else "manifest.json"
        path = directory / name
        moved = report.parent / name.replace("coverage", "saved")
        moved.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(moved)
    with pytest.raises(ValueError):
        artifacts.verify(
            directory.parent, context=context, profiles=profiles, checkout=report.parent
        )


def test_seal_never_replaces_an_existing_artifact(artifacts, inputs):
    """A repeated invocation cannot relabel a previously sealed directory."""
    directory = seal(artifacts, inputs)
    before = (directory / "manifest.json").read_bytes()
    with pytest.raises(ValueError):
        seal(artifacts, inputs)
    assert (directory / "manifest.json").read_bytes() == before


@pytest.mark.parametrize(
    ("python", "django"), [("3.13.9", "6.1.1"), ("3.12.11", "5.2.17")]
)
def test_seal_rejects_noncanonical_producer_runtime(
    artifacts, inputs, monkeypatch, python, django
):
    """Only the actual canonical producer may seal reports for collection."""
    monkeypatch.setattr(artifacts.platform, "python_version", lambda: python)
    monkeypatch.setattr(artifacts.metadata, "version", lambda name: django)
    with pytest.raises(ValueError, match="canonical coverage runtime"):
        seal(artifacts, inputs)


def test_cli_rejects_wrong_checkout_sha_before_sealing(artifacts, inputs, monkeypatch):
    """Job labels must match actual git HEAD, not merely environment text."""
    context, report, started_at = inputs
    for key, value in context.items():
        monkeypatch.setenv("GITHUB_" + key.upper(), value)
    monkeypatch.setenv("COVERAGE_STARTED_AT", str(started_at))
    monkeypatch.setattr(artifacts, "checkout_sha", lambda: "b" * 40)
    monkeypatch.chdir(report.parent)
    assert artifacts.main(["seal", "--profile", "default"]) == 2
    assert not (report.parent / "coverage-reports").exists()


@pytest.mark.parametrize("field", ["counters", "timestamp"])
def test_manifest_numbers_must_remain_exact_integers(artifacts, inputs, field):
    """JSON floats are not the closed integer manifest emitted by the producer."""
    context, report, _ = inputs
    directory = seal(artifacts, inputs)
    path = directory / "manifest.json"
    value = json.loads(path.read_text())
    if field == "counters":
        value[field]["lines-covered"] = 95.0
    else:
        value[field] = float(value[field])
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        artifacts.verify(
            directory.parent,
            context=context,
            profiles=("default",),
            checkout=report.parent,
        )


@pytest.mark.parametrize("selected", ["false", "true"])
def test_cli_seals_and_verifies_the_selected_current_job(
    artifacts, inputs, monkeypatch, selected
):
    """Real CLI dispatch writes only validated file/name outputs, never XML edits."""
    context, report, started_at = inputs
    for key, value in context.items():
        monkeypatch.setenv("GITHUB_" + key.upper(), value)
    monkeypatch.setenv("COVERAGE_STARTED_AT", str(started_at))
    monkeypatch.setenv("COVERAGE_REDIS", selected)
    output = report.parent / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setattr(artifacts, "checkout_sha", lambda: context["sha"])
    monkeypatch.chdir(report.parent)
    assert artifacts.main(["seal", "--profile", "default"]) == 0
    if selected == "true":
        assert artifacts.main(["seal", "--profile", "redis"]) == 0
    assert artifacts.main(["verify"]) == 0
    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert set(values) == {"files", "name"}
    assert len(values["files"].split(",")) == (2 if selected == "true" else 1)
    assert context["sha"] in values["name"]
