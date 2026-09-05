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
