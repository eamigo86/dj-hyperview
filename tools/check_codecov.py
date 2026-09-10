"""Bounded Codecov assessment and reuse of trusted canonical CI receipts."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from xml.etree import ElementTree as ET

from tools import coverage_artifacts as reports

CONTEXTS = ("codecov/project", "codecov/patch")
POLICY = (
    "codecov.yml",
    ".github/workflows/ci.yml",
    ".github/workflows/release.yml",
    "tools/check_codecov.py",
    "tools/coverage_artifacts.py",
    "tools/test_matrix.py",
)


class Pending(Exception):
    """The required evidence is not yet available, within the same deadline."""


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()


def seconds(value: str) -> float:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamp must include timezone")
    return parsed.timestamp()


def xml_fingerprint(xml: str) -> str:
    """Canonicalize the full XML, retaining comments and all but root timestamp."""
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True))
    root = ET.fromstring(xml, parser=parser)
    root.attrib.pop("timestamp", None)
    canonical = ET.canonicalize(
        ET.tostring(root, encoding="unicode"), with_comments=True
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def latest_statuses(rows: list[dict], context: dict, pull: int | None = None) -> dict:
    result = {}
    for name in CONTEXTS:
        candidates = [s for s in rows if s.get("context") == name]
        if not candidates:
            raise Pending("Missing Codecov context")
        if any(type(s.get("id")) is not int or s["id"] <= 0 for s in candidates):
            raise ValueError("Invalid status identity")
        status = max(candidates, key=lambda s: s["id"])
        creator = status.get("creator", {})
        if (
            creator.get("id") != 22429695
            or creator.get("login") != "codecov[bot]"
            or creator.get("type") != "Bot"
        ):
            raise ValueError("Status issuer is not Codecov")
        repository, sha = context["repository"], context["sha"]
        if (
            status.get("url")
            != f"https://api.github.com/repos/{repository}/statuses/{sha}"
        ):
            raise ValueError("Status belongs to another commit")
        target = urlsplit(status["target_url"])
        paths = {f"/gh/{repository}/commit/{sha}"}
        if pull is not None:
            paths.add(f"/gh/{repository}/pull/{pull}")
        if (
            target.scheme != "https"
            or target.netloc != "app.codecov.io"
            or target.path not in paths
            or target.fragment
        ):
            raise ValueError("Unexpected Codecov target")
        if status.get("state") not in ("success", "failure", "error", "pending"):
            raise ValueError("Unknown status state")
        seconds(status["created_at"])
        result[name] = {
            key: status[key] for key in ("id", "state", "created_at", "target_url")
        }
    return result


def merged_uploads(rows: list[dict], sha: str) -> list[dict]:
    result = []
    for row in rows:
        if row.get("state") in ("error", "rejected", "failed") or row.get("errors"):
            raise ValueError("Codecov rejected an upload")
        if row.get("state") != "merged" or row.get("state_name") != "MERGED":
            raise Pending("Upload processing incomplete")
        identity = row["storage_path"]
        if not isinstance(identity, str) or f"/{sha}/" not in identity:
            raise ValueError("Unexpected upload identity")
        totals = row["totals"]
        if not isinstance(totals, dict) or any(
            type(totals.get(k)) is not int or totals[k] < 0
            for k in ("lines", "hits", "misses", "partials")
        ):
            raise ValueError("Invalid upload counters")
        if (
            totals["lines"] <= 0
            or sum(totals[k] for k in ("hits", "misses", "partials")) != totals["lines"]
        ):
            raise ValueError("Incomplete upload counters")
        result.append(
            {
                key: row[key]
                for key in (
                    "storage_path",
                    "name",
                    "provider",
                    "build_url",
                    "created_at",
                    "updated_at",
                    "totals",
                )
            }
        )
    if not result or len({r["storage_path"] for r in result}) != len(result):
        raise ValueError("Missing or ambiguous upload set")
    return sorted(result, key=lambda row: row["storage_path"])


def pr_identity(pull: int | None, pr: dict | None) -> None:
    if pull is None:
        if pr is not None:
            raise ValueError("Unexpected PR identity")
    elif (
        type(pull) is not int
        or pull <= 0
        or not isinstance(pr, dict)
        or set(pr) != {"head_sha", "base_sha"}
        or any(
            not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{40}", v)
            for v in pr.values()
        )
    ):
        raise ValueError("Invalid PR identity")


def verify_checkout(context: dict, pr: dict | None) -> None:
    if reports.checkout_sha() != context["sha"]:
        raise ValueError("Checkout SHA differs from requested assessment")
    if pr is not None:
        # Raw headers retain both parents in actions/checkout's shallow clone.
        commit = subprocess.run(
            ["git", "--no-replace-objects", "cat-file", "-p", context["sha"]],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        parents = [
            line.removeprefix("parent ")
            for line in commit.stdout.split("\n\n", 1)[0].splitlines()
            if line.startswith("parent ")
        ]
        if parents != [pr["base_sha"], pr["head_sha"]]:
            raise ValueError(
                "PR merge checkout does not bind the event's base and head"
            )


def eligible(
    run: dict,
    context: dict,
    *,
    canonical: bool,
    pull: int | None,
    pr: dict | None = None,
) -> bool:
    """Keep push and PR assessments separate; release accepts only push/main."""
    pr_identity(pull, pr)
    if (
        run.get("head_sha") != (pr["head_sha"] if pr else context["sha"])
        or run.get("path") != ".github/workflows/ci.yml"
        or any(
            run.get(k, {}).get("full_name") != context["repository"]
            for k in ("repository", "head_repository")
        )
    ):
        return False
    if pull is not None:
        return (
            not canonical
            and run.get("event") == "pull_request"
            and isinstance(run.get("pull_requests"), list)
            and (
                not run["pull_requests"]
                or any(
                    item.get("number") == pull
                    and item.get("head", {}).get("sha") == pr["head_sha"]
                    and item.get("base", {}).get("sha") == pr["base_sha"]
                    for item in run["pull_requests"]
                )
            )
        )
    return run.get("event") == "push" and (
        not canonical or run.get("head_branch") == "main"
    )


def trusted_artifact(
    run: dict,
    artifact: dict,
    context: dict,
    now: float,
    *,
    canonical: bool = True,
    pull: int | None = None,
    pr: dict | None = None,
) -> None:
    """Trust an immutable artifact only after its exact producer attempt succeeds."""
    if (
        not eligible(run, context, canonical=canonical, pull=pull, pr=pr)
        or run.get("status") != "completed"
        or run.get("conclusion") != "success"
    ):
        raise ValueError("Receipt producer is not an eligible successful CI attempt")
    name = f"codecov-acceptance-{context['sha']}-{run['id']}-{run['run_attempt']}"
    owner = artifact.get("workflow_run", {})
    if (
        artifact.get("name") != name
        or artifact.get("expired") is not False
        or seconds(artifact["expires_at"]) <= now
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", artifact.get("digest", ""))
        or type(artifact.get("id")) is not int
        or not 0 < artifact.get("size_in_bytes", 0) <= 4 * 1024 * 1024
        or owner.get("id") != run["id"]
        or owner.get("head_sha") != (pr["head_sha"] if pr else context["sha"])
        or owner.get("head_branch") != run.get("head_branch")
    ):
        raise ValueError("Receipt artifact identity or lifetime is invalid")


def make_receipt(
    measurement: dict,
    context: dict,
    uploads: list,
    statuses: list,
    *,
    baseline: dict,
    notified_at: float,
    now: float,
    pull: int | None = None,
    pr: dict | None = None,
) -> dict:
    pr_identity(pull, pr)
    current = latest_statuses(statuses, context, pull)
    for name, status in current.items():
        if status["id"] <= baseline.get(name, 0) or seconds(status["created_at"]) < int(
            notified_at
        ):
            raise Pending("No fresh assessment after notification")
        if status["state"] in ("failure", "error"):
            raise ValueError("Codecov assessment failed")
        if status["state"] != "success":
            raise Pending("Codecov assessment pending")
    return copy.deepcopy(
        {
            "schema": 3,
            "pull": pull,
            "pr": pr,
            "assessment_origin": None,
            "measurement": measurement,
            "source": context,
            "accepted_at": now,
            "uploads": merged_uploads(uploads, context["sha"]),
            "statuses": current,
        }
    )


def validate_receipt(
    receipt: dict,
    *,
    measurement: dict,
    run: dict,
    artifact: dict,
    uploads: list,
    statuses: list,
    now: float,
    canonical: bool = True,
    pull: int | None = None,
    pr: dict | None = None,
    origin_run: dict | None = None,
    origin_artifact: dict | None = None,
) -> None:
    if not isinstance(receipt, dict) or set(receipt) != {
        "schema",
        "pull",
        "pr",
        "assessment_origin",
        "measurement",
        "source",
        "accepted_at",
        "uploads",
        "statuses",
    }:
        raise ValueError("Invalid receipt schema")
    context = receipt["source"]
    reports._identity(context)
    pr_identity(pull, pr)
    trusted_artifact(run, artifact, context, now, canonical=canonical, pull=pull, pr=pr)
    if (
        type(receipt["schema"]) is not int
        or receipt["schema"] != 3
        or receipt["pull"] != pull
        or receipt["pr"] != pr
        or set(measurement["profiles"]) != {"default", "redis"}
        or digest(receipt["measurement"]) != digest(measurement)
        or context["sha"] != measurement["sha"]
        or context["repository"] != measurement["repository"]
        or context["run_id"] != str(run["id"])
        or context["run_attempt"] != str(run["run_attempt"])
        or type(receipt["accepted_at"]) not in (int, float)
        or not receipt["accepted_at"] <= now
    ):
        raise ValueError("Receipt measurement or producer mismatch")
    origin = receipt["assessment_origin"]
    if origin is not None:
        if (
            not isinstance(origin, dict)
            or set(origin) != {"source", "artifact", "accepted_at"}
            or origin["accepted_at"] != receipt["accepted_at"]
            or origin_run is None
            or origin_artifact is None
        ):
            raise ValueError("Invalid or unverified assessment origin")
        reports._identity(origin["source"])
        if any(origin["source"][k] != context[k] for k in ("repository", "sha")):
            raise ValueError("Assessment origin belongs to another source")
        trusted_artifact(
            origin_run, origin_artifact, context, now, canonical=False, pull=pull, pr=pr
        )
        if (
            origin["source"]["run_id"] != str(origin_run["id"])
            or origin["source"]["run_attempt"] != str(origin_run["run_attempt"])
            or origin["artifact"] != artifact_proof(origin_artifact)
        ):
            raise ValueError("Assessment origin proof changed")
        first = origin_run
    else:
        first = run
    if not seconds(first["created_at"]) <= receipt["accepted_at"]:
        raise ValueError("Acceptance predates the assessment producer")
    current = latest_statuses(statuses, context, pull)
    if (
        any(s["state"] != "success" for s in current.values())
        or current != receipt["statuses"]
        or digest(merged_uploads(uploads, context["sha"])) != digest(receipt["uploads"])
    ):
        raise ValueError("Accepted remote state changed")


def artifact_proof(artifact: dict) -> dict:
    return {k: artifact[k] for k in ("id", "name", "digest", "expires_at")}


def promote(receipt: dict, context: dict, selected: dict) -> dict:
    """Copy an assessment into a new producer artifact, without refreshing origin."""
    value = copy.deepcopy(receipt)
    if value["assessment_origin"] is None:
        value["assessment_origin"] = {
            "source": value["source"],
            "artifact": artifact_proof(selected["artifact"]),
            "accepted_at": value["accepted_at"],
        }
    value["source"] = copy.deepcopy(context)
    return value


class API:
    """Two fixed read-only services, bounded pagination and process deadlines."""

    def __init__(self, context: dict, deadline: float, read=None, clock=time.time):
        self.context, self.deadline, self.read, self.clock = (
            context,
            deadline,
            read,
            clock,
        )
        self.pr = None
        self.github = f"https://api.github.com/repos/{context['repository']}/"
        self.codecov = f"https://api.codecov.io/api/v2/github/{context['repository'].split('/')[0]}/repos/{context['repository'].split('/')[1]}/commits/{context['sha']}/"

    def get(self, url: str):
        if not any(url.startswith(base) for base in (self.github, self.codecov)):
            raise ValueError("Foreign API URL")
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise ValueError("Coverage acceptance deadline exceeded")
        if self.read:
            return self.read(url)
        timeout = min(10, remaining)
        config = ""
        if url.startswith(self.github):
            token = os.environ["GITHUB_TOKEN"]
            if any(c in token for c in "\r\n\x00"):
                raise ValueError("Invalid API credential")
            config = "header = " + json.dumps("Authorization: Bearer " + token) + "\n"
        command = [
            "curl",
            "--disable",
            "--silent",
            "--show-error",
            "--proto",
            "=https",
            "--request",
            "GET",
            "--max-time",
            str(timeout),
            "--connect-timeout",
            str(timeout),
            "--max-filesize",
            "4194304",
            "--config",
            "-",
            "--write-out",
            "\n%{http_code}",
            url,
        ]
        try:
            result = subprocess.run(
                command,
                input=config,
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise Pending("API request timed out") from error
        if result.returncode:
            raise Pending("API request unavailable")
        body, code = result.stdout.rsplit("\n", 1)
        if code == "404" and url.startswith(self.codecov) and "/uploads/" in url:
            return {"count": 0, "results": [], "next": None}
        if code == "429" or code.startswith("5"):
            raise Pending("API temporarily unavailable")
        if code != "200" or len(body) > 4194304:
            raise ValueError("API response rejected")
        return json.loads(body, object_pairs_hook=reports._unique)

    def pages(self, url: str, key: str | None = None, *, codecov: bool = False) -> list:
        base = urlsplit(url)
        query = parse_qs(base.query)
        query["page_size" if codecov else "per_page"] = ["100"]
        values, seen, total = [], set(), None
        for page in range(1, 11):
            query["page"] = [str(page)]
            target = urlunsplit(base._replace(query=urlencode(query, doseq=True)))
            if target in seen:
                raise ValueError("Repeated API page")
            seen.add(target)
            data = self.get(target)
            rows = data[key] if key else data
            if (
                not isinstance(rows, list)
                or len(rows) > 100
                or any(not isinstance(r, dict) for r in rows)
            ):
                raise ValueError("Invalid API page")
            values.extend(rows)
            if not codecov:
                identities = [row.get("id") for row in values]
                if any(
                    type(identity) is not int or identity <= 0
                    for identity in identities
                ) or len(set(identities)) != len(identities):
                    raise ValueError("Invalid or duplicate API identities")
            if key:
                count = data["count" if codecov else "total_count"]
                if (
                    type(count) is not int
                    or count < 0
                    or count > 1000
                    or (total is not None and total != count)
                ):
                    raise ValueError("Unstable or excessive pagination")
                total = count
                if codecov and data.get("next"):
                    next_page = urlsplit(data["next"])
                    if (next_page.scheme, next_page.netloc, next_page.path) != (
                        base.scheme,
                        base.netloc,
                        base.path,
                    ) or parse_qs(next_page.query).get("page") != [str(page + 1)]:
                        raise ValueError("Invalid pagination link")
                if len(values) == total:
                    if codecov and data.get("next"):
                        raise ValueError("Unexpected extra page")
                    return values
                if (
                    len(values) > total
                    or not rows
                    or (codecov and not data.get("next"))
                ):
                    raise ValueError("Incomplete pagination")
            elif len(rows) < 100:
                return values
        raise ValueError("Pagination limit exceeded")

    def statuses(self):
        return self.pages(self.github + f"commits/{self.context['sha']}/statuses")

    def uploads(self):
        return self.pages(self.codecov + "uploads/", "results", codecov=True)


def wait(callback, deadline: float, *, clock=time.time, sleep=time.sleep):
    while clock() < deadline:
        try:
            return callback()
        except Pending:
            sleep(min(30, max(0, deadline - clock())))
    raise ValueError("Coverage acceptance deadline exceeded")


def producer_runs(api: API, *, canonical: bool, pull: int | None) -> list:
    query = {
        "event": "pull_request" if pull is not None else "push",
        "head_sha": api.pr["head_sha"] if api.pr else api.context["sha"],
    }
    if canonical:
        query["branch"] = "main"
    rows = api.pages(
        api.github + "actions/workflows/ci.yml/runs?" + urlencode(query),
        "workflow_runs",
    )
    return [
        r
        for r in rows
        if eligible(r, api.context, canonical=canonical, pull=pull, pr=api.pr)
    ]


def wait_producers(api: API, *, pull: int | None) -> None:
    """Before taking the lock, wait only for older eligible runs, never peers ahead."""
    if any(
        r["id"] < int(api.context["run_id"]) and r.get("status") != "completed"
        for r in producer_runs(api, canonical=False, pull=pull)
    ):
        raise Pending("An earlier assessment producer is still running")


def discover(
    api: API, *, required: bool, canonical: bool = False, pull: int | None = None
) -> dict | None:
    """Never wait for a producer inside the lock; required discovery is pre-lock."""
    runs = producer_runs(api, canonical=canonical or required, pull=pull)
    for run in sorted(runs, key=lambda value: value["id"], reverse=True):
        artifacts = api.pages(
            api.github + f"actions/runs/{run['id']}/artifacts", "artifacts"
        )
        pattern = f"codecov-acceptance-{api.context['sha']}-{run['id']}-([1-9][0-9]*)"
        candidates = []
        for artifact in artifacts:
            match = re.fullmatch(pattern, artifact.get("name", ""))
            if match and not artifact.get("expired"):
                attempt = int(match[1])
                if attempt > run["run_attempt"]:
                    raise ValueError("Artifact claims an unknown run attempt")
                if str(run["id"]) == api.context["run_id"] and attempt >= int(
                    api.context["run_attempt"]
                ):
                    continue
                candidates.append((attempt, artifact))
        if len({attempt for attempt, _ in candidates}) != len(candidates):
            raise ValueError("Ambiguous acceptance artifact")
        for attempt, artifact in sorted(
            candidates, key=lambda item: item[0], reverse=True
        ):
            source = api.get(
                api.github + f"actions/runs/{run['id']}/attempts/{attempt}"
            )
            if (
                source.get("status") != "completed"
                or source.get("conclusion") != "success"
            ):
                continue
            trusted_artifact(
                source,
                artifact,
                api.context,
                api.clock(),
                canonical=canonical or required,
                pull=pull,
                pr=api.pr,
            )
            return {"run": source, "artifact": artifact}
    if required:
        raise Pending("Canonical main CI receipt not yet available")
    return None


def measure(context: dict, profiles: tuple[str, ...]) -> tuple[dict, dict]:
    root = Path.cwd()
    verified = reports.verify(
        root / "coverage-reports", context=context, profiles=profiles, checkout=root
    )
    values = {}
    for profile in profiles:
        folder = root / "coverage-reports" / profile
        manifest = json.loads(reports._read(folder / "manifest.json"))
        values[profile] = {
            "python": manifest["python"],
            "django": manifest["django"],
            "xml": xml_fingerprint(reports._read(folder / "coverage.xml").decode()),
        }
    policy = {
        path: hashlib.sha256(reports._read(root / path)).hexdigest() for path in POLICY
    }
    return {
        "repository": context["repository"],
        "sha": context["sha"],
        "profiles": values,
        "policy": digest(policy),
    }, verified


def load(path: str) -> dict:
    return json.loads(reports._read(Path(path)), object_pairs_hook=reports._unique)


def save(path: str, value: dict) -> None:
    target = reports._safe(Path(path))
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x") as stream:
        stream.write(json.dumps(value, sort_keys=True, allow_nan=False) + "\n")


def outputs(values: dict) -> None:
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
        for key, value in values.items():
            if any(c in str(value) for c in "\r\n"):
                raise ValueError("Unsafe job output")
            stream.write(f"{key}={value}\n")


def recheck_selection(
    api: API, selected: dict, *, canonical: bool = True, pull: int | None = None
) -> tuple[dict, dict]:
    run = api.get(
        api.github
        + f"actions/runs/{selected['run']['id']}/attempts/"
        + str(selected["run"]["run_attempt"])
    )
    artifact = api.get(api.github + f"actions/artifacts/{selected['artifact']['id']}")
    trusted_artifact(
        run,
        artifact,
        api.context,
        api.clock(),
        canonical=canonical,
        pull=pull,
        pr=api.pr,
    )
    if run["run_attempt"] != selected["run"]["run_attempt"] or any(
        artifact[k] != selected["artifact"][k]
        for k in ("id", "name", "digest", "expires_at")
    ):
        raise ValueError("Selected artifact changed")
    return run, artifact


def plan(
    api: API, profiles: tuple[str, ...], *, reuse_only: bool, pull: int | None
) -> dict:
    measured, verified = measure(api.context, profiles)
    selected = load(".codecov-selection.json")
    if selected:
        folder = reports._safe(Path(".codecov-prior"))
        if {p.name for p in folder.iterdir()} != {"receipt.json"}:
            raise ValueError("Invalid receipt download layout")
        receipt = load(".codecov-prior/receipt.json")
        for _ in range(2):
            run, artifact = recheck_selection(
                api, selected, canonical=reuse_only, pull=pull
            )
            origin_run = origin_artifact = None
            origin = receipt.get("assessment_origin")
            if origin is not None:
                origin_selected = {
                    "run": {
                        "id": int(origin["source"]["run_id"]),
                        "run_attempt": int(origin["source"]["run_attempt"]),
                    },
                    "artifact": origin["artifact"],
                }
                origin_run, origin_artifact = recheck_selection(
                    api, origin_selected, canonical=False, pull=pull
                )
            validate_receipt(
                receipt,
                measurement=measured,
                run=run,
                artifact=artifact,
                uploads=api.uploads(),
                statuses=api.statuses(),
                now=api.clock(),
                canonical=reuse_only,
                pull=pull,
                pr=api.pr,
                origin_run=origin_run,
                origin_artifact=origin_artifact,
            )
        if not reuse_only:
            save(
                "coverage-acceptance/receipt.json",
                promote(receipt, api.context, selected),
            )
        return {"reused": "true"}
    if reuse_only:
        raise ValueError("Release requires an accepted canonical main receipt")
    if os.environ.get("GITHUB_EVENT_NAME") not in ("push", "pull_request"):
        raise ValueError("First assessment requires ordinary push/main CI")
    existing = api.uploads()
    if existing:
        merged_uploads(existing, api.context["sha"])
    state = {
        "context": api.context,
        "measurement": measured,
        "name": verified["name"],
        "started": api.clock(),
        "deadline": api.deadline,
        "pull": pull,
        "pr": api.pr,
        "before_uploads": [u["storage_path"] for u in existing],
    }
    save(".codecov-plan.json", state)
    return dict(verified, reused="false")


def confirm_merge(api: API, state: dict) -> dict:
    rows = api.uploads()
    own = [u for u in rows if u.get("name") == state["name"]]
    if not own:
        raise Pending("Current upload not visible")
    if len(own) != 1:
        raise ValueError("Ambiguous current upload")
    upload = own[0]
    context = api.context
    expected_url = f"https://github.com/{context['repository']}/actions/runs/{context['run_id']}/attempts/{context['run_attempt']}"
    if (
        upload.get("provider") != "github-actions"
        or upload.get("build_url") != expected_url
        or seconds(upload["created_at"]) < int(state["started"])
    ):
        raise ValueError("Upload producer mismatch")
    accepted = merged_uploads(rows, context["sha"])
    if {u["storage_path"] for u in rows} != set(state["before_uploads"]) | {
        upload["storage_path"]
    }:
        raise ValueError("Unexpected concurrent upload")
    baseline = {}
    for status in api.statuses():
        if status.get("context") in CONTEXTS:
            baseline[status["context"]] = max(
                baseline.get(status["context"], 0), status["id"]
            )
    return dict(state, upload_set=accepted, baseline=baseline, notified_at=api.clock())


def accept(api: API, state: dict) -> dict:
    rows = api.uploads()
    if digest(merged_uploads(rows, api.context["sha"])) != digest(state["upload_set"]):
        raise ValueError("Upload set changed before acceptance")
    receipt = make_receipt(
        state["measurement"],
        api.context,
        rows,
        api.statuses(),
        baseline=state["baseline"],
        notified_at=state["notified_at"],
        now=api.clock(),
        pull=state["pull"],
        pr=state["pr"],
    )
    if (
        digest(merged_uploads(api.uploads(), api.context["sha"]))
        != digest(receipt["uploads"])
        or latest_statuses(api.statuses(), api.context, state["pull"])
        != receipt["statuses"]
    ):
        raise ValueError("Remote acceptance changed during verification")
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("prepare", "discover", "plan", "merged", "accept")
    )
    args = parser.parse_args(argv)
    try:
        context = {k: os.environ["GITHUB_" + k.upper()] for k in reports._CONTEXT}
        reports._identity(context)
        reuse = os.environ.get("COVERAGE_REUSE_ONLY", "false")
        selected_profiles = os.environ.get("COVERAGE_REDIS", "true")
        if reuse not in ("true", "false") or selected_profiles not in ("true", "false"):
            raise ValueError("Invalid workflow mode")
        if reuse == "true" and selected_profiles != "true":
            raise ValueError("Release requires both coverage profiles")
        pull_text = os.environ.get("COVERAGE_PULL", "")
        pull = int(pull_text) if pull_text else None
        if pull is not None and (pull <= 0 or reuse == "true"):
            raise ValueError("Invalid assessment audience")
        pr = (
            {
                "head_sha": os.environ["COVERAGE_PR_HEAD"],
                "base_sha": os.environ["COVERAGE_PR_BASE"],
            }
            if pull is not None
            else None
        )
        pr_identity(pull, pr)
        verify_checkout(context, pr)
        api = API(context, time.time() + (1200 if args.command == "prepare" else 600))
        api.pr = pr
        if args.command == "prepare":
            if reuse == "true":
                wait(lambda: discover(api, required=True), api.deadline)
            else:
                wait(lambda: wait_producers(api, pull=pull), api.deadline)
        elif args.command == "discover":
            save(".codecov-budget.json", {"context": context, "deadline": api.deadline})
            selected = discover(
                api, required=False, canonical=reuse == "true", pull=pull
            )
            if reuse == "true" and selected is None:
                raise ValueError("Previously awaited main receipt disappeared")
            save(".codecov-selection.json", selected or {})
            outputs(
                {
                    "artifact_id": selected["artifact"]["id"] if selected else "",
                    "run_id": selected["run"]["id"] if selected else "",
                }
            )
        elif args.command == "plan":
            budget = load(".codecov-budget.json")
            if (
                set(budget) != {"context", "deadline"}
                or budget["context"] != context
                or type(budget["deadline"]) not in (float, int)
                or not time.time() < budget["deadline"] <= time.time() + 600
            ):
                raise ValueError("Invalid or expired assessment budget")
            api.deadline = budget["deadline"]
            profiles = (
                ("default", "redis") if selected_profiles == "true" else ("default",)
            )
            outputs(
                wait(
                    lambda: plan(
                        api,
                        profiles,
                        reuse_only=reuse == "true",
                        pull=pull,
                    ),
                    api.deadline,
                )
            )
        else:
            state = load(
                ".codecov-plan.json"
                if args.command == "merged"
                else ".codecov-notify.json"
            )
            if (
                state["context"] != context
                or reuse == "true"
                or state["pull"] != pull
                or state["pr"] != pr
            ):
                raise ValueError("Assessment state does not belong to this run")
            api.deadline = state["deadline"]
            if args.command == "merged":
                save(
                    ".codecov-notify.json",
                    wait(lambda: confirm_merge(api, state), api.deadline),
                )
            else:
                save(
                    "coverage-acceptance/receipt.json",
                    wait(lambda: accept(api, state), api.deadline),
                )
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        ET.ParseError,
        Pending,
        subprocess.SubprocessError,
    ):
        print(
            "Codecov acceptance failed; required proof is unavailable or invalid. "
            "Inspect ordinary push/main CI; a tag or manual upload is not a bypass.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
