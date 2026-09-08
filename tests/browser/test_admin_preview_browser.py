"""Real isolated Chromium acceptance for the unsaved Admin preview workspace."""

import os
from pathlib import Path

import pytest
from django.conf import settings
from django.urls import reverse

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("DJHV_TEST_BROWSER") != "1",
        reason="Opt in with DJHV_TEST_BROWSER=1 and tests.settings_browser",
    ),
    pytest.mark.django_db(transaction=True),
]

NS = "https://hyperview.org/hyperview"
EDITOR = "document.querySelector('#id_content').previousElementSibling.editor"
TASKS = (
    f'<view xmlns="{NS}">\n'
    "  {% for task in tasks %}<text>{{ task.title }}</text>"
    "{% empty %}<text>No tasks</text>{% endfor %}\n</view>"
)


@pytest.fixture
def admin_page(request, admin_client, admin_user, live_server):
    from playwright.sync_api import sync_playwright

    destination = reverse("admin:dj_hyperview_database_hyperviewtemplate_add")
    readonly = getattr(request, "param", None) == "readonly"
    if readonly:
        from django.contrib.auth.models import Permission

        from dj_hyperview.contrib.database.models import HyperviewTemplate

        admin_user.is_superuser = False
        admin_user.save(update_fields=["is_superuser"])
        admin_user.user_permissions.add(
            Permission.objects.get(codename="view_hyperviewtemplate")
        )
        obj = HyperviewTemplate.objects.create(name="readonly.xml", content=TASKS)
        destination = reverse(
            "admin:dj_hyperview_database_hyperviewtemplate_change", args=[obj.pk]
        )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1500, "height": 1050})
        context.add_cookies(
            [
                {
                    "name": settings.SESSION_COOKIE_NAME,
                    "value": admin_client.cookies[settings.SESSION_COOKIE_NAME].value,
                    "url": live_server.url,
                }
            ]
        )
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(live_server.url + destination)
        if not readonly:
            page.wait_for_function(
                "document.querySelector('.djhv-preview-workspace')"
                "?.dataset.previewInstalled === 'true'",
                timeout=10000,
            )
        yield page, live_server.url, errors
        context.close()
        browser.close()


def set_draft(page, content=TASKS):
    page.locator("#id_name").fill("screens/draft.xml")
    page.evaluate(f"content => {EDITOR}.setValue(content, -1)", content)


def refresh(page, *, state="valid"):
    with page.expect_response(
        lambda response: "/hxml-preview/" in response.url
    ) as response:
        page.get_by_role("button", name="Preview", exact=True).click()
    assert response.value.status == 200
    assert response.value.request.headers["x-csrftoken"]
    page.locator(f'.djhv-preview-workspace[data-preview-state="{state}"]').wait_for()
    return response.value.json()


def snapshot(page):
    return page.evaluate(f"""() => {{
        const editor = {EDITOR};
        return {{content: editor.getValue(), selection: editor.getSelectionRange(),
            revision: editor.session.getUndoManager().getRevision(),
            undo: editor.session.getUndoManager().hasUndo(),
            redo: editor.session.getUndoManager().hasRedo()}};
    }}""")


def test_actual_roundtrip_preserves_ace_buffer_selection_undo_and_scenarios(admin_page):
    page, _, errors = admin_page
    set_draft(page)
    page.evaluate(
        f"{EDITOR}.selection.setSelectionRange({{start:{{row:1,column:2}},end:{{row:1,column:12}}}})"
    )
    before = snapshot(page)
    result = refresh(page)
    assert result["ok"] is True and "Review A &amp; B" in result["hxml"]
    assert snapshot(page) == before
    frame = page.frame_locator(".djhv-preview-frame iframe")
    assert frame.get_by_text("Review A & B", exact=True).is_visible()
    page.locator(".djhv-preview-scenario").select_option("empty_list")
    page.locator('.djhv-preview-workspace[data-preview-state="stale"]').wait_for()
    refresh(page)
    assert frame.get_by_text("No tasks", exact=True).is_visible()
    assert snapshot(page) == before
    assert errors == []


def test_source_and_rendered_errors_use_distinct_readonly_coordinates(admin_page):
    page, _, _ = admin_page
    set_draft(page, "<view>\n{% impossible_tag %}\n</view>")
    result = refresh(page, state="error")
    assert result["diagnostics"][0]["coordinate_space"] == "source"
    page.get_by_role("button", name="Go to source").click()
    assert page.evaluate(f"{EDITOR}.getCursorPosition().row") == 1
    assert page.locator(".djhv-preview-frame iframe").count() == 0
    set_draft(page, "<unknown/>")
    result = refresh(page, state="error")
    assert result["hxml"] == "<unknown/>"
    assert page.locator(".djhv-preview-output").text_content() == "<unknown/>"
    assert page.get_by_role("button", name="Go to source").count() == 0
    assert page.locator(".djhv-preview-frame iframe").count() == 0
    page.get_by_role("button", name="Go to rendered HXML").click()
    assert page.locator(".djhv-preview-output").is_visible()


def test_preview_iframe_is_inert_and_never_loads_embedded_resources(admin_page):
    page, origin, errors = admin_page
    attempted = []

    def protect(route):
        if not route.request.url.startswith(origin):
            attempted.append(route.request.url)
            route.abort()
        else:
            route.continue_()

    page.route("**/*", protect)
    content = (
        f'<view xmlns="{NS}"><image style="placeholder" '
        'source="https://must-not-load.invalid/image"/>'
        "<text>&lt;script&gt;parent.PREVIEW_ATTACK=true&lt;/script&gt;</text>"
        '<behavior trigger="press" action="push" href="https://must-not-load.invalid/action"/>'
        "</view>"
    )
    set_draft(page, content)
    refresh(page, state="warning")
    iframe = page.locator(".djhv-preview-frame iframe")
    assert iframe.get_attribute("sandbox") == ""
    assert iframe.get_attribute("referrerpolicy") == "no-referrer"
    frame = page.frame_locator(".djhv-preview-frame iframe")
    assert frame.locator("img, video, audio, script, form, a[href]").count() == 0
    assert "parent.PREVIEW_ATTACK" in frame.locator("body").inner_text()
    assert page.evaluate("window.PREVIEW_ATTACK") is None
    assert attempted == [] and errors == []


def test_edit_during_request_discards_old_response_and_marks_stale(admin_page):
    page, _, _ = admin_page
    set_draft(page)
    page.evaluate("""() => {
        const original = window.fetch;
        window.fetch = (url, options) => String(url).includes('hxml-preview')
            ? new Promise(resolve => {window.releasePreview = () => resolve(
                new Response(JSON.stringify({ok:true,hxml:'<view/>',diagnostics:[]}),
                    {headers:{'Content-Type':'application/json'}}));})
            : original(url, options);
    }""")
    page.get_by_role("button", name="Preview", exact=True).click()
    page.locator('.djhv-preview-workspace[data-preview-state="loading"]').wait_for()
    page.locator("#id_name").fill("renamed.xml")
    page.evaluate("window.releasePreview()")
    page.locator('.djhv-preview-workspace[data-preview-state="stale"]').wait_for()
    assert page.locator(".djhv-preview-frame iframe").count() == 0
    assert page.locator(".djhv-preview-output").text_content() == ""


def test_workspace_is_responsive_and_supports_admin_dark_theme(admin_page):
    page, _, errors = admin_page
    set_draft(page)
    refresh(page)
    source = page.locator(".djhv-preview-source").bounding_box()
    panel = page.locator(".djhv-preview-panel").bounding_box()
    assert panel["x"] > source["x"] and abs(panel["y"] - source["y"]) < 3
    assert source["width"] >= 400, "The editor must retain usable desktop width"
    page.evaluate("document.documentElement.dataset.theme = 'dark'")
    page.wait_for_function(f"{EDITOR}.getTheme() === 'ace/theme/monokai'")
    screenshot = os.environ.get("DJHV_BROWSER_SCREENSHOT")
    if screenshot:
        page.screenshot(path=str(Path(screenshot)), full_page=True)
    page.set_viewport_size({"width": 800, "height": 1100})
    source = page.locator(".djhv-preview-source").bounding_box()
    panel = page.locator(".djhv-preview-panel").bounding_box()
    assert panel["y"] > source["y"] + source["height"]
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert errors == []


@pytest.mark.parametrize("admin_page", ["readonly"], indirect=True)
def test_readonly_admin_has_no_editor_or_preview_controls(admin_page):
    page, _, errors = admin_page
    assert page.locator(".field-content .readonly").is_visible()
    assert page.locator(".ace_editor, .djhv-preview-workspace").count() == 0
    assert page.get_by_role("button", name="Preview", exact=True).count() == 0
    assert errors == []


def test_disabled_editor_and_preview_reload_without_broken_controls(
    admin_page, settings
):
    page, _, errors = admin_page
    settings.HYPERVIEW = {}
    page.reload()
    assert page.locator("#id_content").is_visible()
    assert page.locator(".ace_editor, .djhv-preview-workspace").count() == 0
    assert errors == []


def test_preview_button_disables_only_while_request_is_pending(admin_page):
    page, _, _ = admin_page
    set_draft(page)
    page.evaluate("""() => {
        const original = window.fetch;
        window.fetch = (url, options) => String(url).includes('hxml-preview')
            ? new Promise(resolve => {window.releasePreview = () => resolve(
                new Response(JSON.stringify({ok:true,hxml:'<view/>',diagnostics:[]}),
                    {headers:{'Content-Type':'application/json'}}));})
            : original(url, options);
    }""")
    button = page.get_by_role("button", name="Preview", exact=True)
    button.click()
    page.locator('.djhv-preview-workspace[data-preview-state="loading"]').wait_for()
    assert button.is_disabled()
    page.locator("#id_name").fill("renamed.xml")
    page.locator('.djhv-preview-workspace[data-preview-state="stale"]').wait_for()
    assert button.is_enabled()
    page.evaluate("window.releasePreview()")


def test_failed_response_removes_previous_screen_selector(admin_page):
    page, _, _ = admin_page
    set_draft(page)
    screens = (
        f'<doc xmlns="{NS}"><screen id="A"><body><text>A</text></body></screen>'
        '<screen id="B"><body><text>B</text></body></screen></doc>'
    )
    result = {"ok": True, "hxml": screens, "diagnostics": []}
    page.route("**/hxml-preview/**", lambda route: route.fulfill(json=result))
    refresh(page)
    assert page.locator(".djhv-preview-screen-label").is_visible()
    assert page.locator(".djhv-preview-screen option").count() == 2
    result.update(
        {
            "ok": False,
            "hxml": None,
            "diagnostics": [
                {
                    "severity": "error",
                    "code": "schema",
                    "message": "Invalid schema output",
                    "template": "screens/draft.xml",
                    "coordinate_space": "rendered",
                    "line": None,
                    "column": None,
                }
            ],
        }
    )
    refresh(page, state="error")
    assert page.locator(".djhv-preview-screen-label").is_hidden()
    assert page.locator(".djhv-preview-screen option").count() == 0


def test_switching_screens_updates_renderer_warning_state(admin_page):
    page, _, _ = admin_page
    set_draft(page)
    screens = (
        f'<doc xmlns="{NS}"><screen id="Clean"><body><text>Clean</text></body></screen>'
        '<screen id="Media"><body><image source="ignored.png"/></body></screen></doc>'
    )
    page.route(
        "**/hxml-preview/**",
        lambda route: route.fulfill(
            json={"ok": True, "hxml": screens, "diagnostics": []}
        ),
    )
    refresh(page)
    page.locator(".djhv-preview-screen").select_option("1")
    page.locator('.djhv-preview-workspace[data-preview-state="warning"]').wait_for()
    assert page.get_by_text("Preview with warnings", exact=True).is_visible()
