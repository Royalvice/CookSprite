"""Browser acceptance against a real CookSprite API and real ComfyUI runtime.

This suite deliberately does not start or recognize the test-only fake runtime.
It reuses artifacts produced by the local real-runtime acceptance flow so that
browser behavior can be checked without making every layout run wait for a new
diffusion pass.
"""

from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import Locator, Page, expect, sync_playwright

BASE = "http://127.0.0.1:5173"
SHOTS = Path(__file__).parents[1] / "web" / "test-results" / "real-runtime"
ARTIFACT_MIME = "application/x-cooksprite-artifact"


def capture_drag(source: Locator, target: Locator) -> dict:
    transfer = source.page.evaluate_handle("new DataTransfer()")
    source.dispatch_event("dragstart", {"dataTransfer": transfer})
    payload = transfer.evaluate(
        """data => ({
          artifact: JSON.parse(data.getData('application/x-cooksprite-artifact')),
          plain: data.getData('text/plain'),
          types: [...data.types],
        })"""
    )
    target.dispatch_event("dragenter", {"dataTransfer": transfer})
    target.dispatch_event("dragover", {"dataTransfer": transfer})
    target.dispatch_event("drop", {"dataTransfer": transfer})
    source.dispatch_event("dragend", {"dataTransfer": transfer})
    return payload


def assert_page_geometry(page: Page, label: str) -> None:
    geometry = page.evaluate(
        """() => {
          const visible = element => {
            const style = getComputedStyle(element);
            const rect = element.getBoundingClientRect();
            return style.display !== 'none' && style.visibility !== 'hidden'
              && rect.width > 0 && rect.height > 0;
          };
          const boxes = [...document.querySelectorAll(
            '.topbar,.studio-main,.project-bar,.studio-stage-content,' +
            '.creation-deck,.frame-studio,.normal-workspace,.lighting-preview'
          )].filter(visible).map(element => {
            const rect = element.getBoundingClientRect();
            return {className: element.className, left: rect.left, right: rect.right, width: rect.width};
          });
          return {
            body: document.body.scrollWidth,
            viewport: document.documentElement.clientWidth,
            boxes,
            emptyVisuals: [...document.querySelectorAll('.artifact-visual')]
              .filter(visible)
              .filter(element => {
                const rect = element.getBoundingClientRect();
                return rect.width < 1 || rect.height < 1;
              }).length,
          };
        }"""
    )
    assert geometry["body"] <= geometry["viewport"] + 1, {label: geometry}
    assert geometry["emptyVisuals"] == 0, {label: geometry}
    for box in geometry["boxes"]:
        assert box["left"] >= -1, {label: label, "box": box}
        assert box["right"] <= geometry["viewport"] + 1, {label: label, "box": box}


def run() -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
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
            lambda request: errors.append(f"requestfailed:{request.url}:{request.failure}"),
        )
        page.on(
            "response",
            lambda response: (
                errors.append(f"http:{response.status}:{response.url}")
                if response.status >= 400 and "/hdri/" not in response.url
                else None
            ),
        )

        health = page.request.get(f"{BASE}/api/v1/health").json()
        assert health["runtime"] == "ready", health
        assert health["runtime_id"] == "local-managed", health
        assert all(item["available"] for item in health["actions"].values()), health
        runtimes = page.request.get(f"{BASE}/api/v1/runtimes").json()
        runtime = next(item for item in runtimes if item["id"] == "local-managed")
        assert runtime["status"] == "ready", runtime
        assert runtime["recipes"], runtime
        projects = page.request.get(f"{BASE}/api/v1/projects").json()
        project = next((item for item in projects if item["name"] == "Real Local Acceptance"), None)
        assert project, projects

        page.goto(f"{BASE}/studio/{project['id']}", wait_until="networkidle")
        if page.get_by_role("button", name="切换到中文").count():
            page.get_by_role("button", name="切换到中文").click()
        expect(page.locator(".topbar .runtime-chip.ready")).to_be_visible(timeout=15_000)
        expect(page.locator(".runtime-warning")).to_have_count(0)
        expect(page.locator(".studio-stage-content")).to_be_visible()
        expect(page.locator(".stage-rail .stage-check")).to_have_count(0)
        expect(page.locator(".stage-rail > button.done")).to_have_count(0)
        assert_page_geometry(page, "create-1440")
        page.screenshot(path=SHOTS / "01-create-real-runtime.png", full_page=True)

        # Every option preview is a typed, draggable CookSprite Artifact.
        category_option = page.locator(".creation-deck .segmented-control button").first
        category_option.hover()
        tooltip = page.locator(".hover-example")
        expect(tooltip).to_be_visible()
        preview = tooltip.locator(".artifact-visual")
        expect(preview).to_have_attribute("data-artifact-kind", "Image")
        size = preview.locator("img").evaluate(
            "image => ({width:image.naturalWidth,height:image.naturalHeight})"
        )
        assert size["width"] > 0 and size["height"] > 0, size
        page.mouse.move(2, 80)

        # Animation is one continuous generator + sequence editor workspace.
        page.locator(".stage-rail > button").nth(1).click()
        expect(page.locator(".animation-generator")).to_be_visible()
        expect(page.locator(".frame-studio")).to_be_visible()
        action = page.locator(".action-grid button").nth(2)
        action.hover()
        tooltip = page.locator(".hover-example")
        expect(tooltip).to_be_visible()
        expect(tooltip.locator(".artifact-visual")).to_have_attribute(
            "data-artifact-kind", "FrameSeq"
        )
        expect(tooltip.locator("img")).to_be_visible()
        animation_name = tooltip.locator("img").evaluate(
            "image => getComputedStyle(image).animationName"
        )
        assert animation_name != "none", animation_name
        page.screenshot(path=SHOTS / "02-animation-hover-preview.png", full_page=True)
        page.mouse.move(2, 80)
        expect(tooltip).to_be_hidden()

        source = page.locator(".animation-preview .artifact-visual[data-artifact-kind=Image]")
        target = page.locator(".animation-generator .drop-target")
        expect(source).to_be_visible()
        dragged = capture_drag(source, target)
        assert set(dragged["artifact"]) == {"artifact_id", "kind"}, dragged
        assert dragged["artifact"]["kind"] == "Image", dragged
        assert dragged["plain"] == dragged["artifact"]["artifact_id"], dragged
        assert ARTIFACT_MIME in dragged["types"], dragged
        expect(target).to_have_attribute("data-drop-state", "success")
        character_preview = target.locator(".artifact-visual[data-artifact-kind=Image] img")
        expect(character_preview).to_be_visible()
        character_size = character_preview.evaluate(
            "image => ({width:image.naturalWidth,height:image.naturalHeight})"
        )
        assert character_size["width"] > 0 and character_size["height"] > 0, character_size
        expect(page.locator(".animation-generator .draw-button")).to_be_enabled()
        target.screenshot(path=SHOTS / "02b-character-drop-thumbnail.png")
        page.screenshot(path=SHOTS / "02b-animation-input-thumbnail.png", full_page=True)

        sequence_card = page.locator(".sequence-dock .artifact-card").first
        sequence_target = page.locator(".sequence-source-row .drop-target")
        expect(sequence_card).to_be_visible()
        sequence_drag = capture_drag(sequence_card, sequence_target)
        assert sequence_drag["artifact"]["kind"] == "FrameSeq", sequence_drag
        expect(sequence_target).to_have_attribute("data-drop-state", "success")
        expect(
            sequence_target.locator(".artifact-visual[data-artifact-kind=FrameSeq] img")
        ).to_be_visible()
        sequence_target.screenshot(path=SHOTS / "02c-sequence-drop-thumbnail.png")
        expect(page.locator(".candidate-row")).to_have_attribute("data-candidate-count", "4")
        expect(page.locator(".candidate-row .artifact-card")).to_have_count(4)
        expect(page.locator(".target-badge")).to_contain_text("WALK · LEVEL · S")
        expect(page.locator(".frame-studio").get_by_text("视角", exact=True)).to_have_count(0)
        expect(page.locator(".frame-studio").get_by_text("方向", exact=True)).to_have_count(0)

        before = page.locator(".timeline-frame").count()
        candidates = page.locator(".candidate-row .artifact-card")
        candidates.first.click()
        candidates.nth(3).click(modifiers=["Shift"])
        expect(page.locator(".candidate-row .artifact-card.selected")).to_have_count(4)
        page.locator(".confirm-selection").click()
        expect(page.locator(".commit-feedback.saved")).to_be_visible(timeout=15_000)
        expect(page.locator(".timeline-frame")).to_have_count(before + 4)
        expect(page.locator(".save-indicator.saved")).to_be_visible(timeout=15_000)
        page.screenshot(path=SHOTS / "03-animation-track-saved.png", full_page=True)

        for width in (1440, 1280, 1024, 768):
            page.set_viewport_size({"width": width, "height": 900})
            page.locator(".studio-stage-content").evaluate("element => element.scrollTop = 0")
            page.wait_for_timeout(200)
            assert_page_geometry(page, f"animation-{width}")
            page.screenshot(path=SHOTS / f"03-animation-{width}.png", full_page=True)
        page.set_viewport_size({"width": 1440, "height": 1000})

        # Existing real normal output must be directly visible, with one canvas and a same-direction light.
        candidates.first.click()
        page.locator(".stage-rail > button").nth(2).click()
        expect(page.locator(".hdri-strip button")).to_have_count(3)
        expect(page.locator(".lighting-stage canvas[data-lighting-canvas=true]")).to_have_count(1)
        page.get_by_role("tab", name="法线图", exact=True).click()
        normal_visual = page.locator(".map-preview .artifact-visual[data-artifact-kind=NormalMap]")
        expect(normal_visual).to_be_visible(timeout=10_000)
        page.wait_for_function(
            "() => (document.querySelector('.map-preview img')?.naturalWidth || 0) > 0",
            timeout=10_000,
        )
        normal_size = normal_visual.locator("img").evaluate(
            "image => ({width:image.naturalWidth,height:image.naturalHeight})"
        )
        assert normal_size["width"] > 0 and normal_size["height"] > 0, normal_size
        page.screenshot(path=SHOTS / "04-real-normal-map.png", full_page=True)
        page.get_by_role("tab", name="光照结果", exact=True).click()
        stage = page.locator(".lighting-stage")
        box = stage.bounding_box()
        assert box
        page.mouse.move(box["x"] + box["width"] * 0.75, box["y"] + box["height"] * 0.45)
        page.mouse.down()
        page.mouse.move(box["x"] + box["width"] * 0.2, box["y"] + box["height"] * 0.45)
        page.mouse.up()
        left = float(page.locator(".screen-light-gizmo").evaluate("e => parseFloat(e.style.left)"))
        page.mouse.move(box["x"] + box["width"] * 0.2, box["y"] + box["height"] * 0.45)
        page.mouse.down()
        page.mouse.move(box["x"] + box["width"] * 0.8, box["y"] + box["height"] * 0.45)
        page.mouse.up()
        right = float(page.locator(".screen-light-gizmo").evaluate("e => parseFloat(e.style.left)"))
        assert left < right, {"left": left, "right": right}
        page.screenshot(path=SHOTS / "05-real-lighting-preview.png", full_page=True)

        # Re-entering the lighting stage must not leak another WebGL canvas.
        page.locator(".stage-rail > button").first.click()
        page.locator(".stage-rail > button").nth(2).click()
        expect(page.locator(".lighting-stage canvas[data-lighting-canvas=true]")).to_have_count(1)

        for width in (1440, 1280, 1024, 768):
            page.set_viewport_size({"width": width, "height": 900})
            page.locator(".studio-stage-content").evaluate("element => element.scrollTop = 0")
            page.wait_for_timeout(200)
            assert_page_geometry(page, f"normal-{width}")
            page.screenshot(path=SHOTS / f"06-normal-{width}.png", full_page=True)

        page.set_viewport_size({"width": 1440, "height": 1000})
        page.goto(f"{BASE}/settings", wait_until="networkidle")
        expect(
            page.locator(".runtime-list article").filter(has_text="Local ComfyUI")
        ).to_contain_text("就绪")
        expect(page.locator(".managed-setup-card")).to_contain_text("本机环境已就绪")
        expect(page.locator(".managed-setup-card")).to_contain_text(
            "v1-5-pruned-emaonly-fp16.safetensors"
        )
        assert_page_geometry(page, "settings-1440")
        page.screenshot(path=SHOTS / "07-settings-real-runtime.png", full_page=True)
        page.set_viewport_size({"width": 768, "height": 900})
        page.wait_for_timeout(200)
        assert_page_geometry(page, "settings-768")
        page.screenshot(path=SHOTS / "08-settings-768.png", full_page=True)

        assert not errors, json.dumps(errors, ensure_ascii=False, indent=2)
        browser.close()


if __name__ == "__main__":
    run()
