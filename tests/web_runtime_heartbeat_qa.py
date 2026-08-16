"""Manual-orchestrated heartbeat check against an actual ComfyUI process."""

import time

from playwright.sync_api import expect, sync_playwright


def main() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto("http://127.0.0.1:5173/studio")
        page.wait_for_load_state("networkidle")
        expect(page.locator(".topbar .runtime-chip.ready")).to_be_visible(timeout=10_000)
        print("READY: stop the real managed ComfyUI runtime", flush=True)
        input()
        started = time.monotonic()
        expect(page.locator(".topbar .runtime-chip.offline")).to_be_visible(timeout=5_000)
        offline_elapsed = time.monotonic() - started
        expect(page.locator(".draw-button")).to_be_disabled()
        print(f"OFFLINE in {offline_elapsed:.2f}s: restart the real runtime", flush=True)
        input()
        expect(page.locator(".topbar .runtime-chip.ready")).to_be_visible(timeout=12_000)
        expect(page.locator(".draw-button")).to_be_enabled()
        print("PASS runtime heartbeat recovered", flush=True)
        browser.close()


if __name__ == "__main__":
    main()
