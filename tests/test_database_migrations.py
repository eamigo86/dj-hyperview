from tests.test_database_app import ROOT, run_isolated


def test_database_migration_applies_and_reverses(tmp_path):
    database = tmp_path / "database.sqlite3"
    result = run_isolated(
        "tests.settings_database",
        "import django; django.setup(); "
        "from django.core.management import call_command; "
        "from django.db import connection; "
        "call_command('migrate','dj_hyperview_database',"
        "verbosity=0,interactive=False); "
        "table='dj_hyperview_database_hyperviewtemplate'; "
        "print(table in connection.introspection.table_names()); "
        "call_command('migrate','dj_hyperview_database','zero',"
        "verbosity=0,interactive=False); "
        "print(table in connection.introspection.table_names())",
        database=database,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["True", "False"]


def test_migration_matches_current_model_state(tmp_path):
    result = run_isolated(
        "tests.settings_database",
        "import django; django.setup(); "
        "from django.core.management import call_command; "
        "call_command('makemigrations','dj_hyperview_database',"
        "verbosity=1,interactive=False,check=True,dry_run=True)",
        database=tmp_path / "state.sqlite3",
    )

    assert result.returncode == 0, result.stderr
    assert "No changes detected" in result.stdout


def test_package_contains_python_migration_without_runtime_markup():
    migration = ROOT / "src/dj_hyperview/contrib/database/migrations/0001_initial.py"

    assert migration.is_file()
    assert (migration.parent / "0002_field_validators.py").is_file()
    assert not list((ROOT / "src/dj_hyperview/contrib").rglob("*.xml"))
    assert not list((ROOT / "src/dj_hyperview/contrib").rglob("*.hxml"))
