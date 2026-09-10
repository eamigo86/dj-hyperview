"""Seal and verify the current CI job's actual coverage reports; never merge XML."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import time
from importlib import metadata
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

from tools.test_matrix import check_coverage

_CONTEXT = {"repository", "sha", "run_id", "run_attempt"}
_FIELDS = _CONTEXT | {
    "schema",
    "profile",
    "python",
    "django",
    "checkout",
    "timestamp",
    "started_at",
    "sha256",
    "counters",
}
_LIMIT = 4 * 1024 * 1024


def _identity(context: dict[str, str]) -> None:
    if set(context) != _CONTEXT or not all(
        isinstance(v, str) for v in context.values()
    ):
        raise ValueError("Invalid job identity")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", context["repository"]):
        raise ValueError("Invalid repository")
    if not re.fullmatch(r"[0-9a-f]{40}", context["sha"]):
        raise ValueError("Invalid commit")
    if any(
        not re.fullmatch(r"[1-9][0-9]{0,19}", context[k])
        for k in ("run_id", "run_attempt")
    ):
        raise ValueError("Invalid run identity")


def _safe(path: Path) -> Path:
    path = path.absolute()
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("Symlink paths are not coverage artifacts")
    return path


def _read(path: Path, limit: int = _LIMIT) -> bytes:
    path = _safe(path)
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError("Missing, nonregular or oversized artifact")
    with path.open("rb") as stream:
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise ValueError("Oversized artifact")
    return value


def _report(report: Path, checkout: Path, started_at: int) -> tuple[bytes, dict, int]:
    data = _read(report)
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data or check_coverage(report):
        raise ValueError("Coverage gate rejected report")
    root = ElementTree.fromstring(data)
    stamp = root.get("timestamp", "")
    if not stamp.isascii() or not stamp.isdecimal():
        raise ValueError("Missing report timestamp")
    timestamp = int(stamp)
    if (
        type(started_at) is not int
        or not 0 < started_at * 1000 <= timestamp <= int(time.time() * 1000) + 5000
    ):
        raise ValueError("Stale or future report")
    if [s.text for s in root.findall("sources/source")] != [str(checkout)]:
        raise ValueError("Foreign report source")
    classes = root.findall("packages/package/classes/class")
    if not classes:
        raise ValueError("Missing package coverage")
    for item in classes:
        name = item.get("filename", "")
        path = PurePosixPath(name)
        if (
            not name.startswith("src/dj_hyperview/")
            or path.suffix != ".py"
            or ".." in path.parts
            or str(path) != name
            or "\\" in name
        ):
            raise ValueError("Foreign covered path")
    counters = {
        f"{d}-{k}": int(root.get(f"{d}-{k}"))
        for d in ("lines", "branches")
        for k in ("covered", "valid")
    }
    return data, counters, timestamp


def seal(
    report: Path,
    directory: Path,
    *,
    context: dict[str, str],
    profile: str,
    started_at: int,
    checkout: Path,
) -> None:
    """Create a new two-file artifact only after the independent gate succeeds."""
    _identity(context)
    if profile not in ("default", "redis"):
        raise ValueError("Unknown coverage profile")
    checkout = _safe(checkout)
    directory = _safe(directory)
    if directory.exists():
        raise ValueError("Artifact already exists")
    data, counters, timestamp = _report(report, checkout, started_at)
    python = platform.python_version()
    django = metadata.version("Django")
    if not python.startswith("3.12.") or django != "6.1.1":
        raise ValueError("Not the canonical coverage runtime")
    manifest = dict(
        context,
        schema=1,
        profile=profile,
        python=python,
        django=django,
        checkout=str(checkout),
        started_at=started_at,
        timestamp=timestamp,
        sha256=hashlib.sha256(data).hexdigest(),
        counters=counters,
    )
    directory.mkdir(parents=True)
    (directory / "coverage.xml").write_bytes(data)
    (directory / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True) + "\n"
    )


def _unique(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate manifest key")
        result[key] = value
    return result


def verify(
    directory: Path,
    *,
    context: dict[str, str],
    profiles: tuple[str, ...],
    checkout: Path,
) -> dict[str, str]:
    """Return explicit files and a unique upload name for the selected run."""
    _identity(context)
    if profiles not in (("default",), ("default", "redis")):
        raise ValueError("Invalid profile selection")
    directory, checkout = _safe(directory), _safe(checkout)
    if not directory.is_dir() or {p.name for p in directory.iterdir()} != set(profiles):
        raise ValueError("Incomplete or extra profile artifacts")
    files, digests = [], []
    for profile in profiles:
        folder = _safe(directory / profile)
        if not folder.is_dir() or {p.name for p in folder.iterdir()} != {
            "coverage.xml",
            "manifest.json",
        }:
            raise ValueError("Incomplete or extra artifact files")
        raw = _read(folder / "manifest.json", 16384)
        manifest = json.loads(raw, object_pairs_hook=_unique)
        if not isinstance(manifest, dict) or set(manifest) != _FIELDS:
            raise ValueError("Invalid manifest schema")
        expected = dict(context, schema=1, profile=profile, checkout=str(checkout))
        if any(
            type(manifest[k]) is not type(v) or manifest[k] != v
            for k, v in expected.items()
        ):
            raise ValueError("Report belongs to another job")
        if (
            not isinstance(manifest["python"], str)
            or not re.fullmatch(r"3\.12\.[0-9]+", manifest["python"])
            or manifest["django"] != "6.1.1"
        ):
            raise ValueError("Unexpected producer runtime")
        report = folder / "coverage.xml"
        data, counters, timestamp = _report(report, checkout, manifest["started_at"])
        if (
            not isinstance(manifest["counters"], dict)
            or any(type(v) is not int for v in manifest["counters"].values())
            or type(manifest["timestamp"]) is not int
        ):
            raise ValueError("Manifest counters must be integers")
        if (
            manifest["sha256"] != hashlib.sha256(data).hexdigest()
            or manifest["counters"] != counters
            or manifest["timestamp"] != timestamp
        ):
            raise ValueError("Report bytes or counters changed")
        files.append(str(report))
        digests.append(hashlib.sha256(raw).hexdigest())
    if any("," in f or "\n" in f or "\r" in f for f in files):
        raise ValueError("Unsafe upload filename")
    suffix = hashlib.sha256("".join(digests).encode()).hexdigest()[:16]
    name = (
        f"coverage-{context['sha']}-{context['run_id']}-{context['run_attempt']}-"
        f"{'-'.join(profiles)}-{suffix}"
    )
    return {"files": ",".join(files), "name": name}


def checkout_sha() -> str:
    """Read the checkout identity rather than trusting only CI variables."""
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def main(argv: list[str] | None = None) -> int:
    """Use fixed workspace paths and independently supplied GitHub identity."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("seal", "verify"))
    parser.add_argument("--profile", choices=("default", "redis"))
    args = parser.parse_args(argv)
    try:
        context = {key: os.environ["GITHUB_" + key.upper()] for key in _CONTEXT}
        _identity(context)
        if checkout_sha() != context["sha"]:
            raise ValueError("Checkout does not match job SHA")
        checkout = Path.cwd()
        root = checkout / "coverage-reports"
        if args.command == "seal":
            seal(
                checkout / "coverage.xml",
                root / str(args.profile),
                context=context,
                profile=args.profile,
                started_at=int(os.environ["COVERAGE_STARTED_AT"]),
                checkout=checkout,
            )
        else:
            selected = os.environ["COVERAGE_REDIS"]
            if selected not in ("true", "false"):
                raise ValueError("Missing explicit profile selection")
            profiles = ("default", "redis") if selected == "true" else ("default",)
            result = verify(root, context=context, profiles=profiles, checkout=checkout)
            with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
                for key, value in result.items():
                    output.write(f"{key}={value}\n")
    except (
        OSError,
        ValueError,
        KeyError,
        ElementTree.ParseError,
        subprocess.SubprocessError,
    ):
        print("Coverage artifact validation failed.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
