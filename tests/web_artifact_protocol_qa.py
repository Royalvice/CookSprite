"""Browser QA for the single typed Artifact rendering and drag protocol."""

from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:5173"
ARTIFACT_MIME = "application/x-cooksprite-artifact"


def capture_drag_payload(source, target) -> dict:
    transfer = source.page.evaluate_handle("new DataTransfer()")
    source.dispatch_event("dragstart", {"dataTransfer": transfer})
    captured = transfer.evaluate(
        """dataTransfer => ({
          payload: JSON.parse(dataTransfer.getData('application/x-cooksprite-artifact')),
          plain: dataTransfer.getData('text/plain'),
          types: [...dataTransfer.types],
        })"""
    )
    target.dispatch_event("dragenter", {"dataTransfer": transfer})
    target.dispatch_event("dragover", {"dataTransfer": transfer})
    target.dispatch_event("drop", {"dataTransfer": transfer})
    source.dispatch_event("dragend", {"dataTransfer": transfer})
    return captured


def cross_browser_smoke(browser_type) -> None:
    browser = browser_type.launch(headless=True)
    page = browser.new_page(viewport={"width": 1024, "height": 900})
    page.goto(f"{BASE}/studio", wait_until="networkidle")
    if page.get_by_role("button", name="切换到中文").count():
        page.get_by_role("button", name="切换到中文").click()
    page.get_by_role("button", name="角色", exact=True).hover()
    source = page.locator(".hover-example .artifact-visual[data-artifact-kind=Image]")
    target = page.locator(".artifact-input-panel .drop-target").first
    expect(source).to_be_visible()
    source.drag_to(target)
    expect(target).to_contain_text("EXAMPLE · ACTOR")
    assert page.locator("img").evaluate_all(
        "images => images.every(image => Boolean(image.closest('.artifact-visual')))"
    )
    assert page.evaluate("document.body.scrollWidth <= window.innerWidth")
    browser.close()


def run() -> None:
    failures: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on(
            "console",
            lambda message: (
                failures.append(f"console:{message.type}:{message.text}")
                if message.type == "error"
                else None
            ),
        )
        page.on("pageerror", lambda error: failures.append(f"pageerror:{error}"))
        page.on(
            "requestfailed",
            lambda request: failures.append(f"requestfailed:{request.url}:{request.failure}"),
        )

        page.goto(f"{BASE}/studio", wait_until="networkidle")
        if page.get_by_role("button", name="切换到中文").count():
            page.get_by_role("button", name="切换到中文").click()
        expect(page.get_by_text("未配置", exact=True)).to_be_visible()

        image_action = page.request.get(
            "http://127.0.0.1:8000/api/v1/actions/image.generate"
        ).json()
        assert "presets" not in image_action
        category = next(
            control for control in image_action["controls"] if control["id"] == "category"
        )
        assert all(option["example"]["kind"] == "Image" for option in category["options"])
        assert all(
            option["example"]["url"].startswith("/api/v1/artifacts/")
            for option in category["options"]
        )
        assert page.request.get("http://127.0.0.1:8000/api/v1/artifacts").json() == []

        page.get_by_role("button", name="角色", exact=True).hover()
        tooltip = page.locator(".hover-example")
        expect(tooltip).to_be_visible()
        example_image = tooltip.locator(".artifact-visual")
        expect(example_image).to_have_attribute("data-artifact-kind", "Image")
        reference = page.locator(".artifact-input-panel .drop-target").first
        image_drag = capture_drag_payload(example_image, reference)
        assert set(image_drag["payload"]) == {"artifact_id", "kind"}
        assert image_drag["payload"]["kind"] == "Image"
        assert image_drag["plain"] == image_drag["payload"]["artifact_id"]
        assert ARTIFACT_MIME in image_drag["types"]
        expect(reference).to_contain_text("EXAMPLE · ACTOR")
        expect(
            page.locator(".sprite-canvas .artifact-visual[data-artifact-kind=Image]")
        ).to_be_visible()
        page.wait_for_timeout(1700)
        page.mouse.move(1, 1)
        page.get_by_role("button", name="角色", exact=True).hover()
        expect(page.locator(".hover-example")).to_be_visible()
        page.locator(".hover-example .artifact-visual").drag_to(reference)
        expect(reference).to_have_attribute("data-drop-state", "success")

        untyped_images = page.locator("img").evaluate_all(
            "images => images.filter(image => !image.closest('.artifact-visual')).map(image => image.src)"
        )
        assert untyped_images == [], f"untyped business images: {untyped_images}"

        page.locator(".stage-rail > button").nth(1).click()
        character = page.locator(".artifact-input-panel .drop-target").first
        selected_image = page.locator(
            ".animation-preview .artifact-visual[data-artifact-kind=Image]"
        )
        expect(selected_image).to_be_visible()
        capture_drag_payload(selected_image, character)
        expect(character).to_contain_text("EXAMPLE · ACTOR")

        page.get_by_role("button", name="奔跑", exact=True).hover()
        tooltip = page.locator(".hover-example")
        sequence_example = tooltip.locator(".artifact-visual")
        expect(sequence_example).to_have_attribute("data-artifact-kind", "FrameSeq")
        sequence_target = page.locator(".sequence-source-row .drop-target")
        sequence_example.drag_to(sequence_target)
        expect(page.locator(".candidate-row")).to_have_attribute("data-candidate-count", "4")
        page.mouse.move(1, 1)
        page.get_by_role("button", name="奔跑", exact=True).hover()
        sequence_drag = capture_drag_payload(
            page.locator(".hover-example .artifact-visual"), sequence_target
        )
        assert set(sequence_drag["payload"]) == {"artifact_id", "kind"}
        assert sequence_drag["payload"]["kind"] == "FrameSeq"
        expect(page.locator(".candidate-row")).to_have_attribute("data-candidate-count", "4")
        expect(page.locator(".candidate-row .artifact-card")).to_have_count(4)

        first_frame = page.locator(".candidate-row .artifact-card").first
        page.wait_for_timeout(1700)
        first_frame.drag_to(character)
        expect(character).to_have_attribute("data-drop-state", "success")
        expect(character).to_contain_text("EXAMPLE FRAME 1")

        page.get_by_role("button", name="奔跑", exact=True).hover()
        sequence_example = page.locator(".hover-example .artifact-visual")
        capture_drag_payload(sequence_example, character)
        expect(character).to_have_attribute("data-drop-state", "error")
        expect(character).to_contain_text("这里需要一张角色素材")

        page.locator(".stage-rail > button").nth(2).click()
        normal_target = page.locator(".normal-input .drop-target")
        expect(normal_target).to_contain_text("EXAMPLE FRAME 1")
        page.get_by_role("tab", name="原图", exact=True).click()
        diffuse_visual = page.locator(".map-preview .artifact-visual[data-artifact-kind=Image]")
        expect(diffuse_visual).to_be_visible()
        capture_drag_payload(diffuse_visual, normal_target)
        expect(normal_target).to_contain_text("EXAMPLE FRAME 1")

        for width in (1440, 1024):
            page.set_viewport_size({"width": width, "height": 900})
            page.wait_for_timeout(100)
            geometry = page.evaluate(
                """() => ({
                  body: document.body.scrollWidth,
                  viewport: window.innerWidth,
                  visuals: [...document.querySelectorAll('.artifact-visual')].every(el => {
                    const rect = el.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                  }),
                })"""
            )
            assert geometry["body"] <= geometry["viewport"], geometry
            assert geometry["visuals"], geometry

        output = Path("/tmp/cooksprite-artifact-protocol.png")
        page.screenshot(path=output, full_page=True)
        assert output.exists()
        assert not failures, json.dumps(failures, ensure_ascii=False, indent=2)
        browser.close()

        cross_browser_smoke(playwright.firefox)
        cross_browser_smoke(playwright.webkit)


if __name__ == "__main__":
    run()
