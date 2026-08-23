"""Manual-orchestrated heartbeat check against an actual ComfyUI process."""

import time

from playwright.sync_api import expect

from web_qa_harness import browser_qa, wait_for_app


def main() -> None:
    with browser_qa(viewport=(1280, 900)) as qa:
        page = qa.page
        wait_for_app(
            page,
            "http://127.0.0.1:5173/studio",
            selectors=(".studio-view",),
            wait_until="networkidle",
        )
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
        qa.assert_clean()
        print("PASS runtime heartbeat recovered", flush=True)


if __name__ == "__main__":
    main()
