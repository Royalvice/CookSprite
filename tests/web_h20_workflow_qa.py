"""Real browser workflow acceptance against CookSprite API + ComfyUI on H20.

This suite never starts or accepts the fake runtime.  Start a Vite frontend with
``COOKSPRITE_API_PROXY_TARGET`` pointing at the tunneled H20 CookSprite API,
then provide the JSON produced by ``remote_real_acceptance.py``.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from playwright.sync_api import Locator, Page, expect, sync_playwright

BASE = os.environ.get("COOKSPRITE_WEB_URL", "http://127.0.0.1:15173").rstrip("/")
RESULT = Path(
    os.environ.get(
        "COOKSPRITE_REAL_RESULT",
        "web/test-results/h20-workflow-20260814/api-real-acceptance.json",
    )
)
SHOTS = Path(
    os.environ.get(
        "COOKSPRITE_QA_SHOTS",
        "web/test-results/h20-workflow-20260814/browser",
    )
)
EDGE = "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"
ARTIFACT_MIME = "application/x-cooksprite-artifact"


def browser_launch_options() -> dict:
    executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
    if executable:
        return {"executable_path": executable}
    if Path(EDGE).exists():
        return {"executable_path": EDGE}
    return {}


def wait_for_app(page: Page, url: str) -> None:
    page.goto(url, wait_until="commit", timeout=20_000)
    page.locator(".studio-view, .library-view").first.wait_for(timeout=20_000)
    page.wait_for_timeout(1_200)


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


def assert_geometry(page: Page, label: str) -> None:
    geometry = page.evaluate(
        """() => {
          const visible = element => {
            const style = getComputedStyle(element);
            const rect = element.getBoundingClientRect();
            return style.display !== 'none' && style.visibility !== 'hidden'
              && rect.width > 0 && rect.height > 0;
          };
          const selectors = [
            '.topbar', '.studio-main', '.project-bar', '.studio-stage-content',
            '.creation-deck', '.run-results', '.continue-bar', '.frame-studio',
            '.normal-workspace', '.lighting-preview', '.library-layout'
          ];
          const boxes = selectors.flatMap(selector => [...document.querySelectorAll(selector)])
            .filter(visible).map(element => {
              const rect = element.getBoundingClientRect();
              return { selector: element.className, left: rect.left, right: rect.right,
                width: rect.width, scrollWidth: element.scrollWidth, clientWidth: element.clientWidth };
            });
          return {
            body: document.body.scrollWidth,
            viewport: document.documentElement.clientWidth,
            boxes,
          };
        }"""
    )
    assert geometry["body"] <= geometry["viewport"] + 1, {label: geometry}
    for box in geometry["boxes"]:
        assert box["left"] >= -1, {label: label, "box": box}
        assert box["right"] <= geometry["viewport"] + 1, {label: label, "box": box}


def run() -> None:
    acceptance = json.loads(RESULT.read_text(encoding="utf-8"))
    project_id = acceptance["project_id"]
    image_id = acceptance["image_artifact"]
    sequence_id = acceptance["frame_sequence"]
    SHOTS.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, **browser_launch_options())
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.set_default_timeout(20_000)
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
        page.on(
            "response",
            lambda response: (
                failures.append(f"http:{response.status}:{response.url}")
                if response.status >= 400 and "/hdri/" not in response.url
                else None
            ),
        )

        health = page.request.get(f"{BASE}/api/v1/health").json()
        assert health["runtime"] == "ready", health
        assert health["runtime_id"] == "h20-gpu0-workflow", health
        assert all(item["available"] for item in health["actions"].values()), health

        image_action = page.request.get(f"{BASE}/api/v1/actions/image.generate").json()
        controls = {item["id"]: item for item in image_action["controls"]}
        assert {option["id"] for option in controls["category"]["options"]} == {
            "character",
            "weapon",
            "prop",
            "terrain",
            "scene",
            "vfx",
        }
        assert {option["id"] for option in controls["style"]["options"]} == {
            "pixel",
            "smooth",
        }
        assert all(
            option["i18n"]["zh-CN"]["description"]
            for control in (controls["category"], controls["style"])
            for option in control["options"]
        )
        assert "presets" not in image_action

        wait_for_app(page, f"{BASE}/studio/{project_id}")
        expect(page.locator(".topbar .runtime-chip.ready")).to_be_visible()
        expect(page.locator(".stage-check")).to_have_count(0)
        expect(page.locator(".run-results .artifact-card")).to_have_count(1)
        result = page.locator(".run-results .artifact-card").first
        assert result.locator(".artifact-visual").get_attribute("data-artifact-id") == image_id
        result.click()
        expect(page.locator(".continue-bar")).to_be_visible()

        category = page.locator(".creation-deck .segmented-control button").first
        category.hover()
        tooltip = page.locator(".hover-example")
        expect(tooltip).to_be_visible()
        expect(tooltip).to_contain_text("单个完整角色")
        expect(tooltip.locator(".artifact-visual")).to_have_attribute("data-artifact-kind", "Image")
        page.mouse.move(2, 80)
        page.screenshot(path=SHOTS / "01-create-result-and-next-step.png")

        page.get_by_role("button", name="用这张图制作动画").click()
        expect(page.locator(".stage-rail > button").nth(1)).to_have_class(re.compile("active"))
        character = page.locator(".animation-generator .drop-target").first
        expect(character.locator(".artifact-visual img")).to_be_visible()
        assert character.locator(".artifact-visual").get_attribute("data-artifact-id") == image_id
        expect(page.locator(".animation-generator .draw-button")).to_be_enabled()

        expect(page.locator(".candidate-row")).to_have_attribute(
            "data-candidate-count", str(len(acceptance["frames"]))
        )
        sequence_card = page.locator(
            f'.sequence-dock .artifact-visual[data-artifact-id="{sequence_id}"]'
        ).locator("xpath=ancestor::button[1]")
        sequence_target = page.locator(".sequence-source-row .drop-target")
        drag = capture_drag(sequence_card, sequence_target)
        assert drag["artifact"] == {"artifact_id": sequence_id, "kind": "FrameSeq"}
        assert drag["plain"] == sequence_id
        assert ARTIFACT_MIME in drag["types"]
        expect(sequence_target).to_have_attribute("data-drop-state", "success")

        candidates = page.locator(".candidate-row .artifact-card")
        candidates.first.click()
        candidates.last.click(modifiers=["Shift"])
        expect(page.locator(".candidate-row .artifact-card.selected")).to_have_count(
            len(acceptance["frames"])
        )
        page.locator(".confirm-selection").click()
        expect(page.locator(".commit-feedback.saved")).to_be_visible()
        expect(page.locator(".timeline-frame")).to_have_count(len(acceptance["frames"]))
        expect(page.locator(".final-sequence-bar")).to_be_visible()
        expect(page.get_by_role("button", name="用整段动画生成法线")).to_be_visible()
        page.screenshot(path=SHOTS / "02-animation-curated-track.png", full_page=True)

        page.get_by_role("button", name="用整段动画生成法线").click()
        expect(page.locator(".stage-rail > button").nth(2)).to_have_class(re.compile("active"))
        expect(page.locator(".normal-frame-strip button")).to_have_count(len(acceptance["frames"]))
        expect(
            page.locator(
                '.normal-input .drop-target .artifact-visual[data-artifact-kind="FrameSeq"] img'
            )
        ).to_be_visible()
        expect(page.locator(".hdri-strip button")).to_have_count(3)

        normal_tab = page.get_by_role("tab", name="法线图", exact=True)
        expect(normal_tab).to_be_enabled()
        normal_tab.click()
        normal_image = page.locator(
            '.map-preview .artifact-visual[data-artifact-kind="NormalMap"] img'
        )
        expect(normal_image).to_be_visible()
        page.wait_for_function("document.querySelector('.map-preview img')?.naturalWidth > 0")
        dimensions = normal_image.evaluate(
            "image => ({width:image.naturalWidth,height:image.naturalHeight})"
        )
        assert dimensions["width"] > 0 and dimensions["height"] > 0, dimensions
        page.screenshot(path=SHOTS / "03-real-normal-map.png")

        page.get_by_role("tab", name="光照结果", exact=True).click()
        expect(page.locator("canvas[data-lighting-canvas=true]")).to_have_count(1)
        gizmo = page.locator(".screen-light-gizmo")
        expect(gizmo).to_be_visible()
        stage = page.locator(".lighting-stage")
        box = stage.bounding_box()
        assert box
        y = box["y"] + box["height"] * 0.45
        page.mouse.move(box["x"] + box["width"] * 0.8, y)
        page.mouse.down()
        page.mouse.move(box["x"] + box["width"] * 0.2, y)
        page.mouse.up()
        left = float(gizmo.evaluate("element => parseFloat(element.style.left)"))
        page.mouse.move(box["x"] + box["width"] * 0.2, y)
        page.mouse.down()
        page.mouse.move(box["x"] + box["width"] * 0.8, y)
        page.mouse.up()
        right = float(gizmo.evaluate("element => parseFloat(element.style.left)"))
        assert left < right, {"left": left, "right": right}
        page.screenshot(path=SHOTS / "04-real-light-gizmo.png")

        for width in (1440, 1024, 768):
            page.set_viewport_size({"width": width, "height": 900})
            page.wait_for_timeout(150)
            assert_geometry(page, f"normal-{width}")
            page.screenshot(path=SHOTS / f"05-normal-{width}.png")

        page.set_viewport_size({"width": 1440, "height": 1000})
        page.reload(wait_until="commit")
        expect(page.locator(".normal-workspace")).to_be_visible()
        expect(page.locator(".normal-frame-strip button")).to_have_count(len(acceptance["frames"]))
        assert (
            page.locator(".normal-input .drop-target .artifact-visual").get_attribute(
                "data-artifact-id"
            )
            == sequence_id
        )

        page.get_by_role("link", name="素材库").click()
        expect(page.locator(".library-view")).to_be_visible()
        image_card = page.locator(
            f'.library-grid .artifact-visual[data-artifact-id="{image_id}"]'
        ).locator("xpath=ancestor::button[1]")
        image_card.click()
        expect(page.locator(".library-workflow-actions")).to_be_visible()
        page.get_by_role("button", name="用这张图制作动画").click()
        expect(page.locator(".animation-generator")).to_be_visible()
        assert (
            page.locator(".animation-generator .drop-target .artifact-visual").get_attribute(
                "data-artifact-id"
            )
            == image_id
        )

        assert not failures, json.dumps(failures, ensure_ascii=False, indent=2)
        browser.close()

    print(
        json.dumps(
            {
                "status": "passed",
                "runtime": "h20-gpu0-workflow",
                "project": project_id,
                "image": image_id,
                "sequence": sequence_id,
                "screenshots": str(SHOTS.resolve()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    run()
