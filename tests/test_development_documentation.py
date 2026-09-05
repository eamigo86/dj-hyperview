"""Development-plan documentation contract tests."""

from pathlib import Path

ROOT = Path(__file__).parents[1]
DEVELOPMENT = ROOT / "docs" / "development"
CLOSING_COMMIT = "077589b813f20947d2ff350a0af42a6ea1d39307"


def test_project_summaries_report_the_verified_mvp() -> None:
    """Human-facing summaries start from the verified package outcome."""
    readme = (DEVELOPMENT / "README.md").read_text()
    status = (DEVELOPMENT / "status.md").read_text()
    roadmap = (DEVELOPMENT / "roadmap.md").read_text()
    combined = "\n".join((readme, status, roadmap))

    assert "Tasks 1–13 verificadas" in readme
    assert CLOSING_COMMIT in status
    assert "| 13 |" in roadmap and "| **Verificado** |" in roadmap
    assert "Hito D — Publicación" in roadmap
    assert "**Completado y verificado:** Task 13" in roadmap
    for stale in (
        "Task 13 pendiente",
        "Task 13 sigue pendiente",
        "Task 13 permanece pendiente",
        "Próxima acción | Task 13.1",
    ):
        assert stale not in combined


def test_status_separates_verified_code_from_external_setup() -> None:
    """Status distinguishes local evidence from hosted release prerequisites."""
    status = (DEVELOPMENT / "status.md").read_text()

    assert "PyPI Trusted Publisher" in status
    assert "GitHub Pages" in status
    assert "configuración externa" in status.lower()
    assert 'quoted "on"' in status
    assert "no se ejecutaron builds locales" in status.lower()
    assert "no se publicó" in status.lower()


def test_task13_ledger_records_each_verified_subtask() -> None:
    """Task 13 retains goals, outcomes, problems, and evidence."""
    tasks = (DEVELOPMENT / "tasks.md").read_text()
    assert "Tasks 1–13 están completas y verificadas" in tasks
    assert "| 13 | **Verificado** |" in tasks
    assert "Tasks 1–12 están completas" not in tasks
    assert "Task 13 permanece pendiente" not in tasks
    task13 = tasks.split("## Task 13 —", 1)[1].split("## Post-MVP", 1)[0]

    assert "**Estado:** Verificado" in task13
    for subtask in ("13.1", "13.2", "13.3", "13.4", "13.5", "13.6"):
        assert f"### {subtask} " in task13
    for field in ("**Meta:**", "**Hecho:**", "**Problemas:", "**Evidencia:"):
        assert task13.count(field) >= 6
    for commit in (
        "35e7252",
        "5396477",
        "70833ba",
        "9843e0f",
        "40ee52b",
        "077589b",
    ):
        assert commit in task13
    assert "**Estado:** Pendiente" not in task13
    assert "quoted" in task13 and '"on"' in task13


def test_decisions_and_public_configuration_match_the_release() -> None:
    """Decisions and public links describe the completed release design."""
    decisions = (DEVELOPMENT / "decisions.md").read_text()
    configuration = (ROOT / "docs" / "configuration.md").read_text()

    for decision in (
        "zensical.yml",
        "contrato YAML semántico",
        "build-once",
        "Trusted Publishing",
        "PyPI",
        "Pages",
    ):
        assert decision in decisions
    assert "Antes de Task 13.6" not in decisions
    assert "belong to Task 13.3" not in configuration
    for guide in (
        "filesystem.md",
        "database-admin.md",
        "cache-consistency.md",
        "security.md",
        "testing.md",
        "release-rollback.md",
    ):
        assert f"]({guide})" in configuration
