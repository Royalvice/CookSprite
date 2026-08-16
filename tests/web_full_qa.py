"""Black-box Playwright acceptance suite for the CookSprite web client.

The suite intentionally exercises the UI through the browser while the API and
ComfyUI boundary are provided by the local deterministic test runtime.
"""

from __future__ import annotations

import json
import os
import re
import zipfile
from collections.abc import Callable
from pathlib import Path

import httpx
from playwright.sync_api import Browser, BrowserContext, Page, expect, sync_playwright

BASE = "http://127.0.0.1:5173"
API = "http://127.0.0.1:8000/api/v1"
ROOT = Path(__file__).parents[1]
SHOTS = Path(os.environ.get("COOKSPRITE_FULL_QA_SHOTS", "/tmp/cooksprite-full-qa/shots"))
REPORT = Path(os.environ.get("COOKSPRITE_FULL_QA_REPORT", "/tmp/cooksprite-full-qa/report.json"))


class QARun:
    def __init__(self) -> None:
        self.failures: list[dict[str, str]] = []
        self.passes: list[str] = []
        self.notes: list[str] = []
        SHOTS.mkdir(parents=True, exist_ok=True)

    def note(self, message: str) -> None:
        self.notes.append(message)
        print(f"NOTE {message}")

    def fail(self, scope: str, error: Exception | str, page: Page | None = None) -> None:
        message = str(error)
        item = {"scope": scope, "error": message}
        if page:
            item["url"] = page.url
            shot = SHOTS / f"failure-{len(self.failures) + 1:02d}.png"
            try:
                page.screenshot(path=shot, full_page=True)
                item["screenshot"] = str(shot)
            except Exception as screenshot_error:  # noqa: BLE001
                item["screenshot_error"] = str(screenshot_error)
        self.failures.append(item)
        print(f"FAIL {scope}: {message}")

    def step(self, scope: str, fn: Callable[[], None], page: Page | None = None) -> None:
        try:
            fn()
            self.passes.append(scope)
            print(f"PASS {scope}")
        except Exception as error:  # noqa: BLE001 - continue to collect the full report.
            self.fail(scope, error, page)

    def write_report(self) -> None:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(
            json.dumps(
                {
                    "passes": self.passes,
                    "failures": self.failures,
                    "notes": self.notes,
                    "screenshots": str(SHOTS),
                },
                ensure_ascii=False,
                indent=2,
            )
        )


def seed_runtime() -> None:
    with httpx.Client(timeout=10) as client:
        response = client.post(
            f"{API}/runtimes",
            json={
                "id": "rt_full_qa",
                "label": "Full QA Runtime",
                "base_url": "http://127.0.0.1:8188",
            },
        )
        response.raise_for_status()
        client.post(f"{API}/runtimes/rt_full_qa/doctor").raise_for_status()


def wait_network(page: Page) -> None:
    page.wait_for_load_state("networkidle")


def wait_candidates(page: Page, count: int) -> None:
    page.wait_for_function(
        "minimum => Number(document.querySelector('[data-testid=candidate-row]')?.dataset.candidateCount || 0) >= minimum",
        arg=count,
        timeout=20_000,
    )


def wait_artifacts(page: Page, count: int) -> None:
    page.wait_for_function(
        "minimum => document.querySelectorAll('.artifact-strip .artifact-card').length >= minimum",
        arg=count,
        timeout=20_000,
    )


def assert_no_page_overflow(page: Page) -> None:
    metrics = page.locator("body").evaluate(
        "e => ({scrollWidth:e.scrollWidth, clientWidth:e.clientWidth, scrollHeight:e.scrollHeight, clientHeight:e.clientHeight})"
    )
    assert metrics["scrollWidth"] <= metrics["clientWidth"] + 1, metrics


def assert_true(value: object, message: str = "assertion failed") -> None:
    assert value, message


def assert_accessibility_basics(page: Page) -> None:
    missing_buttons = page.locator("button").evaluate_all(
        "buttons => buttons.filter(b => !(b.innerText || b.getAttribute('aria-label') || b.getAttribute('title'))).map(b => b.outerHTML.slice(0,180))"
    )
    assert not missing_buttons, missing_buttons
    duplicate_ids = page.locator("[id]").evaluate_all(
        "nodes => { const ids = nodes.map(n => n.id); return [...new Set(ids.filter((id, i) => ids.indexOf(id) !== i))]; }"
    )
    assert not duplicate_ids, duplicate_ids


def assert_visible_geometry(page: Page, selector: str, viewport_width: int) -> None:
    for index in range(page.locator(selector).count()):
        box = page.locator(selector).nth(index).bounding_box()
        assert box and box["width"] > 0 and box["height"] > 0, f"{selector}[{index}] has no box"
        assert box["x"] >= -1, (selector, index, box)
        assert box["x"] + box["width"] <= viewport_width + 1, (selector, index, box)


def capture(page: Page, name: str) -> None:
    page.screenshot(path=SHOTS / f"{name}.png", full_page=True)


def install_browser_logging(page: Page, qa: QARun, label: str) -> None:
    def on_console(message) -> None:
        if message.type != "error":
            return
        # Chromium reports an expected non-2xx fetch as "Failed to load resource".
        # Keep it in the evidence notes; the UI error state is asserted separately.
        if message.text.startswith("Failed to load resource"):
            qa.note(f"{label}:console:{message.text}")
        else:
            qa.fail(f"{label}:console", message.text, page)

    page.on("console", on_console)
    page.on("pageerror", lambda error: qa.fail(f"{label}:pageerror", error, page))

    def on_request_failed(request) -> None:
        failure = str(request.failure)
        # EventSource is deliberately closed once a run reaches a terminal
        # state; Chromium surfaces that successful client-side close as ABORTED.
        if request.url.endswith("/events") and "ABORTED" in failure.upper():
            qa.note(f"{label}:expected event stream close")
            return
        qa.fail(f"{label}:requestfailed", f"{request.method} {request.url} {request.failure}", page)

    page.on("requestfailed", on_request_failed)


def setup_context(browser: Browser, width: int = 1440, height: int = 1000) -> BrowserContext:
    context = browser.new_context(
        viewport={"width": width, "height": height},
        reduced_motion="reduce",
        accept_downloads=True,
    )
    context.add_init_script(
        "if (!localStorage.getItem('cooksprite.language')) localStorage.setItem('cooksprite.language','zh-CN');"
        "if (!localStorage.getItem('cooksprite.theme')) localStorage.setItem('cooksprite.theme','neon');"
    )
    return context


def open_route(page: Page, path: str) -> None:
    page.goto(BASE + path)
    wait_network(page)


def connect_runtime(page: Page) -> None:
    open_route(page, "/settings")
    page.locator(".runtime-form input").nth(0).fill("rt_full_qa")
    page.locator(".runtime-form input").nth(1).fill("http://127.0.0.1:8188")
    with page.expect_response(
        "**/api/v1/runtimes/rt_full_qa/doctor", timeout=12_000
    ) as doctor_info:
        page.locator(".runtime-form .arcade-button").click()
    doctor_response = doctor_info.value
    assert doctor_response.ok, f"runtime doctor returned {doctor_response.status}"
    page.wait_for_function(
        "async () => (await fetch('/api/v1/health').then(response => response.json())).runtime === 'ready'",
        timeout=12_000,
    )
    expect(page.locator(".inline-status")).to_contain_text(re.compile("就绪|READY"), timeout=12_000)
    expect(page.locator(".settings-section .runtime-chip.ready")).to_be_visible(timeout=12_000)
    expect(page.locator(".runtime-list article")).not_to_have_count(0)


def test_offline_and_global(qa: QARun, browser: Browser) -> None:
    context = setup_context(browser)
    page = context.new_page()
    install_browser_logging(page, qa, "offline-global")
    try:
        open_route(page, "/")
        qa.step(
            "gallery empty state",
            lambda: (
                expect(page.locator(".gallery-empty")).to_be_visible(),
                assert_no_page_overflow(page),
            ),
            page,
        )
        qa.step("global accessibility on gallery", lambda: assert_accessibility_basics(page), page)
        capture(page, "01-gallery-empty")

        page.locator('a[href="/studio"]').first.click()
        wait_network(page)
        qa.step(
            "offline studio warning",
            lambda: (
                expect(page.locator(".runtime-warning")).to_be_visible(),
                expect(page.locator(".draw-button")).to_be_disabled(),
            ),
            page,
        )
        qa.step(
            "offline studio geometry",
            lambda: (
                assert_no_page_overflow(page),
                assert_visible_geometry(page, ".studio-view", 1440),
            ),
            page,
        )
        capture(page, "02-studio-offline")

        page.locator('a[href="/settings"]').first.click()
        wait_network(page)
        qa.step(
            "deep route settings",
            lambda: expect(page.locator(".settings-view")).to_be_visible(),
            page,
        )
        qa.step(
            "settings offline geometry",
            lambda: (assert_no_page_overflow(page), assert_accessibility_basics(page)),
            page,
        )

        page.locator('a[href="/library"]').first.click()
        wait_network(page)
        qa.step(
            "empty library", lambda: expect(page.locator(".library-empty")).to_be_visible(), page
        )
        page.locator('a[href="/"]').first.click()
        wait_network(page)
        qa.step(
            "logo and navigation return",
            lambda: expect(page.locator(".gallery-empty")).to_be_visible(),
            page,
        )
    finally:
        context.close()


def test_settings_and_create(qa: QARun, browser: Browser) -> None:
    context = setup_context(browser)
    page = context.new_page()
    install_browser_logging(page, qa, "settings-create")
    try:
        connect_runtime(page)
        qa.step(
            "runtime ready",
            lambda: expect(page.locator(".settings-section .runtime-chip.ready")).to_be_visible(),
            page,
        )

        # Invalid doctor response must remain a readable inline state.
        page.locator(".runtime-form input").nth(0).fill("rt_bad_qa")
        page.locator(".runtime-form input").nth(1).fill("http://127.0.0.1:8199")
        page.locator(".runtime-form .arcade-button").click()
        expect(page.locator(".inline-status")).to_be_visible(timeout=8_000)
        qa.step(
            "invalid runtime error is visible",
            lambda: assert_true(page.locator(".inline-status").inner_text()),
            page,
        )

        # Theme, language and sound must survive a reload.
        page.locator(".settings-grid fieldset").nth(0).locator(".theme-option").nth(1).click()
        page.locator(".settings-grid fieldset").nth(1).get_by_role("button", name="ENGLISH").click()
        page.locator(".settings-grid fieldset").nth(2).locator(".theme-option").click()
        page.reload()
        wait_network(page)
        qa.step(
            "settings persist after reload",
            lambda: (
                expect(page.locator(".theme-option.ember.selected")).to_be_visible(),
                expect(page.locator(".settings-view")).to_be_visible(),
            ),
            page,
        )
        capture(page, "03-settings-ember-en")

        page.locator(".settings-grid fieldset").nth(0).locator(".theme-option").nth(2).click()
        page.locator(".settings-grid fieldset").nth(1).get_by_role("button", name="中文").click()

        open_route(page, "/studio")
        qa.step(
            "runtime ready after settings reload",
            lambda: expect(page.locator(".topbar .runtime-chip.ready")).to_be_visible(
                timeout=10_000
            ),
            page,
        )
        if not page.locator(".topbar .runtime-chip.ready").is_visible():
            connect_runtime(page)
            open_route(page, "/studio")
        qa.step(
            "studio ready after settings",
            lambda: (
                expect(page.locator(".creation-deck")).to_be_visible(),
                expect(page.locator(".draw-button")).to_be_enabled(),
            ),
            page,
        )

        prompt = page.locator("#prompt")
        prompt.fill("x" * 650)
        qa.step(
            "prompt length is bounded",
            lambda: assert_true(len(prompt.input_value()) <= 600, "prompt exceeded 600 characters"),
            page,
        )
        prompt.fill("a soup knight with a copper ladle")
        option = page.locator(".segmented-control").first.locator("button").nth(1)
        active_before = (
            page.locator(".segmented-control").first.locator("button.active").all_text_contents()
        )
        option.hover()
        qa.step(
            "functional option hover shows example without changing selection",
            lambda: (
                expect(page.locator(".hover-example")).to_be_visible(),
                assert_true(
                    page.locator(".segmented-control")
                    .first.locator("button.active")
                    .all_text_contents()
                    == active_before,
                    "hover changed selected option",
                ),
            ),
            page,
        )
        page.mouse.move(8, 80)
        qa.step(
            "preset package cards are removed",
            lambda: expect(page.locator(".preset-picker, .preset-grid")).to_have_count(0),
            page,
        )
        page.locator(".segmented-control button").nth(1).click()
        page.locator(".segmented-control button").nth(0).click()
        page.locator(".model-row .text-button").click()
        qa.step(
            "advanced controls expand",
            lambda: expect(page.locator(".advanced-grid")).to_be_visible(),
            page,
        )
        page.locator(".advanced-grid input[type=range]").first.fill("0.55")
        page.locator(".advanced-grid input[type=number]").first.fill("123")

        image_input = page.locator(".artifact-input-panel input[type=file]").first
        image_input.set_input_files(ROOT / "cooksprite/example_assets/actor.svg")
        expect(page.locator(".artifact-strip .artifact-card")).to_have_count(1, timeout=10_000)
        qa.step(
            "reference upload updates artifact dock",
            lambda: (
                expect(page.locator(".artifact-input-panel .drop-target")).to_contain_text("actor"),
                expect(page.locator(".sprite-canvas img")).to_be_visible(),
            ),
            page,
        )

        page.locator(".draw-button").click()
        expect(page.locator(".artifact-strip .artifact-card")).to_have_count(5, timeout=20_000)
        qa.step(
            "image generation creates candidates",
            lambda: (
                expect(page.locator(".artifact-strip .artifact-card")).to_have_count(5),
                assert_no_page_overflow(page),
            ),
            page,
        )
        capture(page, "04-studio-image-generated")

        page.locator(".queue-button").click()
        expect(page.locator(".queue-drawer")).to_be_visible()
        page.wait_for_timeout(50)
        qa.step(
            "queue history drawer opens",
            lambda: expect(page.locator(".queue-group").last).to_contain_text("image.generate"),
            page,
        )
        capture(page, "04-queue-history")
        page.keyboard.press("Escape")
        qa.step(
            "queue closes with escape",
            lambda: expect(page.locator(".queue-drawer")).to_be_hidden(),
            page,
        )
    finally:
        context.close()


def test_animation_frames_normals_export(qa: QARun, browser: Browser) -> None:
    context = setup_context(browser)
    page = context.new_page()
    install_browser_logging(page, qa, "animation-frames")
    try:
        connect_runtime(page)
        open_route(page, "/studio")
        page.wait_for_function(
            "() => document.querySelectorAll('.artifact-strip .artifact-card').length >= 5",
            timeout=10_000,
        )

        page.locator(".creation-mode-tabs button").nth(1).click()
        expect(page.locator(".action-grid")).to_be_visible()
        for action in ["idle", "walk", "run", "attack", "cast", "hit", "jump", "death"]:
            button = page.locator(".action-grid button", has_text=action.upper())
            qa.step(
                f"animation action {action}",
                lambda button=button: (
                    button.click(),
                    expect(button).to_have_class(re.compile("active")),
                ),
                page,
            )
        runtime_probe = page.evaluate(
            "async () => { const health = await fetch('/api/v1/health').then(r => r.json()); const action = await fetch('/api/v1/actions/animation.generate').then(r => r.json()); return {runtime: health.runtime, available: action.available}; }"
        )
        qa.note(f"animation runtime probe: {runtime_probe}")
        source = page.locator(".artifact-strip .artifact-card").first
        target = page.locator(".artifact-input-panel .drop-target").first
        drag_payload = page.evaluate(
            """() => {
              const source = document.querySelector('.artifact-strip .artifact-card');
              const transfer = new DataTransfer();
              source.dispatchEvent(new DragEvent('dragstart', { bubbles: true, dataTransfer: transfer }));
              return JSON.parse(transfer.getData('application/x-cooksprite-artifact'));
            }"""
        )
        qa.step(
            "artifact drag payload is minimal",
            lambda: assert_true(set(drag_payload) == {"artifact_id", "kind"}, drag_payload),
            page,
        )
        source.drag_to(target)
        qa.note(
            f"character drop target after drag: {target.inner_text()} inputs: {page.locator('.draw-button').get_attribute('disabled')}"
        )
        qa.step(
            "valid image to character drag",
            lambda: expect(page.locator(".draw-button")).to_be_enabled(),
            page,
        )
        if page.locator(".draw-button").is_disabled():
            qa.note(
                "animation chain stopped after character drag; remaining animation checks skipped"
            )
            return
        page.locator(".action-grid button", has_text="ATTACK").click()
        with page.expect_response(
            "**/api/v1/actions/animation.generate/runs", timeout=12_000
        ) as animation_run_info:
            page.locator(".draw-button").click()
        animation_response = animation_run_info.value
        animation_body = animation_response.json()
        qa.note(f"animation run response: {animation_response.status} {animation_body.get('id')}")
        if animation_response.status >= 300:
            qa.fail(
                "animation run request", f"HTTP {animation_response.status}: {animation_body}", page
            )
            return
        try:
            wait_artifacts(page, 13)
        except Exception as error:  # noqa: BLE001 - preserve API diagnostics in the report.
            diagnostics = page.evaluate(
                """async runId => {
                  const run = await fetch(`/api/v1/runs/${runId}`).then(response => response.json());
                  return {
                    run,
                    queue: await fetch('/api/v1/queue').then(response => response.json()),
                    artifacts: await fetch(`/api/v1/projects/${run.project_id}/artifacts`).then(response => response.text()),
                    candidateCount: document.querySelector('[data-testid=candidate-row]')?.dataset.candidateCount || '0',
                    artifactCards: document.querySelectorAll('.artifact-strip .artifact-card').length,
                    notice: document.querySelector('.global-notice')?.innerText || document.querySelector('.runtime-warning')?.innerText || ''
                  };
                }""",
                animation_body["id"],
            )
            qa.fail("animation candidate wait", f"{error}; diagnostics={diagnostics}", page)
            return
        qa.step(
            "animation generation returns frame candidates",
            lambda: (
                expect(page.locator(".artifact-strip .artifact-card")).to_have_count(13),
                expect(page.locator(".dock-tabs button").nth(1)).to_have_class(
                    re.compile("active")
                ),
            ),
            page,
        )
        valid_target_label = target.inner_text()
        incompatible_state = page.evaluate(
            """async () => {
              const source = document.querySelector('.artifact-strip .artifact-card');
              const target = document.querySelector('.artifact-input-panel .drop-target');
              const transfer = new DataTransfer();
              source.dispatchEvent(new DragEvent('dragstart', { bubbles: true, dataTransfer: transfer }));
              target.dispatchEvent(new DragEvent('dragover', { bubbles: true, cancelable: true, dataTransfer: transfer }));
              await new Promise(requestAnimationFrame);
              const incompatible = target.classList.contains('incompatible');
              target.dispatchEvent(new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: transfer }));
              return incompatible;
            }"""
        )
        qa.step(
            "incompatible FrameSeq drag is rejected",
            lambda: (
                assert_true(incompatible_state, "drop target never showed incompatible state"),
                expect(target).to_contain_text(valid_target_label.splitlines()[0]),
            ),
            page,
        )

        page.locator(".stage-rail > button").nth(2).click()
        expect(page.locator(".frame-studio")).to_be_visible()
        candidates = page.locator(".candidate-row .artifact-card")
        candidates.nth(0).click()
        candidates.nth(3).click(modifiers=["Shift"])
        qa.step(
            "shift range selects candidates",
            lambda: expect(page.locator(".candidate-row .artifact-card.selected")).to_have_count(4),
            page,
        )
        page.locator(".confirm-selection").click()
        expect(page.locator(".timeline-frame")).to_have_count(4)
        qa.step(
            "selected candidates enter timeline",
            lambda: (
                expect(page.locator(".confirm-selection")).to_be_disabled(),
                expect(page.locator(".timeline-frame")).to_have_count(4),
            ),
            page,
        )

        play_button = page.locator(".playback-controls .primary-icon")
        initial_play_label = play_button.get_attribute("aria-label")
        page.keyboard.press("Space")
        qa.step(
            "space toggles playback",
            lambda: assert_true(play_button.get_attribute("aria-label") != initial_play_label),
            page,
        )
        page.keyboard.press("Space")
        page.locator(".playback-controls .icon-button").first.click()
        page.locator(".playback-controls .icon-button").last.click()
        qa.step(
            "first and next playback controls",
            lambda: expect(page.locator(".timeline-frame").nth(1)).to_have_class(
                re.compile("active")
            ),
            page,
        )
        page.locator(".playback-controls .icon-button").first.click()

        duration = page.locator(".timeline-frame").first.locator("input[type=number]").first
        duration.fill("-5")
        duration.press("Tab")
        qa.step("duration lower bound", lambda: expect(duration).to_have_value("16"), page)
        duration.fill("99999")
        duration.press("Tab")
        qa.step("duration upper bound", lambda: expect(duration).to_have_value("60000"), page)
        duration.fill("160")
        duration.press("Tab")
        qa.step(
            "single frame duration edit",
            lambda: (
                expect(duration).to_have_value("160"),
                expect(
                    page.locator(".timeline-frame").nth(1).locator("input[type=number]").first
                ).not_to_have_value("160"),
            ),
            page,
        )
        page.locator(".timeline-frame").first.locator(".offsets input").nth(0).fill("3")
        page.locator(".timeline-frame").first.locator(".offsets input").nth(0).press("Tab")
        page.locator(".mini-field input").fill("12")
        page.locator(".mini-field input").press("Tab")
        qa.step(
            "FPS updates all frame durations",
            lambda: assert_true(
                set(
                    page.locator(".timeline-frame > label input").evaluate_all(
                        "els => els.map(e => e.value)"
                    )
                )
                == {"83"}
            ),
            page,
        )
        page.locator(".mini-field select").select_option("pingpong")
        page.locator(".direction-ring button").nth(2).click()
        page.get_by_role("button", name="TOP45").click()
        qa.step(
            "timeline timing direction and view edits",
            lambda: (
                expect(page.locator(".save-indicator.saved")).to_be_visible(timeout=5_000),
                expect(page.locator(".direction-ring button").nth(2)).to_have_class(
                    re.compile("active")
                ),
            ),
            page,
        )
        page.get_by_role("button", name="LEVEL").click()
        page.locator(".direction-ring button").nth(4).click()
        expect(page.locator(".timeline-frame")).to_have_count(4)

        page.locator(".toggle-icon", has_text="A/B").click()
        page.locator(".toggle-icon", has_text=re.compile("洋葱|ONION", re.IGNORECASE)).click()
        page.locator(".toggle-icon", has_text=re.compile("差异|DIFF", re.IGNORECASE)).click()
        qa.step(
            "frame comparison toggles",
            lambda: (
                expect(page.locator(".compare-overlay")).to_be_visible(),
                expect(page.locator(".timeline-row.onion")).to_be_visible(),
            ),
            page,
        )
        page.locator(".toggle-icon", has_text="A/B").click()
        expect(page.locator(".compare-overlay")).to_be_hidden()

        page.locator(".frame-toolbar .icon-button").last.click()
        expect(page.locator(".shortcut-panel")).to_be_visible()
        page.keyboard.press("Escape")
        qa.step(
            "shortcut dialog closes with escape",
            lambda: expect(page.locator(".shortcut-panel")).to_be_hidden(),
            page,
        )

        first_frame = page.locator(".timeline-frame").first
        first_frame.locator(".frame-actions button").first.click()
        expect(page.locator(".timeline-frame")).to_have_count(5)
        page.locator(".timeline-frame").last.locator(".frame-actions button").last.click()
        expect(page.locator(".timeline-frame")).to_have_count(4)
        qa.step(
            "duplicate and delete timeline frame",
            lambda: expect(page.locator(".timeline-frame")).to_have_count(4),
            page,
        )

        moved_src = page.locator(".timeline-frame").last.locator("img").get_attribute("src")
        original_src = page.locator(".timeline-frame").first.locator("img").get_attribute("src")
        page.locator(".timeline-frame").last.drag_to(page.locator(".timeline-frame").first)
        qa.step(
            "timeline frame reorder",
            lambda: expect(page.locator(".timeline-frame").first.locator("img")).to_have_attribute(
                "src", moved_src
            ),
            page,
        )
        page.keyboard.press("Meta+z")
        qa.step(
            "timeline undo",
            lambda: expect(page.locator(".timeline-frame").first.locator("img")).to_have_attribute(
                "src", original_src
            ),
            page,
        )
        page.keyboard.press("Meta+Shift+z")
        qa.step(
            "timeline redo",
            lambda: expect(page.locator(".timeline-frame").first.locator("img")).to_have_attribute(
                "src", moved_src
            ),
            page,
        )
        page.locator(".timeline-frame").last.click()
        page.keyboard.press("Delete")
        qa.step(
            "delete clamps active frame",
            lambda: (
                expect(page.locator(".timeline-frame")).to_have_count(3),
                expect(page.locator(".timeline-frame").last).to_have_class(re.compile("active")),
            ),
            page,
        )
        page.keyboard.press("Meta+z")
        expect(page.locator(".timeline-frame")).to_have_count(4)
        page.locator(".timeline-frame").first.click()
        page.keyboard.press("Meta+d")
        expect(page.locator(".timeline-frame")).to_have_count(5)
        page.keyboard.press("Meta+z")
        qa.step(
            "keyboard duplicate and undo",
            lambda: expect(page.locator(".timeline-frame")).to_have_count(4),
            page,
        )
        expect(page.locator(".save-indicator.saved")).to_be_visible(timeout=5_000)
        page.reload()
        wait_network(page)
        page.locator(".creation-mode-tabs button").nth(1).click()
        page.locator(".action-grid button", has_text="ATTACK").click()
        page.locator(".stage-rail > button").nth(2).click()
        qa.step(
            "timeline persists after reload",
            lambda: (
                expect(page.locator(".timeline-frame")).to_have_count(4),
                expect(page.locator(".timeline-frame").first.locator("img")).to_have_attribute(
                    "src", moved_src
                ),
            ),
            page,
        )

        page.get_by_test_id("redraw-frame").click()
        wait_candidates(page, 17)
        qa.step(
            "frame redraw returns new candidates",
            lambda: expect(page.locator("[data-testid=candidate-row]")).to_have_attribute(
                "data-candidate-count", re.compile("^(1[7-9]|[2-9][0-9])$")
            ),
            page,
        )

        page.get_by_test_id("import-frame-source").click()
        expect(page.locator(".source-extractor")).to_be_visible()
        page.locator(".extractor-tabs button").nth(1).click()
        qa.step(
            "video extractor tab",
            lambda: expect(page.locator(".extractor-tabs button").nth(1)).to_have_class(
                re.compile("active")
            ),
            page,
        )
        page.locator(".extractor-tabs button").nth(0).click()
        page.locator(".source-extractor input[type=file]").set_input_files(
            ROOT / "cooksprite/example_assets/tile.svg"
        )
        page.get_by_test_id("auto-grid").click()
        grid_values = page.locator(".extractor-controls input[type=number]").evaluate_all(
            "els => Object.fromEntries(els.map(e => [e.parentElement?.innerText.trim() || '', Number(e.value)]))"
        )
        qa.note(f"auto-grid controls: {grid_values}")
        qa.step(
            "spritesheet auto grid",
            lambda: assert_true(
                all(
                    value > 0
                    for label, value in grid_values.items()
                    if not re.search(r"margin|spacing|边距|间距", label, re.IGNORECASE)
                ),
                "auto-grid left a required dimension at zero",
            ),
            page,
        )
        page.get_by_test_id("extract-source").click()
        expect(page.locator(".source-extractor")).to_be_hidden(timeout=20_000)
        wait_candidates(page, 33)
        capture(page, "05-frame-studio-full")

        page.locator(".timeline-frame").first.click()
        active_timeline_artifact = (
            page.locator(".timeline-frame").first.locator("img").get_attribute("src")
        )
        qa.step(
            "timeline frame previews in inspector",
            lambda: expect(page.locator(".inspector-preview img")).to_have_attribute(
                "src", active_timeline_artifact
            ),
            page,
        )
        page.locator(".stage-rail > button").nth(3).click()
        expect(page.locator(".normal-workspace")).to_be_visible()
        page.locator(".normal-input .arcade-button.primary").click()
        expect(page.locator(".normal-source-row .artifact-card")).to_have_count(2, timeout=20_000)
        qa.step(
            "normal map generation and source pair",
            lambda: expect(page.locator(".normal-source-row")).to_contain_text("NormalMap"),
            page,
        )
        diffuse_label = page.locator(".normal-source-row .artifact-card").first.get_attribute(
            "aria-label"
        )
        page.locator(".normal-source-row .artifact-card").nth(1).click()
        qa.step(
            "normal map selection preserves diffuse source",
            lambda: expect(
                page.locator(".normal-source-row .artifact-card").first
            ).to_have_attribute("aria-label", diffuse_label),
            page,
        )
        for index in range(6):
            button = page.locator(".hdri-strip button").nth(index)
            button.click()
            expect(button).to_have_attribute("aria-checked", "true")
        page.locator(".light-arc-control input").fill("0")
        page.locator(".light-arc-control input").fill("90")
        page.locator(".light-arc-control input").fill("180")
        page.locator(".lighting-controls input[type=range]").nth(0).fill("0")
        page.locator(".lighting-controls input[type=range]").nth(0).fill("2")
        page.locator(".lighting-controls .toggle-icon").first.click()
        page.locator(".lighting-controls .toggle-icon").last.click()
        qa.step(
            "HDR and live lighting controls",
            lambda: (
                expect(page.locator(".three-mount canvas")).to_be_visible(),
                assert_visible_geometry(page, ".lighting-preview", 1440),
            ),
            page,
        )
        qa.step(
            "lighting controls remain readable",
            lambda: assert_true(
                page.locator(".lighting-controls .toggle-icon").evaluate_all(
                    "buttons => buttons.every(button => button.clientWidth >= 70 && button.scrollWidth <= button.clientWidth + 1)"
                ),
                "lighting toggle labels are squeezed or clipped",
            ),
            page,
        )
        mount_box = page.locator(".three-mount").bounding_box()
        canvas_box = page.locator(".three-mount canvas").bounding_box()
        qa.step(
            "lighting canvas fits preview mount",
            lambda: (
                assert_true(mount_box and canvas_box, "lighting canvas has no geometry"),
                assert_true(
                    abs(mount_box["width"] - canvas_box["width"]) <= 1, (mount_box, canvas_box)
                ),
                assert_true(
                    abs(mount_box["height"] - canvas_box["height"]) <= 1, (mount_box, canvas_box)
                ),
            ),
            page,
        )
        capture(page, "06-normal-lighting")

        page.locator(".stage-rail > button").nth(2).click()
        expect(page.locator(".three-mount canvas")).to_have_count(0)
        page.locator(".stage-rail > button").nth(3).click()
        qa.step(
            "lighting preview cleans up on stage re-entry",
            lambda: expect(page.locator(".three-mount canvas")).to_have_count(1),
            page,
        )

        page.locator(".inspector-footer .arcade-button").click()
        page.locator(".stage-rail > button").nth(4).click()
        page.locator(".export-card .arcade-button.primary").click()
        expect(page.locator(".export-warning")).to_be_visible(timeout=20_000)
        qa.step(
            "incomplete export lists integrity issues",
            lambda: expect(page.locator(".export-warning li").first).to_be_visible(),
            page,
        )
        capture(page, "07-export-warning")
        page.locator(".warning-link").click()
        expect(page.locator(".package-list article")).to_have_count(1, timeout=20_000)
        qa.step(
            "package appears after accepted export",
            lambda: expect(page.locator(".package-list article")).to_be_visible(),
            page,
        )
        with page.expect_download(timeout=10_000) as download_info:
            page.locator(".package-list article .arcade-button").click()
        download = download_info.value
        package_path = download.path()
        qa.step(
            "cooksprite package download",
            lambda: (
                assert_true(download.suggested_filename.endswith(".cooksprite")),
                assert_true(package_path),
            ),
            page,
        )
        with zipfile.ZipFile(package_path) as archive:
            names = set(archive.namelist())
            manifest = json.loads(archive.read("manifest.json"))
            provenance = json.loads(archive.read("provenance.json"))
        qa.step(
            "cooksprite package contents",
            lambda: (
                assert_true({"manifest.json", "provenance.json"}.issubset(names)),
                assert_true(
                    any(name.startswith("frames/") for name in names), "package has no frames"
                ),
                assert_true(
                    any(name.startswith("normals/") for name in names), "package has no normals"
                ),
                assert_true(manifest["schema"] == "cooksprite.package/v1"),
                assert_true(isinstance(provenance.get("artifacts"), list)),
            ),
            page,
        )
        capture(page, "07-export")
    finally:
        context.close()


def test_gallery_library_queue_and_persistence(qa: QARun, browser: Browser) -> None:
    context = setup_context(browser)
    page = context.new_page()
    install_browser_logging(page, qa, "gallery-library")
    try:
        open_route(page, "/")
        # The previous test uses a separate browser context but the same backend project.
        expect(page.locator(".arcade-cabinet")).to_have_count(1, timeout=20_000)
        card = page.locator(".arcade-cabinet").first
        card.hover()
        qa.step(
            "published gallery cabinet",
            lambda: (
                expect(card.locator(".screen-play")).to_be_visible(),
                assert_no_page_overflow(page),
            ),
            page,
        )
        card.click()
        expect(page.locator(".gallery-dialog")).to_be_visible()
        qa.step(
            "gallery dialog contents",
            lambda: (
                expect(page.locator(".gallery-dialog img")).to_be_visible(),
                expect(page.locator(".gallery-dialog .token-row")).to_be_visible(),
            ),
            page,
        )
        capture(page, "09-gallery-dialog")
        page.keyboard.press("Escape")
        qa.step(
            "gallery escape closes and restores focus",
            lambda: (
                expect(page.locator(".gallery-dialog")).to_be_hidden(),
                assert_true(
                    page.evaluate(
                        "() => document.activeElement?.classList.contains('arcade-cabinet')"
                    ),
                    "gallery card did not regain focus",
                ),
            ),
            page,
        )
        card.click()
        page.locator(".dialog-overlay").click(position={"x": 10, "y": 10})
        qa.step(
            "gallery dialog closes from backdrop",
            lambda: expect(page.locator(".gallery-dialog")).to_be_hidden(),
            page,
        )
        card.click()
        page.locator(".gallery-dialog .dialog-close").click()
        qa.step(
            "gallery dialog closes from button",
            lambda: expect(page.locator(".gallery-dialog")).to_be_hidden(),
            page,
        )
        card.click()
        project_id = page.evaluate(
            "() => document.querySelector('.arcade-cabinet')?.getAttribute('data-project-id')"
        )
        page.locator(".gallery-dialog .arcade-button.primary").click()
        expect(page).to_have_url(f"{BASE}/studio/{project_id}")
        qa.step(
            "gallery continue opens source project",
            lambda: expect(page.locator(".studio-view")).to_be_visible(),
            page,
        )

        open_route(page, "/library")
        expect(page.locator(".library-grid .artifact-card").first).to_be_visible(timeout=10_000)
        artifact = page.locator(".library-grid .artifact-card").first
        artifact_name = artifact.get_attribute("aria-label").split(",", 1)[0]
        artifact.click()
        expect(page.locator(".library-layout .inspector")).to_contain_text("ARTIFACT")
        favorite = page.locator(".stack-actions .arcade-button").first
        favorite.click()
        page.reload()
        wait_network(page)
        persisted_artifact = page.locator(
            f'.library-grid .artifact-card[aria-label^="{artifact_name},"]'
        ).first
        persisted_artifact.click()
        qa.step(
            "favorite persists after reload",
            lambda: expect(page.locator(".stack-actions .arcade-button").first).to_have_class(
                re.compile("active")
            ),
            page,
        )
        capture(page, "10-library-populated")
        page.locator(".stack-actions .arcade-button.danger").click()
        qa.step(
            "artifact moves to trash",
            lambda: expect(page.locator(".library-layout .inspector")).to_have_class(
                re.compile("empty")
            ),
            page,
        )
        page.locator(".library-toolbar .text-button").click()
        expect(page.locator(".library-grid .artifact-card")).to_have_count(1)
        capture(page, "11-library-trash")
        page.locator(".library-grid .artifact-card").first.click()
        page.locator(".stack-actions .arcade-button.danger").click()
        qa.step(
            "artifact restores from trash",
            lambda: (
                expect(page.locator(".library-empty")).to_be_visible(),
                page.locator(".library-toolbar .text-button").click(),
                expect(page.locator(".library-grid .artifact-card").first).to_be_visible(),
            ),
            page,
        )
        search = page.locator(".search-field input")
        search.fill("not-a-real-artifact")
        qa.step(
            "library no results state",
            lambda: expect(page.locator(".library-empty")).to_be_visible(),
            page,
        )
        search.fill("")
        page.locator(".select-field select").select_option("NormalMap")
        qa.step(
            "library kind filter",
            lambda: expect(page.locator(".library-grid .artifact-card")).to_have_count(1),
            page,
        )

        open_route(page, "/settings")
        page.locator(".settings-grid fieldset").nth(0).locator(".theme-option").nth(0).click()
        page.locator(".settings-grid fieldset").nth(1).get_by_role("button", name="中文").click()
        page.reload()
        wait_network(page)
        qa.step(
            "global preferences persist",
            lambda: (
                expect(page.locator(".settings-view")).to_be_visible(),
                assert_no_page_overflow(page),
            ),
            page,
        )
        capture(page, "08-settings-final")

        open_route(page, "/")
        page.locator(".queue-button").click()
        expect(page.locator(".queue-drawer")).to_be_visible()
        page.wait_for_timeout(50)
        qa.step(
            "queue history from completed runs",
            lambda: (
                expect(page.locator(".queue-group").last).not_to_contain_text("No runs"),
                assert_visible_geometry(page, ".queue-drawer", 1440),
            ),
            page,
        )
        capture(page, "12-queue-history")
        page.locator(".dialog-overlay").click(position={"x": 10, "y": 10})
        qa.step(
            "queue closes from backdrop",
            lambda: expect(page.locator(".queue-drawer")).to_be_hidden(),
            page,
        )
        page.locator(".queue-button").click()
        page.locator(".queue-drawer .drawer-head > .icon-button").click()
        qa.step(
            "queue close button", lambda: expect(page.locator(".queue-drawer")).to_be_hidden(), page
        )
    finally:
        context.close()


def test_responsive(qa: QARun, browser: Browser) -> None:
    for width, height in [(1280, 900), (1024, 900), (768, 900), (375, 812), (320, 720)]:
        context = setup_context(browser, width, height)
        page = context.new_page()
        install_browser_logging(page, qa, f"responsive-{width}")
        try:
            open_route(page, "/")
            qa.step(
                f"gallery geometry {width}",
                lambda page=page: (
                    assert_no_page_overflow(page),
                    assert_accessibility_basics(page),
                ),
                page,
            )
            open_route(page, "/studio")
            if width < 768:
                qa.step(
                    f"small screen gate {width}",
                    lambda page=page: (
                        expect(page.locator(".small-screen-gate")).to_be_visible(),
                        assert_true(
                            (page.locator(".small-screen-gate").bounding_box() or {})["y"] < 80,
                            "small-screen gate starts below a blank page",
                        ),
                        assert_true(
                            page.locator("body").evaluate("e => e.scrollHeight <= innerHeight + 1"),
                            "small-screen gate creates vertical overflow",
                        ),
                    ),
                    page,
                )
            else:
                qa.step(
                    f"studio responsive {width}",
                    lambda page=page, width=width: (
                        expect(page.locator(".studio-view")).to_be_visible(),
                        assert_no_page_overflow(page),
                        assert_visible_geometry(page, ".studio-view", width),
                    ),
                    page,
                )
                if width <= 900:
                    assert page.locator(".creation-layout").evaluate(
                        "e => e.scrollWidth <= e.clientWidth + 1"
                    )
            capture(page, f"responsive-{width}")
        finally:
            context.close()


def test_queue_states_and_error_notice(qa: QARun, browser: Browser) -> None:
    context = setup_context(browser)
    page = context.new_page()
    install_browser_logging(page, qa, "queue-states")
    try:
        running = {
            "id": "run-running",
            "status": "running",
            "progress": 0.35,
            "message": "working",
            "action_id": "image.generate",
            "artifacts": [],
            "error": None,
            "created_at": "",
            "updated_at": "",
            "project_id": None,
        }
        pending = {
            **running,
            "id": "run-pending",
            "status": "queued",
            "progress": 0,
            "message": "queued",
        }
        failed = {
            **running,
            "id": "run-failed",
            "status": "failed",
            "progress": 1,
            "message": "failed",
        }
        history = {
            **running,
            "id": "run-success",
            "status": "succeeded",
            "progress": 1,
            "message": "completed",
        }
        queue_payload = {
            "running": [running],
            "pending": [pending],
            "history": [failed, history],
            "runtime": {},
        }
        page.route(
            "**/api/v1/queue",
            lambda route: route.fulfill(
                status=200, content_type="application/json", body=json.dumps(queue_payload)
            ),
        )
        page.route(
            "**/api/v1/runs/run-running/cancel",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({**running, "status": "cancelled", "message": "cancelled"}),
            ),
        )
        page.route(
            "**/api/v1/runs/run-failed/retry",
            lambda route: route.fulfill(
                status=202,
                content_type="application/json",
                body=json.dumps(
                    {**failed, "id": "run-retry", "status": "queued", "message": "queued"}
                ),
            ),
        )
        open_route(page, "/")
        page.locator(".queue-button").click()
        expect(page.locator(".queue-drawer")).to_be_visible()
        qa.step(
            "queue running pending history groups",
            lambda: (
                expect(page.locator(".queue-group").nth(0)).to_contain_text("1"),
                expect(page.locator(".queue-group").nth(1)).to_contain_text("1"),
                expect(page.locator(".queue-group").nth(2)).to_contain_text("2"),
            ),
            page,
        )
        page.locator(".run-row.is-running").get_by_role(
            "button", name=re.compile("取消|cancel", re.IGNORECASE)
        ).click()
        qa.step(
            "queue cancel action",
            lambda: expect(page.locator(".run-row.is-cancelled")).to_be_visible(),
            page,
        )
        page.locator(".run-row.is-failed").get_by_role(
            "button", name=re.compile("重试|retry", re.IGNORECASE)
        ).click()
        qa.step(
            "queue retry action",
            lambda: expect(page.locator(".run-row.is-queued")).to_be_visible(),
            page,
        )
        capture(page, "13-queue-states")
        page.locator(".queue-drawer .drawer-head > .icon-button").click()

        page.route(
            "**/api/v1/health",
            lambda route: route.fulfill(
                status=503, content_type="application/json", body=json.dumps({"detail": "offline"})
            ),
        )
        page.reload()
        wait_network(page)
        expect(page.locator(".global-notice")).to_be_visible()
        page.locator(".global-notice .icon-button").click()
        qa.step(
            "global API error notice dismisses",
            lambda: expect(page.locator(".global-notice")).to_be_hidden(),
            page,
        )
    finally:
        context.close()


def smoke_cross_browser(qa: QARun, browser_type, name: str) -> None:
    browser = browser_type.launch(headless=True)
    qa.note(f"{name} version: {browser.version}")
    context = setup_context(browser, 1024, 820)
    page = context.new_page()
    install_browser_logging(page, qa, name)
    try:
        open_route(page, "/")
        expect(page.locator(".topbar")).to_be_visible()
        open_route(page, "/studio")
        expect(page.locator(".creation-deck")).to_be_visible()
        qa.step(
            f"{name} smoke routes",
            lambda: (assert_accessibility_basics(page), assert_no_page_overflow(page)),
            page,
        )
        capture(page, f"{name}-smoke")
    finally:
        context.close()
        browser.close()


def main() -> None:
    qa = QARun()
    phase = os.environ.get("COOKSPRITE_QA_PHASE", "full")
    with sync_playwright() as playwright:
        chromium = playwright.chromium.launch(headless=True)
        qa.note(f"chromium version: {chromium.version}")
        try:
            if phase == "full":
                test_offline_and_global(qa, chromium)
                seed_runtime()
                test_settings_and_create(qa, chromium)
                test_animation_frames_normals_export(qa, chromium)
                test_gallery_library_queue_and_persistence(qa, chromium)
                test_responsive(qa, chromium)
                test_queue_states_and_error_notice(qa, chromium)
            elif phase == "tail":
                seed_runtime()
                test_responsive(qa, chromium)
                test_queue_states_and_error_notice(qa, chromium)
            else:
                raise ValueError(f"unknown COOKSPRITE_QA_PHASE {phase}")
        finally:
            chromium.close()
        smoke_cross_browser(qa, playwright.firefox, "firefox")
        smoke_cross_browser(qa, playwright.webkit, "webkit")
    qa.write_report()
    print(f"COOKSPRITE_FULL_QA_REPORT {REPORT}")
    print(f"COOKSPRITE_FULL_QA_SHOTS {SHOTS}")
    if qa.failures:
        print(f"COOKSPRITE_FULL_QA_FAILED {len(qa.failures)}")
        raise SystemExit(1)
    print("COOKSPRITE_FULL_QA_OK")


if __name__ == "__main__":
    main()
