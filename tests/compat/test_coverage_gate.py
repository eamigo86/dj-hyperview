"""Separate exact line and branch thresholds for canonical coverage."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from tools import test_matrix


def write_report(path: Path, **updates: str) -> None:
    """Write a small report with explicit integer coverage counters."""
    attributes = {
        "lines-covered": "100",
        "lines-valid": "100",
        "branches-covered": "100",
        "branches-valid": "100",
    }
    attributes.update(updates)
    serialized = " ".join(f'{name}="{value}"' for name, value in attributes.items())
    path.write_text(f"<coverage {serialized} />", encoding="utf-8")


@pytest.mark.parametrize(
    ("counts", "expected"),
    [
        ((94, 100, 1_000, 1_000), 1),
        ((1_000, 1_000, 94, 100), 1),
        ((95, 100, 95, 100), 0),
        ((94_999, 100_000, 100, 100), 1),
        ((100, 100, 94_999, 100_000), 1),
        ((100, 100, 100, 100), 0),
    ],
)
def test_each_coverage_dimension_has_an_exact_independent_threshold(
    tmp_path: Path, counts: tuple[int, int, int, int], expected: int, capsys
) -> None:
    """A passing combined or rounded percentage cannot conceal either deficit."""
    report = tmp_path / "coverage.xml"
    write_report(
        report,
        **dict(
            zip(
                ("lines-covered", "lines-valid", "branches-covered", "branches-valid"),
                map(str, counts),
                strict=True,
            )
        ),
    )

    assert test_matrix.check_coverage(report) == expected
    output = capsys.readouterr()
    assert "lines" in output.out + output.err
    assert "branches" in output.out + output.err


@pytest.mark.parametrize("counter", ["lines-covered", "branches-covered"])
@pytest.mark.parametrize("value", ["NaN", "inf", "-1", "95.0", "", "101"])
def test_invalid_coverage_counters_fail_closed(
    tmp_path: Path, counter: str, value: str, capsys
) -> None:
    """Counters must be nonnegative integers no greater than their totals."""
    report = tmp_path / "coverage.xml"
    write_report(report, **{counter: value})

    assert test_matrix.check_coverage(report) == 2
    assert "Invalid coverage report" in capsys.readouterr().err


@pytest.mark.parametrize("counter", ["lines-valid", "branches-valid"])
@pytest.mark.parametrize("value", ["0", "-1", "nan", "Infinity", "100.0"])
def test_invalid_or_empty_coverage_totals_fail_closed(
    tmp_path: Path, counter: str, value: str
) -> None:
    """Neither dimension may claim success without measured executable code."""
    report = tmp_path / "coverage.xml"
    write_report(report, **{counter: value})

    assert test_matrix.check_coverage(report) == 2


@pytest.mark.parametrize(
    "content", [None, "<coverage", "<coverage />", '<not-coverage lines-valid="100" />']
)
def test_missing_malformed_or_incomplete_reports_fail_closed(
    tmp_path: Path, content: str | None
) -> None:
    """Missing files, wrong roots and missing counters are not passing reports."""
    report = tmp_path / "coverage.xml"
    if content is not None:
        report.write_text(content, encoding="utf-8")

    assert test_matrix.check_coverage(report) == 2


@pytest.mark.parametrize("codes", [[7], [0, 9]])
def test_independent_gate_never_runs_after_a_failed_phase(
    tmp_path: Path, monkeypatch, codes: list[int]
) -> None:
    """A stale valid report cannot replace either successful test phase."""
    monkeypatch.chdir(tmp_path)
    write_report(tmp_path / "coverage.xml")
    remaining = iter(codes)
    monkeypatch.setattr(
        test_matrix.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=next(remaining)),
    )
    monkeypatch.setattr(
        test_matrix,
        "check_coverage",
        lambda report: pytest.fail("failed coverage phase reached independent gate"),
    )

    assert test_matrix.run_coverage(redis=False) == codes[-1]
    assert not (tmp_path / "coverage.xml").exists()


@pytest.mark.parametrize("create_report", [True, False])
def test_runner_checks_only_the_report_written_after_both_successful_phases(
    tmp_path: Path, monkeypatch, create_report: bool
) -> None:
    """Successful process exits without a fresh report cannot reuse old coverage."""
    monkeypatch.chdir(tmp_path)
    report = tmp_path / "coverage.xml"
    write_report(report)
    calls = []

    def run(command, *, check, env):
        calls.append(command)
        assert not report.exists()
        if len(calls) == 2 and create_report:
            write_report(report, **{"branches-covered": "94"})
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(test_matrix.subprocess, "run", run)

    assert test_matrix.run_coverage(redis=False) == (1 if create_report else 2)
    assert calls == list(test_matrix.coverage_commands())


def test_report_reset_failure_stops_before_executing_tests(tmp_path: Path, monkeypatch):
    """An unwritable report destination cannot permit stale report acceptance."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "coverage.xml").mkdir()
    monkeypatch.setattr(
        test_matrix.subprocess,
        "run",
        lambda *a, **kw: pytest.fail("invalid report destination started tests"),
    )

    assert test_matrix.run_coverage(redis=False) == 2


@pytest.mark.parametrize("dimension", ["lines", "branches"])
def test_successful_phases_do_not_accept_a_failing_individual_dimension(
    tmp_path: Path, monkeypatch, dimension: str
) -> None:
    """Reproduce the audit: the combined gate passes while one dimension fails."""
    monkeypatch.chdir(tmp_path)
    calls = []

    def run(command, *, check, env):
        calls.append(command)
        if len(calls) == 2:
            other = "branches" if dimension == "lines" else "lines"
            write_report(
                tmp_path / "coverage.xml",
                **{
                    f"{dimension}-covered": "94",
                    f"{other}-covered": "1000",
                    f"{other}-valid": "1000",
                },
            )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(test_matrix.subprocess, "run", run)

    assert test_matrix.run_coverage(redis=False) == 1
