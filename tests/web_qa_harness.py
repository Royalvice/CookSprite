"""Shared browser lifecycle, navigation, and failure collection for Web QA scripts."""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Literal

from playwright.sync_api import Page, sync_playwright

BrowserName = Literal["chromium", "firefox", "webkit"]


@dataclass(slots=True)
class BrowserQASession:
    page: Page
    failures: list[str]

    def assert_clean(self) -> None:
        assert not self.failures, json.dumps(self.failures, ensure_ascii=False, indent=2)


def _launch_options(browser_name: BrowserName) -> dict[str, Any]:
    environment_name = f"PLAYWRIGHT_{browser_name.upper()}_EXECUTABLE"
    executable = os.environ.get(environment_name) or shutil.which(browser_name)
    return {"executable_path": executable} if executable else {}


@contextmanager
def browser_qa(
    *,
    browser_name: BrowserName = "chromium",
    viewport: tuple[int, int] = (1440, 1000),
    timeout_ms: int = 20_000,
    collect_http_errors: bool = False,
    ignored_http_fragments: Sequence[str] = (),
) -> Iterator[BrowserQASession]:
    """Open one headless browser page and collect browser/protocol failures."""

    failures: list[str] = []
    with sync_playwright() as playwright:
        browser_type = getattr(playwright, browser_name)
        browser = browser_type.launch(
            headless=True,
            **_launch_options(browser_name),
        )
        page = browser.new_page(viewport={"width": viewport[0], "height": viewport[1]})
        page.set_default_timeout(timeout_ms)
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
        if collect_http_errors:
            page.on(
                "response",
                lambda response: (
                    failures.append(f"http:{response.status}:{response.url}")
                    if response.status >= 400
                    and not any(fragment in response.url for fragment in ignored_http_fragments)
                    else None
                ),
            )
        try:
            yield BrowserQASession(page=page, failures=failures)
        finally:
            browser.close()


def wait_for_app(
    page: Page,
    url: str,
    *,
    selectors: Sequence[str] = (".studio-view", ".library-view"),
    wait_until: Literal["commit", "domcontentloaded", "load", "networkidle"] = "commit",
    timeout_ms: int = 20_000,
    settle_ms: int = 0,
) -> None:
    """Navigate and wait for a CookSprite application root, not a timer alone."""

    page.goto(url, wait_until=wait_until, timeout=timeout_ms)
    page.locator(", ".join(selectors)).first.wait_for(timeout=timeout_ms)
    if settle_ms:
        page.wait_for_timeout(settle_ms)
