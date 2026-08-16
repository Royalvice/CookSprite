from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto("http://127.0.0.1:5173")
    page.wait_for_load_state("networkidle")
    assert page.get_by_role("heading", name="CookSprite").is_visible()
    assert page.get_by_text("Saved, versioned sprite tasks").is_visible()
    browser.close()
