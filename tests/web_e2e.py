from __future__ import annotations

import os
from pathlib import Path

import httpx
from playwright.sync_api import BrowserType, Page, expect, sync_playwright

BASE = "http://127.0.0.1:5173"
API = "http://127.0.0.1:8000/api/v1"
SCREENSHOTS = Path(os.environ.get("COOKSPRITE_E2E_SHOTS", "/tmp/cooksprite-e2e"))
ROOT = Path(__file__).parents[1]


def seed_runtime() -> None:
    with httpx.Client(timeout=10) as client:
        response = client.post(
            f"{API}/runtimes",
            json={"id": "rt_demo", "label": "Demo Runtime", "base_url": "http://127.0.0.1:8188"},
        )
        response.raise_for_status()
        client.post(f"{API}/runtimes/rt_demo/doctor").raise_for_status()


def wait_for_cards(page: Page, minimum: int) -> None:
    page.wait_for_function(
        "minimum => document.querySelectorAll('.artifact-strip .artifact-card').length >= minimum",
        arg=minimum,
        timeout=15_000,
    )


def assert_accessible_buttons(page: Page) -> None:
    missing = page.locator("button").evaluate_all(
        "buttons => buttons.filter(b => !(b.innerText || b.getAttribute('aria-label') || b.getAttribute('title'))).map(b => b.outerHTML.slice(0,120))"
    )
    assert not missing, f"buttons without accessible names: {missing}"


def full_chromium_flow(browser_type: BrowserType) -> None:
    browser = browser_type.launch(headless=True)
    context = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
    page = context.new_page()
    console_errors: list[str] = []
    page.on(
        "console",
        lambda message: (
            (console_errors.append(message.text), print(f"BROWSER_CONSOLE_ERROR {message.text}"))
            if message.type == "error"
            else None
        ),
    )
    page.on("pageerror", lambda error: print(f"BROWSER_PAGE_ERROR {error}"))
    page.goto(BASE)
    page.evaluate("localStorage.setItem('cooksprite.language','zh-CN')")
    page.reload()
    page.wait_for_load_state("networkidle")
    expect(page.locator(".gallery-empty")).to_be_visible()
    assert_accessible_buttons(page)
    page.screenshot(path=SCREENSHOTS / "gallery-empty-neon-zh.png", full_page=True)

    page.locator('a[href="/studio"]').first.click()
    page.wait_for_load_state("networkidle")
    expect(page.locator(".creation-deck")).to_be_visible()
    expect(page.locator(".runtime-chip.ready")).to_be_visible()
    file_input = page.locator('.artifact-input-panel input[type="file"]').first
    file_input.set_input_files(ROOT / "cooksprite/example_assets/actor.svg")
    wait_for_cards(page, 1)
    page.locator("#prompt").fill("an adventurous soup knight with a copper ladle")
    page.locator(".draw-button").click()
    wait_for_cards(page, 5)
    page.screenshot(path=SCREENSHOTS / "studio-image-1440-neon-zh.png", full_page=True)

    page.locator(".creation-mode-tabs button").nth(1).click()
    source = page.locator(".artifact-strip .artifact-card").first
    target = page.locator(".artifact-input-panel .drop-target").first
    source.drag_to(target)
    expect(page.locator(".draw-button")).to_be_enabled()
    page.locator(".draw-button").click()
    wait_for_cards(page, 13)

    page.locator(".stage-rail > button").nth(2).click()
    expect(page.locator(".frame-studio")).to_be_visible()
    for index in range(4):
        page.locator(".candidate-row .artifact-card").nth(index).click()
    page.locator(".confirm-selection").click()
    expect(page.locator(".timeline-frame")).to_have_count(4)
    page.locator(".timeline-frame > label input").first.fill("160")
    page.keyboard.press("Space")
    page.keyboard.press("Space")
    page.screenshot(path=SCREENSHOTS / "frame-studio-1440-neon-zh.png", full_page=True)

    page.get_by_test_id("redraw-frame").click()
    page.wait_for_function(
        "() => Number(document.querySelector('[data-testid=candidate-row]')?.dataset.candidateCount || 0) >= 17",
        timeout=15_000,
    )
    page.get_by_test_id("import-frame-source").click()
    expect(page.locator(".source-extractor")).to_be_visible()
    page.locator('.source-extractor input[type="file"]').set_input_files(
        ROOT / "cooksprite/example_assets/tile.svg"
    )
    page.get_by_test_id("auto-grid").click()
    expect(page.locator(".extractor-controls input[type='number']").first).not_to_have_value("0")
    page.get_by_test_id("extract-source").click()
    expect(page.locator(".source-extractor")).to_be_hidden(timeout=15_000)
    page.wait_for_function(
        "() => Number(document.querySelector('[data-testid=candidate-row]')?.dataset.candidateCount || 0) >= 33",
        timeout=15_000,
    )
    page.screenshot(path=SCREENSHOTS / "frame-studio-imported-sources.png", full_page=True)

    page.locator(".stage-rail > button").nth(3).click()
    page.locator(".normal-input .arcade-button.primary").click()
    page.wait_for_function(
        "() => document.querySelectorAll('.normal-source-row .artifact-card').length >= 2",
        timeout=15_000,
    )
    page.locator(".hdri-strip button").nth(5).click()
    page.locator(".light-arc-control input").fill("140")
    page.screenshot(path=SCREENSHOTS / "normal-light-1440-neon-zh.png", full_page=True)
    page.locator(".inspector-footer .arcade-button").click()

    page.locator(".stage-rail > button").nth(4).click()
    page.locator(".warning-link").click()
    page.wait_for_function(
        "() => document.querySelectorAll('.package-list article').length >= 1", timeout=15_000
    )
    expect(page.locator(".package-list article")).to_have_count(1)
    page.screenshot(path=SCREENSHOTS / "export-1440-neon-zh.png", full_page=True)

    page.locator('a[href="/"]').first.click()
    page.wait_for_load_state("networkidle")
    expect(page.locator(".arcade-cabinet")).to_have_count(1)
    page.screenshot(path=SCREENSHOTS / "gallery-finished-neon-zh.png", full_page=True)
    assert_accessible_buttons(page)

    page.evaluate("localStorage.setItem('cooksprite.theme','ember')")
    page.reload()
    page.wait_for_load_state("networkidle")
    page.screenshot(path=SCREENSHOTS / "gallery-ember-zh.png", full_page=True)
    page.evaluate("localStorage.setItem('cooksprite.theme','mint')")
    page.reload()
    page.wait_for_load_state("networkidle")
    page.locator(".language-button").click()
    page.screenshot(path=SCREENSHOTS / "gallery-mint-en.png", full_page=True)

    for width in (1024, 768):
        page.set_viewport_size({"width": width, "height": 900})
        page.goto(f"{BASE}/studio")
        page.wait_for_load_state("networkidle")
        expect(page.locator(".studio-view")).to_be_visible()
        assert page.locator(".creation-layout").evaluate(
            "element => element.scrollWidth <= element.clientWidth + 1"
        ), f"creation controls overflow at {width}px"
        page.screenshot(path=SCREENSHOTS / f"studio-{width}-compact.png", full_page=True)
    page.set_viewport_size({"width": 375, "height": 812})
    page.goto(f"{BASE}/studio")
    page.wait_for_load_state("networkidle")
    expect(page.locator(".small-screen-gate")).to_be_visible()
    page.goto(BASE)
    page.wait_for_load_state("networkidle")
    expect(page.locator(".arcade-cabinet")).to_be_visible()
    page.screenshot(path=SCREENSHOTS / "gallery-375-browse.png", full_page=True)

    assert not [error for error in console_errors if "favicon" not in error.lower()], console_errors
    context.close()
    browser.close()


def smoke(browser_type: BrowserType, name: str) -> None:
    browser = browser_type.launch(headless=True)
    page = browser.new_page(viewport={"width": 1024, "height": 820})
    page.goto(BASE)
    page.wait_for_load_state("networkidle")
    expect(page.locator(".topbar")).to_be_visible()
    page.goto(f"{BASE}/studio")
    page.wait_for_load_state("networkidle")
    expect(page.locator(".creation-deck")).to_be_visible()
    assert_accessible_buttons(page)
    page.screenshot(path=SCREENSHOTS / f"{name}-studio-smoke.png", full_page=True)
    browser.close()


def main() -> None:
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    seed_runtime()
    with sync_playwright() as playwright:
        full_chromium_flow(playwright.chromium)
        smoke(playwright.firefox, "firefox")
        smoke(playwright.webkit, "webkit")
    print(f"COOKSPRITE_WEB_E2E_OK {SCREENSHOTS}")


if __name__ == "__main__":
    main()
