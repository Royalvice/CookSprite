"""Cross-browser smoke test against a real CookSprite API + ComfyUI runtime."""

from __future__ import annotations

import json
import os
from pathlib import Path

from playwright.sync_api import BrowserType, expect, sync_playwright

BASE = os.environ.get("COOKSPRITE_WEB_URL", "http://127.0.0.1:15173").rstrip("/")
RESULT = Path(
    os.environ.get(
        "COOKSPRITE_REAL_RESULT",
        "web/test-results/h20-workflow-20260814/api-real-acceptance.json",
    )
)
EXPECTED_RUNTIME = os.environ.get("COOKSPRITE_EXPECTED_RUNTIME", "h20-gpu0-workflow")
SHOTS = Path(
    os.environ.get(
        "COOKSPRITE_CROSS_BROWSER_SHOTS",
        "web/test-results/h20-workflow-20260814/cross-browser",
    )
)


def check(browser_type: BrowserType, name: str, project_id: str, sequence_id: str) -> None:
    browser = browser_type.launch(headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    errors: list[str] = []
    page.on(
        "console",
        lambda message: (
            errors.append(f"console:{message.type}:{message.text}")
            if message.type == "error"
            else None
        ),
    )
    page.on("pageerror", lambda error: errors.append(f"pageerror:{error}"))
    page.on(
        "requestfailed",
        lambda request: (
            errors.append(f"requestfailed:{request.url}:{request.failure}")
            if not any(token in str(request.failure).upper() for token in ("ABORT", "CANCEL"))
            else None
        ),
    )
    page.on(
        "response",
        lambda response: (
            errors.append(f"http:{response.status}:{response.url}")
            if response.status >= 400 and "/hdri/" not in response.url
            else None
        ),
    )

    page.goto(f"{BASE}/studio/{project_id}", wait_until="commit")
    page.locator(".studio-view").wait_for(timeout=20_000)
    page.wait_for_timeout(1_000)
    expect(page.locator(".topbar .runtime-chip.ready")).to_be_visible(timeout=15_000)
    expect(page.locator(".runtime-warning")).to_have_count(0)

    page.locator(".stage-rail > button").nth(1).click()
    expect(page.locator(".animation-generator")).to_be_visible()
    expect(page.locator(".frame-studio")).to_be_visible()
    page.locator(".action-grid button").nth(2).hover()
    expect(page.locator(".hover-example")).to_be_visible()
    expect(page.locator(".hover-example [data-artifact-kind=FrameSeq]")).to_be_visible()

    page.goto(
        f"{BASE}/studio/{project_id}?artifact={sequence_id}&intent=normal",
        wait_until="commit",
    )
    page.locator(".normal-workspace").wait_for(timeout=20_000)
    page.wait_for_timeout(1_000)
    expect(page.locator(".hdri-strip button")).to_have_count(3)
    expect(page.locator("canvas[data-lighting-canvas=true]")).to_have_count(1)
    page.screenshot(path=SHOTS / f"09-{name}-real-runtime.png", full_page=True)

    geometry = page.evaluate(
        "() => ({body: document.body.scrollWidth, viewport: document.documentElement.clientWidth})"
    )
    assert geometry["body"] <= geometry["viewport"] + 1, geometry
    assert not errors, json.dumps(errors, ensure_ascii=False, indent=2)
    browser.close()


def run() -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        request = playwright.request.new_context()
        health = request.get(f"{BASE}/api/v1/health").json()
        assert health["runtime"] == "ready", health
        assert health["runtime_id"] == EXPECTED_RUNTIME, health
        acceptance = json.loads(RESULT.read_text(encoding="utf-8"))
        request.dispose()

        check(
            playwright.firefox,
            "firefox",
            acceptance["project_id"],
            acceptance["frame_sequence"],
        )
        check(
            playwright.webkit,
            "webkit",
            acceptance["project_id"],
            acceptance["frame_sequence"],
        )


if __name__ == "__main__":
    run()
