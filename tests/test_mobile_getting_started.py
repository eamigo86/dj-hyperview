"""The mobile getting-started guide keeps the steps that were run end to end."""

from pathlib import Path

DOCS = Path(__file__).parents[1] / "docs"
GUIDE = DOCS / "mobile-getting-started.md"


def test_guide_creates_the_app_from_the_sdk_57_template() -> None:
    """The create command pins the blank TypeScript template to Expo SDK 57.

    The unpinned template now creates an SDK 58 project, which the SDK 57 matrix
    would only partly downgrade.
    """
    guide = GUIDE.read_text(encoding="utf-8")

    assert "--template expo-template-blank-typescript@sdk-57" in guide
    assert "--template blank-typescript " not in guide


def test_guide_installs_the_type_packages_hyperview_sources_need() -> None:
    """The guide adds the type packages that Hyperview's TypeScript sources import."""
    guide = GUIDE.read_text(encoding="utf-8")

    assert (
        "corepack yarn add -D @types/lodash@^4.17.20 @types/url-parse@^1.4.11" in guide
    )


def test_guide_entrypoint_is_the_quickstart_screen() -> None:
    """The shell loads the screen that the quickstart serves."""
    guide = GUIDE.read_text(encoding="utf-8")
    quickstart = (DOCS / "quickstart.md").read_text(encoding="utf-8")

    assert "EXPO_PUBLIC_API_URL=http://127.0.0.1:8000/hyperview/home/" in guide
    assert '"hyperview/home/"' in quickstart


def test_guide_shell_keeps_the_screen_inside_the_safe_area() -> None:
    """The shell wraps Hyperview in a SafeAreaView so the status bar cannot cover it."""
    guide = GUIDE.read_text(encoding="utf-8")

    assert "<SafeAreaView style={{ flex: 1 }}>" in guide
