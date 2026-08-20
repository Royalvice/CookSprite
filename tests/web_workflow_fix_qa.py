"""Focused browser acceptance for the unified animation and normal workflow.

Requires Vite on 5173, CookSprite API on 8000, and the explicitly enabled
test-only Comfy stand-in on 8188.
"""

from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

BASE = "http://127.0.0.1:5173"
ROOT = Path(__file__).parents[1]
SHOTS = Path("/tmp/cooksprite-workflow-fix")


def no_overflow(page: Page) -> None:
    result = page.locator("body").evaluate(
        "body => ({body: body.scrollWidth, viewport: document.documentElement.clientWidth})"
    )
    assert result["body"] <= result["viewport"] + 1, result


def contained(page: Page, selector: str) -> None:
    box = page.locator(selector).bounding_box()
    assert box, selector
    viewport = page.viewport_size
    assert viewport
    assert box["x"] >= -1, {selector: box, "viewport": viewport}
    assert box["x"] + box["width"] <= viewport["width"] + 1, {
        selector: box,
        "viewport": viewport,
    }


def open_studio(page: Page) -> None:
    response = page.request.post(
        f"{BASE}/api/v1/projects",
        data={"name": "Workflow QA", "type": "static"},
    )
    assert response.ok, response.text()
    project = response.json()
    page.goto(f"{BASE}/studio/{project['id']}")
    page.wait_for_load_state("networkidle")
    expect(page.locator(".topbar .runtime-chip.ready")).to_be_visible(timeout=10_000)


def create_image(page: Page) -> None:
    source = ROOT / "cooksprite/example_assets/actor.svg"
    page.locator(".artifact-input-panel input[type=file]").first.set_input_files(source)
    expect(page.locator(".sprite-canvas img")).to_be_visible(timeout=10_000)
    page.locator(".draw-button").click()
    page.wait_for_function(
        "() => document.querySelectorAll('.artifact-strip .artifact-card').length >= 5",
        timeout=20_000,
    )


def empty_animation_state(page: Page) -> None:
    """An empty animation editor must be guidance, not a blank pseudo-timeline."""
    page.locator(".stage-rail > button").nth(1).click()
    editor = page.locator(".frame-studio.is-empty")
    expect(editor).to_be_visible()
    expect(editor.locator(".frame-studio-empty")).to_be_visible()
    expect(editor.locator(".frame-toolbar")).to_have_count(0)
    expect(editor.locator(".candidate-row")).to_have_count(0)
    expect(editor.locator(".timeline-row")).to_have_count(0)
    box = editor.bounding_box()
    assert box and box["height"] < 300, box
    no_overflow(page)
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SHOTS / "animation-empty-compact.png", full_page=True)
    page.locator(".stage-rail > button").first.click()


def hover_preview_flow(page: Page) -> None:
    """Hover previews animate without silently changing the selected option."""
    page.locator(".stage-rail > button").nth(1).click()
    actions = page.locator(".action-grid button")
    active_before = actions.locator(".active").count()
    selected_before = actions.locator(".active").all_text_contents()
    actions.nth(2).hover()
    preview = page.locator(".hover-example")
    expect(preview).to_be_visible()
    expect(preview).to_contain_text("示例动画占位")
    assert actions.locator(".active").count() == active_before
    assert actions.locator(".active").all_text_contents() == selected_before
    animation_name = preview.locator("img").evaluate("node => getComputedStyle(node).animationName")
    assert animation_name != "none", animation_name
    dimensions = preview.locator("img").evaluate(
        "node => ({width: node.naturalWidth, height: node.naturalHeight})"
    )
    assert dimensions["width"] > 0 and dimensions["height"] > 0, dimensions
    contained(page, ".hover-example")

    page.mouse.move(8, 80)
    expect(preview).to_have_count(0)
    expect(page.locator(".preset-picker, .preset-grid")).to_have_count(0)
    page.locator(".stage-rail > button").first.click()


def animation_flow(page: Page) -> None:
    page.locator(".stage-rail > button").nth(1).click()
    expect(page.locator(".animation-generator")).to_be_visible()
    source = page.locator(".animation-preview img")
    target = page.locator(".animation-generator .drop-target").first
    payload = page.evaluate(
        """() => {
          const source = document.querySelector('.animation-preview img');
          const transfer = new DataTransfer();
          source.dispatchEvent(new DragEvent('dragstart', {bubbles:true, dataTransfer:transfer}));
          return JSON.parse(transfer.getData('application/x-cooksprite-artifact'));
        }"""
    )
    assert set(payload) == {"artifact_id", "kind"}, payload
    source.drag_to(target)
    expect(target).to_have_attribute("data-drop-state", "success")

    # Some browsers preserve only text/plain during a cross-component drag.
    page.evaluate(
        """({id}) => {
          const target = document.querySelector('.animation-generator .drop-target');
          const transfer = new DataTransfer();
          transfer.setData('text/plain', id);
          target.dispatchEvent(new DragEvent('dragenter', {bubbles:true, cancelable:true, dataTransfer:transfer}));
          target.dispatchEvent(new DragEvent('drop', {bubbles:true, cancelable:true, dataTransfer:transfer}));
        }""",
        {"id": payload["artifact_id"]},
    )
    expect(target).to_have_attribute("data-drop-state", "success")
    expect(page.locator(".draw-button")).to_be_enabled()
    page.locator(".action-grid button").nth(3).click()
    page.locator(".draw-button").click()
    page.wait_for_function(
        "() => Number(document.querySelector('[data-testid=candidate-row]')?.dataset.candidateCount || 0) === 8",
        timeout=20_000,
    )
    expect(page.locator(".sequence-dock .artifact-card")).to_have_count(1)
    expect(page.locator(".direction-row")).to_have_count(0)
    expect(page.locator(".target-badge")).to_contain_text("ATTACK · LEVEL · S")
    candidates = page.locator(".candidate-row .artifact-card")
    candidates.nth(0).click()
    candidates.nth(3).click(modifiers=["Shift"])
    expect(page.locator(".candidate-row .artifact-card.selected")).to_have_count(4)
    page.locator(".confirm-selection").click()
    expect(page.locator(".commit-feedback.saved")).to_be_visible(timeout=10_000)
    expect(page.locator(".timeline-frame")).to_have_count(4)
    expect(page.locator(".save-indicator.saved")).to_be_visible(timeout=10_000)

    sequence_card = page.locator(".sequence-dock .artifact-card").first
    sequence_drop = page.locator(".sequence-source-row .drop-target")
    page.evaluate(
        """({id}) => {
          const target = document.querySelector('.sequence-source-row .drop-target');
          const transfer = new DataTransfer();
          transfer.setData('application/x-cooksprite-artifact', JSON.stringify({artifact_id:id, kind:'Image'}));
          target.dispatchEvent(new DragEvent('dragenter', {bubbles:true, cancelable:true, dataTransfer:transfer}));
          target.dispatchEvent(new DragEvent('drop', {bubbles:true, cancelable:true, dataTransfer:transfer}));
        }""",
        {"id": payload["artifact_id"]},
    )
    expect(sequence_drop).to_have_attribute("data-drop-state", "error")
    expect(sequence_drop).to_contain_text("FrameSeq")
    expect(page.locator(".timeline-frame")).to_have_count(4)
    sequence_card.drag_to(sequence_drop)
    expect(sequence_drop).to_have_attribute("data-drop-state", "success")
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SHOTS / "animation-workbench.png", full_page=True)

    page.reload()
    page.wait_for_load_state("networkidle")
    page.locator(".stage-rail > button").nth(1).click()
    expect(page.locator(".timeline-frame")).to_have_count(4, timeout=10_000)


def normal_flow(page: Page) -> None:
    sequence_id = page.evaluate(
        "async () => (await fetch('/api/v1/artifacts?kind=FrameSeq').then(r => r.json()))[0].id"
    )
    page.locator(".stage-rail > button").nth(2).click()
    target = page.locator(".normal-input .drop-target")
    page.evaluate(
        """({id}) => {
          const target = document.querySelector('.normal-input .drop-target');
          const transfer = new DataTransfer();
          transfer.setData('application/x-cooksprite-artifact', JSON.stringify({artifact_id:id, kind:'FrameSeq'}));
          target.dispatchEvent(new DragEvent('dragenter', {bubbles:true, cancelable:true, dataTransfer:transfer}));
          target.dispatchEvent(new DragEvent('drop', {bubbles:true, cancelable:true, dataTransfer:transfer}));
        }""",
        {"id": sequence_id},
    )
    expect(target).to_have_attribute("data-drop-state", "success")
    page.locator(".normal-input .arcade-button.primary").click()
    expect(page.locator(".normal-source-row .artifact-card")).to_have_count(2, timeout=20_000)
    expect(page.locator(".hdri-strip button")).to_have_count(3)
    page.locator(".preview-mode-tabs button").nth(2).click()
    expect(page.locator(".map-preview img")).to_be_visible()
    dimensions = page.locator(".map-preview img").evaluate(
        "image => ({naturalWidth:image.naturalWidth,naturalHeight:image.naturalHeight,width:image.getBoundingClientRect().width,height:image.getBoundingClientRect().height})"
    )
    assert dimensions["naturalWidth"] > 0 and dimensions["naturalHeight"] > 0, dimensions
    assert abs(
        dimensions["naturalWidth"] / dimensions["naturalHeight"]
        - dimensions["width"] / dimensions["height"]
    ) < 0.01, dimensions
    page.locator(".preview-mode-tabs button").first.click()
    expect(page.locator(".screen-light-gizmo")).to_have_count(0)
    stage = page.locator(".lighting-stage")
    box = stage.bounding_box()
    assert box
    page.mouse.move(box["x"] + box["width"] * 0.8, box["y"] + box["height"] * 0.45)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] * 0.2, box["y"] + box["height"] * 0.45)
    page.mouse.up()
    left = float(stage.get_attribute("data-light-x"))
    page.mouse.move(box["x"] + box["width"] * 0.2, box["y"] + box["height"] * 0.45)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] * 0.8, box["y"] + box["height"] * 0.45)
    page.mouse.up()
    right = float(stage.get_attribute("data-light-x"))
    assert left < right, {"left": left, "right": right}
    stage.focus()
    before_key = float(stage.get_attribute("data-light-y"))
    page.keyboard.press("ArrowUp")
    assert float(stage.get_attribute("data-light-y")) > before_key
    expect(page.locator(".three-mount canvas")).to_have_count(1)
    page.locator(".stage-rail > button").nth(0).click()
    expect(page.locator(".three-mount canvas")).to_have_count(0)
    page.locator(".stage-rail > button").nth(2).click()
    expect(page.locator(".three-mount canvas")).to_have_count(1)
    page.screenshot(path=SHOTS / "normal-map-and-light.png", full_page=True)


def layout_matrix(page: Page) -> None:
    for width in (1440, 1280, 1024, 768):
        page.set_viewport_size({"width": width, "height": 900})
        page.locator(".stage-rail > button").nth(1).click()
        expect(page.locator(".stage-rail > button.active")).to_contain_text("Animate")
        page.wait_for_timeout(250)
        page.locator(".studio-stage-content").evaluate("node => node.scrollTop = 0")
        no_overflow(page)
        for selector in (
            ".studio-view",
            ".studio-stages",
            ".studio-main",
            ".project-bar",
            ".studio-stage-content",
            ".animation-generator",
            ".frame-studio",
        ):
            contained(page, selector)
        creation_geometry = page.locator(".animation-generator .creation-layout").evaluate(
            "node => ({client: node.clientWidth, scroll: node.scrollWidth})"
        )
        assert creation_geometry["scroll"] <= creation_geometry["client"] + 1, {
            "width": width,
            "creation": creation_geometry,
        }
        page.screenshot(path=SHOTS / f"animation-{width}.png")

        page.locator(".stage-rail > button").nth(2).click()
        expect(page.locator(".stage-rail > button.active")).to_contain_text("Normal")
        page.wait_for_timeout(250)
        page.locator(".studio-stage-content").evaluate("node => node.scrollTop = 0")
        no_overflow(page)
        for selector in (".normal-workspace", ".normal-input", ".lighting-preview"):
            contained(page, selector)
        page.screenshot(path=SHOTS / f"normal-{width}.png")


def main() -> None:
    errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on(
            "console",
            lambda message: errors.append(message.text) if message.type == "error" else None,
        )
        open_studio(page)
        empty_animation_state(page)
        create_image(page)
        hover_preview_flow(page)
        animation_flow(page)
        normal_flow(page)
        layout_matrix(page)
        no_overflow(page)
        context.close()
        browser.close()

        for browser_type in (playwright.firefox, playwright.webkit):
            smoke = browser_type.launch(headless=True)
            context = smoke.new_context(viewport={"width": 1024, "height": 900})
            page = context.new_page()
            open_studio(page)
            page.locator(".stage-rail > button").nth(1).click()
            expect(page.locator(".frame-studio")).to_be_visible()
            no_overflow(page)
            context.close()
            smoke.close()
    assert not errors, json.dumps(errors, ensure_ascii=False, indent=2)
    print(f"PASS unified workflow; screenshots: {SHOTS}")


if __name__ == "__main__":
    main()
