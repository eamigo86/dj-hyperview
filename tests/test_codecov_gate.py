"""Codecov acceptance binds current reports, remote state and trusted receipts."""

import copy
import importlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[1]
SHA = "a" * 40
REPO = "eamigo86/dj-hyperview"
NOW = 1800000000


def iso(seconds):
    return datetime.fromtimestamp(seconds, UTC).isoformat()


@pytest.fixture
def gate():
    return importlib.import_module("tools.check_codecov")


@pytest.fixture
def evidence():
    context = {"repository": REPO, "sha": SHA, "run_id": "100", "run_attempt": "1"}
    statuses = [
        {
            "id": number,
            "context": "codecov/" + label,
            "state": "success",
            "creator": {"id": 22429695, "login": "codecov[bot]", "type": "Bot"},
            "created_at": iso(NOW),
            "target_url": f"https://app.codecov.io/gh/{REPO}/commit/{SHA}",
            "url": f"https://api.github.com/repos/{REPO}/statuses/{SHA}",
        }
        for number, label in [(10, "project"), (11, "patch")]
    ]
    uploads = [
        {
            "storage_path": f"store/{SHA}/unique.txt",
            "name": "expected-upload",
            "provider": "github-actions",
            "build_url": f"https://github.com/{REPO}/actions/runs/100/attempts/1",
            "state": "merged",
            "state_name": "MERGED",
            "created_at": iso(NOW - 10),
            "updated_at": iso(NOW - 5),
            "totals": {"lines": 100, "hits": 99, "misses": 1, "partials": 0},
        }
    ]
    measured = {
        "repository": REPO,
        "sha": SHA,
        "policy": "b" * 64,
        "profiles": {
            name: {"python": "3.12.11", "django": "6.1.1", "xml": "c" * 64}
            for name in ("default", "redis")
        },
    }
    run = {
        "id": 100,
        "run_attempt": 1,
        "event": "push",
        "head_branch": "main",
        "head_sha": SHA,
        "status": "completed",
        "conclusion": "success",
        "path": ".github/workflows/ci.yml",
        "head_repository": {"full_name": REPO},
        "repository": {"full_name": REPO},
        "created_at": iso(NOW - 60),
    }
    artifact = {
        "id": 200,
        "name": f"codecov-acceptance-{SHA}-100-1",
        "expired": False,
        "digest": "sha256:" + "d" * 64,
        "expires_at": iso(NOW + 86400),
        "created_at": iso(NOW + 1),
        "size_in_bytes": 1000,
        "workflow_run": {"id": 100, "head_sha": SHA, "head_branch": "main"},
    }
    return context, statuses, uploads, measured, run, artifact


def receipt(gate, evidence):
    context, statuses, uploads, measured, _, _ = evidence
    return gate.make_receipt(
        measured,
        context,
        uploads,
        statuses,
        baseline={},
        notified_at=NOW - 1,
        now=NOW + 1,
    )


def test_first_assessment_requires_current_bot_success(gate, evidence):
    value = receipt(gate, evidence)
    assert value["source"]["run_id"] == "100"
    assert value["statuses"]["codecov/project"]["id"] == 10
    assert value["measurement"]["profiles"]["redis"]["xml"] == "c" * 64


@pytest.mark.parametrize(
    "mutation",
    [
        "old-id",
        "old-time",
        "missing",
        "pending",
        "failure",
        "spoof",
        "wrong-sha",
        "foreign-url",
    ],
)
def test_first_assessment_rejects_unproven_statuses(gate, evidence, mutation):
    context, statuses, uploads, measured, _, _ = evidence
    baseline = {}
    if mutation == "old-id":
        baseline = {"codecov/project": 10}
    elif mutation == "old-time":
        statuses[0]["created_at"] = iso(NOW - 100)
    elif mutation == "missing":
        statuses.pop()
    elif mutation in ("pending", "failure"):
        statuses[0]["state"] = mutation
    elif mutation == "spoof":
        statuses[0]["creator"]["id"] = 123
    elif mutation == "wrong-sha":
        statuses[0]["url"] = statuses[0]["url"].replace(SHA, "f" * 40)
    else:
        statuses[0]["target_url"] = "https://foreign.example/success"
    with pytest.raises((ValueError, gate.Pending)):
        gate.make_receipt(
            measured,
            context,
            uploads,
            statuses,
            baseline=baseline,
            notified_at=NOW - 1,
            now=NOW + 1,
        )


def test_pr_and_empty_diff_are_real_success_not_inferred_from_percentages(
    gate, evidence
):
    context, statuses, uploads, measured, _, _ = evidence
    for status in statuses:
        status["target_url"] = f"https://app.codecov.io/gh/{REPO}/pull/7"
        status["description"] = "Coverage not affected"
    assert gate.make_receipt(
        measured,
        context,
        uploads,
        statuses,
        baseline={},
        notified_at=NOW - 1,
        now=NOW + 1,
        pull=7,
        pr={"head_sha": "d" * 40, "base_sha": "e" * 40},
    )
    with pytest.raises(ValueError):
        gate.make_receipt(
            measured,
            context,
            uploads,
            statuses,
            baseline={},
            notified_at=NOW - 1,
            now=NOW + 1,
            pull=8,
            pr={"head_sha": "d" * 40, "base_sha": "e" * 40},
        )


def test_identical_receipt_reuses_unchanged_status_ids(gate, evidence):
    _, statuses, uploads, measured, run, artifact = evidence
    value = receipt(gate, evidence)
    gate.validate_receipt(
        value,
        measurement=measured,
        run=run,
        artifact=artifact,
        uploads=uploads,
        statuses=statuses,
        now=NOW + 20,
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "xml",
        "versions",
        "policy",
        "sha",
        "base-only",
        "expired",
        "digest",
        "fork",
        "pr",
        "failed-run",
        "attempt",
        "new-upload",
        "pending-upload",
        "current-failure",
        "new-status",
        "extra-field",
    ],
)
def test_receipt_cannot_reuse_stale_or_untrusted_success(gate, evidence, mutation):
    _, statuses, uploads, measured, run, artifact = evidence
    value = receipt(gate, evidence)
    if mutation == "xml":
        measured["profiles"]["redis"]["xml"] = "d" * 64
    elif mutation == "versions":
        measured["profiles"]["redis"]["python"] = "3.12.12"
    elif mutation == "policy":
        measured["policy"] = "d" * 64
    elif mutation == "sha":
        run["head_sha"] = "f" * 40
    elif mutation == "base-only":
        measured["profiles"].pop("redis")
    elif mutation == "expired":
        artifact["expires_at"] = iso(NOW - 1)
    elif mutation == "digest":
        artifact["digest"] = "missing"
    elif mutation == "fork":
        run["head_repository"]["full_name"] = "fork/repo"
    elif mutation == "pr":
        run["event"] = "pull_request"
    elif mutation == "failed-run":
        run["conclusion"] = "failure"
    elif mutation == "attempt":
        run["run_attempt"] = 2
    elif mutation == "new-upload":
        other = copy.deepcopy(uploads[0])
        other["storage_path"] += "another"
        uploads.append(other)
    elif mutation == "pending-upload":
        uploads[0]["state"] = "started"
    elif mutation == "current-failure":
        statuses[0]["state"] = "failure"
    elif mutation == "new-status":
        statuses[0]["id"] = 99
    else:
        value["untrusted"] = True
    with pytest.raises((ValueError, gate.Pending)):
        gate.validate_receipt(
            value,
            measurement=measured,
            run=run,
            artifact=artifact,
            uploads=uploads,
            statuses=statuses,
            now=NOW + 20,
        )


def test_semantic_xml_removes_only_timestamp(gate):
    a = '<coverage timestamp="1" x="a"><!--proof--><line hits="1"/></coverage>'
    b = a.replace('timestamp="1"', 'timestamp="2"')
    assert gate.xml_fingerprint(a) == gate.xml_fingerprint(b)
    assert gate.xml_fingerprint(a) != gate.xml_fingerprint(
        a.replace('hits="1"', 'hits="2"')
    )
    assert gate.xml_fingerprint(a) != gate.xml_fingerprint(
        a.replace("<!--proof-->", "")
    )
    assert gate.xml_fingerprint(a) != gate.xml_fingerprint(a.replace('x="a"', 'x="b"'))


def test_release_waits_outside_lock_and_cannot_upload_or_notify():
    ci = yaml.load(
        (ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader
    )
    release = yaml.load(
        (ROOT / ".github/workflows/release.yml").read_text(), Loader=yaml.BaseLoader
    )
    wait = ci["jobs"]["coverage-receipt"]
    assert "concurrency" not in wait
    assert wait["timeout-minutes"] == "25"
    assert "python -m tools.check_codecov prepare" in str(wait)
    assert (
        wait["steps"][-1]["env"]["COVERAGE_REUSE_ONLY"]
        == "${{ inputs.coverage_reuse_only == true }}"
    )
    assert release["jobs"]["ci"]["with"]["coverage_reuse_only"] == "true"
    job = ci["jobs"]["coverage"]
    assert "coverage-receipt" in job["needs"]
    assert job["concurrency"] == {
        "group": "codecov-${{ github.repository }}-${{ github.sha }}",
        "cancel-in-progress": "false",
        "queue": "max",
    }
    for step in job["steps"]:
        if "codecov/codecov-action" in step.get("uses", ""):
            assert "inputs.coverage_reuse_only != true" in step["if"]
        if "download-artifact" in step.get("uses", ""):
            assert step["with"]["digest-mismatch"] == "error"


def test_notification_policy_does_not_modify_coverage_thresholds():
    assert yaml.safe_load((ROOT / "codecov.yml").read_text()) == {
        "codecov": {
            "require_ci_to_pass": False,
            "notify": {"wait_for_ci": False, "manual_trigger": True},
        }
    }


def test_pagination_rejects_duplicate_status_ids(gate, evidence):
    context = evidence[0]
    first = [{"id": i} for i in range(1, 101)]
    pages = iter([first, [{"id": 100}]])
    api = gate.API(context, NOW + 600, read=lambda url: next(pages), clock=lambda: NOW)
    with pytest.raises(ValueError):
        api.statuses()


def test_pagination_rejects_off_host_next_link(gate, evidence):
    context = evidence[0]
    api = gate.API(
        context,
        NOW + 600,
        read=lambda url: {
            "count": 2,
            "results": [evidence[2][0]],
            "next": "https://foreign.example/page=2",
        },
        clock=lambda: NOW,
    )
    with pytest.raises(ValueError):
        api.uploads()


def test_required_wait_expires_after_twenty_minutes_without_reset(gate):
    now, calls = [NOW], []

    def unavailable():
        calls.append(now[0])
        raise gate.Pending("No canonical receipt")

    with pytest.raises(ValueError, match="deadline"):
        gate.wait(
            unavailable,
            NOW + 1200,
            clock=lambda: now[0],
            sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
        )
    assert now[0] == NOW + 1200
    assert len(calls) == 40


def test_http_client_ignores_local_curl_config_and_does_not_log_token(
    gate, evidence, monkeypatch
):
    monkeypatch.setenv("GITHUB_TOKEN", "private_test_token")
    commands = []

    def request(command, **kwargs):
        commands.append((command, kwargs))
        return gate.subprocess.CompletedProcess(
            command, 0, "{}\n403", "private_test_token"
        )

    monkeypatch.setattr(gate.subprocess, "run", request)
    api = gate.API(evidence[0], NOW + 5, clock=lambda: NOW)
    with pytest.raises(ValueError) as error:
        api.get(api.github + "actions/runs/100")
    command, kwargs = commands[0]
    assert command[1] == "--disable"
    assert "private_test_token" not in str(command)
    assert "private_test_token" not in str(error.value)
    assert kwargs["timeout"] <= 5


def test_plan_does_not_reset_the_locked_assessment_deadline(
    gate, evidence, monkeypatch, tmp_path
):
    context = evidence[0]
    for key, value in context.items():
        monkeypatch.setenv("GITHUB_" + key.upper(), value)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(gate.reports, "checkout_sha", lambda: SHA)
    now = [NOW]
    monkeypatch.setattr(gate.time, "time", lambda: now[0])
    monkeypatch.setattr(gate, "discover", lambda api, **kwargs: None)
    monkeypatch.setattr(gate, "outputs", lambda value: None)
    deadlines = []
    monkeypatch.setattr(
        gate, "plan", lambda api, *args, **kwargs: deadlines.append(api.deadline) or {}
    )
    assert gate.main(["discover"]) == 0
    now[0] += 60
    assert gate.main(["plan"]) == 0
    assert deadlines == [NOW + 600]


def test_rerun_can_find_an_immutable_successful_prior_attempt(gate, evidence):
    context, _, _, _, previous, artifact = evidence
    current = dict(previous, run_attempt=2, status="in_progress", conclusion=None)
    context = dict(context, run_attempt="2")

    def read(url):
        if "/workflows/" in url:
            return {"total_count": 1, "workflow_runs": [current]}
        if "/artifacts?" in url:
            return {"total_count": 1, "artifacts": [artifact]}
        if url.endswith("/attempts/1"):
            return previous
        raise AssertionError("Unexpected API path")

    api = gate.API(context, NOW + 600, read=read, clock=lambda: NOW + 20)
    selected = gate.discover(api, required=False)
    assert selected is not None
    assert selected["run"]["run_attempt"] == 1


def test_recheck_uses_exact_accepted_attempt_not_the_latest_attempt(gate, evidence):
    context, _, _, _, previous, artifact = evidence
    seen = []

    def read(url):
        seen.append(url)
        if url.endswith("/attempts/1"):
            return previous
        if "/actions/artifacts/" in url:
            return artifact
        return dict(previous, run_attempt=2, status="in_progress", conclusion=None)

    api = gate.API(
        dict(context, run_id="300"), NOW + 600, read=read, clock=lambda: NOW + 20
    )
    gate.recheck_selection(api, {"run": previous, "artifact": artifact})
    assert seen[0].endswith("/actions/runs/100/attempts/1")


def test_locked_discovery_never_waits_for_an_incomplete_producer(gate, evidence):
    context, _, _, _, previous, _ = evidence
    previous.update(status="in_progress", conclusion=None)

    def read(url):
        if "/workflows/" in url:
            return {"total_count": 1, "workflow_runs": [previous]}
        return {"total_count": 0, "artifacts": []}

    api = gate.API(dict(context, run_id="102"), NOW + 600, read=read, clock=lambda: NOW)
    assert gate.discover(api, required=False) is None


def test_prelock_wait_only_orders_older_runs_not_newer_or_current(gate, evidence):
    context, _, _, _, previous, _ = evidence
    previous.update(status="in_progress", conclusion=None)
    runs = [dict(previous, id=101), dict(previous, id=102)]
    api = gate.API(
        dict(context, run_id="102"),
        NOW + 1200,
        read=lambda url: {"total_count": len(runs), "workflow_runs": runs},
        clock=lambda: NOW,
    )
    with pytest.raises(gate.Pending):
        gate.wait_producers(api, pull=None)
    api.context = dict(context, run_id="101")
    assert gate.wait_producers(api, pull=None) is None
    runs[0].update(status="completed", conclusion="failure")
    api.context = dict(context, run_id="102")
    assert gate.wait_producers(api, pull=None) is None


def test_branch_assessment_promotes_once_to_main_preserving_origin(gate, evidence):
    context, statuses, uploads, measured, run, artifact = evidence
    value = receipt(gate, evidence)
    run["head_branch"] = artifact["workflow_run"]["head_branch"] = "feature"
    gate.validate_receipt(
        value,
        measurement=measured,
        run=run,
        artifact=artifact,
        uploads=uploads,
        statuses=statuses,
        now=NOW + 20,
        canonical=False,
    )
    with pytest.raises(ValueError):
        gate.validate_receipt(
            value,
            measurement=measured,
            run=run,
            artifact=artifact,
            uploads=uploads,
            statuses=statuses,
            now=NOW + 20,
        )
    main = dict(context, run_id="101")
    promoted = gate.promote(value, main, {"run": run, "artifact": artifact})
    assert promoted["source"] == main
    assert promoted["accepted_at"] == value["accepted_at"]
    assert promoted["assessment_origin"]["source"] == context
    main_run = dict(run, id=101, head_branch="main", created_at=iso(NOW + 10))
    main_artifact = copy.deepcopy(artifact)
    main_artifact.update(id=201, name=f"codecov-acceptance-{SHA}-101-1")
    main_artifact["workflow_run"].update(id=101, head_branch="main")
    gate.validate_receipt(
        promoted,
        measurement=measured,
        run=main_run,
        artifact=main_artifact,
        uploads=uploads,
        statuses=statuses,
        now=NOW + 30,
        origin_run=run,
        origin_artifact=artifact,
    )
    again = gate.promote(
        promoted,
        dict(context, run_id="102"),
        {"run": main_run, "artifact": main_artifact},
    )
    assert again["assessment_origin"] == promoted["assessment_origin"]
    assert again["accepted_at"] == value["accepted_at"]
    assert "assessment_origin" not in again["assessment_origin"]
    artifact["expires_at"] = iso(NOW - 1)
    with pytest.raises(ValueError):
        gate.validate_receipt(
            promoted,
            measurement=measured,
            run=main_run,
            artifact=main_artifact,
            uploads=uploads,
            statuses=statuses,
            now=NOW + 30,
            origin_run=run,
            origin_artifact=artifact,
        )


def test_pr_receipt_reuses_only_same_pull_not_main_or_another_pull(gate, evidence):
    context, statuses, uploads, measured, run, artifact = evidence
    for status in statuses:
        status["target_url"] = f"https://app.codecov.io/gh/{REPO}/pull/7"
    value = gate.make_receipt(
        measured,
        context,
        uploads,
        statuses,
        baseline={},
        notified_at=NOW - 1,
        now=NOW + 1,
        pull=7,
        pr={"head_sha": "d" * 40, "base_sha": "e" * 40},
    )
    run.update(event="pull_request", head_sha="d" * 40, pull_requests=[])
    artifact["workflow_run"]["head_sha"] = "d" * 40
    gate.validate_receipt(
        value,
        measurement=measured,
        run=run,
        artifact=artifact,
        uploads=uploads,
        statuses=statuses,
        now=NOW + 20,
        canonical=False,
        pull=7,
        pr={"head_sha": "d" * 40, "base_sha": "e" * 40},
    )
    for kwargs in ({}, {"canonical": False}, {"canonical": False, "pull": 8}):
        with pytest.raises(ValueError):
            gate.validate_receipt(
                value,
                measurement=measured,
                run=run,
                artifact=artifact,
                uploads=uploads,
                statuses=statuses,
                now=NOW + 20,
                **kwargs,
            )


def test_workflow_prepares_all_producers_outside_lock_and_retains_promotions():
    ci = yaml.load(
        (ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader
    )
    before = ci["jobs"]["coverage-receipt"]
    assert "if" not in before
    assert "prepare" in str(before)
    assert "concurrency" not in before
    retain = next(
        s
        for s in ci["jobs"]["coverage"]["steps"]
        if s.get("name") == "Retain acceptance receipt"
    )
    assert "steps.plan.outputs.reused" not in retain["if"]
    assert "github.event_name == 'pull_request'" in retain["if"]
    assert "toJSON(inputs.redis) != 'false'" in retain["if"]


def test_actual_sealed_profiles_branch_main_release_flow_uses_one_assessment(
    gate, evidence, monkeypatch, tmp_path
):
    """Real CLI and artifact validation, injected GET responses, no hosted writes."""
    import shutil

    context, statuses, uploads, _, source_run, source_artifact = evidence
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(gate.reports, "checkout_sha", lambda: SHA)
    monkeypatch.setattr(gate.reports.platform, "python_version", lambda: "3.12.11")
    monkeypatch.setattr(gate.reports.metadata, "version", lambda name: "6.1.1")
    monkeypatch.setattr(gate.time, "time", lambda: NOW + 20)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "outputs"))
    monkeypatch.setenv("COVERAGE_REDIS", "true")
    monkeypatch.setenv("COVERAGE_REUSE_ONLY", "false")
    monkeypatch.setenv("COVERAGE_PULL", "")
    for name in gate.POLICY:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixed policy")
    remote = {"runs": [], "artifacts": [], "uploads": [], "statuses": []}

    def read(url):
        if "/workflows/" in url:
            return {"total_count": len(remote["runs"]), "workflow_runs": remote["runs"]}
        if "/runs/" in url and "/artifacts?" in url:
            rid = int(url.split("/runs/")[1].split("/")[0])
            rows = [a for a in remote["artifacts"] if a["workflow_run"]["id"] == rid]
            return {"total_count": len(rows), "artifacts": rows}
        if "/attempts/" in url:
            rid = int(url.split("/runs/")[1].split("/")[0])
            return next(r for r in remote["runs"] if r["id"] == rid)
        if "/actions/artifacts/" in url:
            return next(
                a for a in remote["artifacts"] if str(a["id"]) == url.rsplit("/", 1)[1]
            )
        if "/uploads/" in url:
            return {
                "count": len(remote["uploads"]),
                "results": remote["uploads"],
                "next": None,
            }
        if "/statuses?" in url:
            return remote["statuses"]
        raise AssertionError("Unexpected read-only API path")

    real_api = gate.API
    monkeypatch.setattr(
        gate,
        "API",
        lambda ctx, deadline: real_api(
            ctx, deadline, read=read, clock=lambda: NOW + 20
        ),
    )

    def job(rid):
        for name in ("coverage-reports", "coverage-acceptance", ".codecov-prior"):
            shutil.rmtree(tmp_path / name, ignore_errors=True)
        for path in tmp_path.glob(".codecov-*.json"):
            path.unlink()
        ctx = dict(context, run_id=str(rid))
        for key, value in ctx.items():
            monkeypatch.setenv("GITHUB_" + key.upper(), value)
        xml = (
            f'<coverage timestamp="{(NOW + 20) * 1000}" '
            'lines-covered="95" lines-valid="100" '
            'branches-covered="95" branches-valid="100">'
            f"<sources><source>{tmp_path}</source></sources><packages><package><classes>"
            '<class filename="src/dj_hyperview/conf.py"><lines>'
            '<line number="1" hits="1"/>'
            "</lines></class></classes></package></packages></coverage>"
        )
        report = tmp_path / "coverage.xml"
        report.write_text(xml)
        for profile in ("default", "redis"):
            gate.reports.seal(
                report,
                tmp_path / "coverage-reports" / profile,
                context=ctx,
                profile=profile,
                started_at=NOW,
                checkout=tmp_path,
            )
        return ctx

    job(100)
    assert gate.main(["prepare"]) == gate.main(["discover"]) == gate.main(["plan"]) == 0
    initial_plan = gate.load(".codecov-plan.json")
    upload = copy.deepcopy(uploads[0])
    upload.update(name=initial_plan["name"], created_at=iso(NOW + 20))
    remote["uploads"] = [upload]
    assert gate.main(["merged"]) == 0
    remote["statuses"] = copy.deepcopy(statuses)
    for status in remote["statuses"]:
        status["created_at"] = iso(NOW + 20)
    assert gate.main(["accept"]) == 0
    branch_receipt = gate.load("coverage-acceptance/receipt.json")
    assert branch_receipt["assessment_origin"] is None
    source_run["head_branch"] = source_artifact["workflow_run"]["head_branch"] = (
        "feature"
    )
    remote["runs"] = [source_run]
    remote["artifacts"] = [source_artifact]
    job(101)
    assert gate.main(["prepare"]) == gate.main(["discover"]) == 0
    gate.save(
        ".codecov-prior/receipt.json", branch_receipt
    )  # models digest-checked pinned download
    assert gate.main(["plan"]) == 0
    assert not (tmp_path / ".codecov-plan.json").exists()
    main_receipt = gate.load("coverage-acceptance/receipt.json")
    assert main_receipt["accepted_at"] == branch_receipt["accepted_at"]
    assert main_receipt["source"]["run_id"] == "101"
    main_run = dict(source_run, id=101, head_branch="main")
    main_artifact = copy.deepcopy(source_artifact)
    main_artifact.update(id=201, name=f"codecov-acceptance-{SHA}-101-1")
    main_artifact["workflow_run"].update(id=101, head_branch="main")
    remote["runs"].append(main_run)
    remote["artifacts"].append(main_artifact)
    job(105)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    assert gate.main(["discover"]) == 0
    gate.save(".codecov-prior/receipt.json", main_receipt)
    assert gate.main(["plan"]) == 0
    assert not (tmp_path / ".codecov-plan.json").exists()
    job(102)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("COVERAGE_REUSE_ONLY", "true")
    assert gate.main(["prepare"]) == gate.main(["discover"]) == 0
    gate.save(".codecov-prior/receipt.json", main_receipt)
    assert gate.main(["plan"]) == 0
    assert not (tmp_path / "coverage-acceptance").exists()
    assert not (tmp_path / ".codecov-plan.json").exists()
    assert len(remote["uploads"]) == 1
    assert gate.main(["merged"]) == gate.main(["accept"]) == 2


@pytest.mark.parametrize("changed", ["digest", "expired", "failure", "foreign-origin"])
def test_promoted_receipt_rechecks_original_proof(gate, evidence, changed):
    context, statuses, uploads, measured, run, artifact = evidence
    original = receipt(gate, evidence)
    value = gate.promote(
        original, dict(context, run_id="101"), {"run": run, "artifact": artifact}
    )
    current = dict(run, id=101)
    current_artifact = copy.deepcopy(artifact)
    current_artifact.update(id=201, name=f"codecov-acceptance-{SHA}-101-1")
    current_artifact["workflow_run"]["id"] = 101
    if changed == "digest":
        artifact["digest"] = "sha256:" + "f" * 64
    elif changed == "expired":
        artifact["expired"] = True
    elif changed == "failure":
        run["conclusion"] = "failure"
    else:
        value["assessment_origin"]["source"]["sha"] = "f" * 40
    with pytest.raises(ValueError):
        gate.validate_receipt(
            value,
            measurement=measured,
            run=current,
            artifact=current_artifact,
            uploads=uploads,
            statuses=statuses,
            now=NOW + 20,
            origin_run=run,
            origin_artifact=artifact,
        )


def test_manual_first_assessment_stops_before_upload_and_points_to_main(
    gate, evidence, monkeypatch, tmp_path, capsys
):
    context = evidence[0]
    monkeypatch.chdir(tmp_path)
    for key, value in context.items():
        monkeypatch.setenv("GITHUB_" + key.upper(), value)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("COVERAGE_REDIS", "false")
    monkeypatch.setenv("COVERAGE_REUSE_ONLY", "false")
    monkeypatch.setenv("COVERAGE_PULL", "")
    monkeypatch.setattr(gate.reports, "checkout_sha", lambda: SHA)
    monkeypatch.setattr(gate.time, "time", lambda: NOW)
    monkeypatch.setattr(
        gate,
        "measure",
        lambda *args: (evidence[3], {"name": "test", "files": "coverage.xml"}),
    )
    monkeypatch.setattr(gate.API, "uploads", lambda api: [])
    monkeypatch.setattr(gate, "outputs", lambda values: None)
    gate.save(".codecov-selection.json", {})
    gate.save(".codecov-budget.json", {"context": context, "deadline": NOW + 600})
    assert gate.main(["plan"]) == 2
    assert not (tmp_path / ".codecov-plan.json").exists()
    assert "push/main CI" in capsys.readouterr().err


def test_pr_discovery_binds_merge_report_to_distinct_source_head(gate, evidence):
    """Synthetic event/artifact; empty run association matches observed public API."""
    context, _, _, _, run, artifact = evidence
    head, base = "d" * 40, "e" * 40
    run.update(
        event="pull_request", head_sha=head, pull_requests=[], head_branch="feature"
    )
    artifact["workflow_run"].update(head_sha=head, head_branch="feature")
    seen = []

    def read(url):
        seen.append(url)
        if "/workflows/" in url:
            return {"total_count": 1, "workflow_runs": [run]}
        if "/artifacts?" in url:
            return {"total_count": 1, "artifacts": [artifact]}
        if "/attempts/1" in url:
            return run
        if "/actions/artifacts/" in url:
            return artifact
        raise AssertionError("Unexpected API path")

    api = gate.API(
        dict(context, run_attempt="2"), NOW + 600, read=read, clock=lambda: NOW + 20
    )
    api.pr = {"head_sha": head, "base_sha": base}
    selected = gate.discover(api, required=False, pull=7)
    assert selected is not None
    assert "head_sha=" + head in seen[0]
    assert selected["artifact"]["name"].startswith("codecov-acceptance-" + SHA)
    gate.recheck_selection(api, selected, canonical=False, pull=7)


@pytest.mark.parametrize("mismatch", [None, "head", "base", "merge", "pull"])
def test_pr_checkout_and_receipt_reject_other_identity(
    gate, evidence, monkeypatch, mismatch
):
    context, statuses, uploads, measured, run, artifact = evidence
    pr = {"head_sha": "d" * 40, "base_sha": "e" * 40}
    run.update(event="pull_request", head_sha=pr["head_sha"], pull_requests=[])
    artifact["workflow_run"]["head_sha"] = pr["head_sha"]
    monkeypatch.setattr(gate.reports, "checkout_sha", lambda: SHA)
    commands = []

    def cat_file(command, **kwargs):
        commands.append(command)
        assert "cat-file" in command  # works with checkout's default shallow clone
        return gate.subprocess.CompletedProcess(
            command,
            0,
            f"tree {'f' * 40}\nparent {pr['base_sha']}\n"
            f"parent {pr['head_sha']}\n\nmessage",
            "",
        )

    monkeypatch.setattr(gate.subprocess, "run", cat_file)
    gate.verify_checkout(context, pr)
    value = gate.make_receipt(
        measured,
        context,
        uploads,
        statuses,
        baseline={},
        notified_at=NOW - 1,
        now=NOW + 1,
        pull=7,
        pr=pr,
    )
    changed = copy.deepcopy(pr)
    number = 7
    if mismatch == "head":
        changed["head_sha"] = "f" * 40
    elif mismatch == "base":
        changed["base_sha"] = "f" * 40
    elif mismatch == "merge":
        measured["sha"] = "f" * 40
    elif mismatch == "pull":
        number = 8

    def verify():
        gate.verify_checkout(dict(context, sha=measured["sha"]), changed)
        gate.validate_receipt(
            value,
            measurement=measured,
            run=run,
            artifact=artifact,
            uploads=uploads,
            statuses=statuses,
            now=NOW + 20,
            canonical=False,
            pull=number,
            pr=changed,
        )

    if mismatch is None:
        verify()
        assert value["measurement"]["sha"] == SHA
        assert value["pr"] == pr
    else:
        with pytest.raises(ValueError):
            verify()


@pytest.mark.parametrize(
    "association",
    [[], [{"number": 7, "head": {"sha": "d" * 40}, "base": {"sha": "e" * 40}}]],
)
def test_pr_run_association_can_be_empty_but_not_contradictory(
    gate, evidence, association
):
    context, _, _, _, run, artifact = evidence
    pr = {"head_sha": "d" * 40, "base_sha": "e" * 40}
    run.update(event="pull_request", head_sha=pr["head_sha"], pull_requests=association)
    artifact["workflow_run"]["head_sha"] = pr["head_sha"]
    gate.trusted_artifact(
        run, artifact, context, NOW + 20, canonical=False, pull=7, pr=pr
    )
    run["pull_requests"] = [
        {"number": 8, "head": {"sha": pr["head_sha"]}, "base": {"sha": pr["base_sha"]}}
    ]
    with pytest.raises(ValueError):
        gate.trusted_artifact(
            run, artifact, context, NOW + 20, canonical=False, pull=7, pr=pr
        )


def test_workflow_passes_event_head_and_base_without_changing_report_sha():
    ci = yaml.load(
        (ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader
    )
    for env in (
        ci["jobs"]["coverage"]["env"],
        ci["jobs"]["coverage-receipt"]["steps"][-1]["env"],
    ):
        assert (
            env["COVERAGE_PR_HEAD"] == "${{ github.event.pull_request.head.sha || '' }}"
        )
        assert (
            env["COVERAGE_PR_BASE"] == "${{ github.event.pull_request.base.sha || '' }}"
        )
        assert env["COVERAGE_PULL"] == "${{ github.event.pull_request.number || '' }}"
    for step in ci["jobs"]["coverage"]["steps"]:
        if "codecov/codecov-action" in step.get("uses", ""):
            assert step["with"]["override_commit"] == "${{ github.sha }}"
