import json

from playwright.sync_api import sync_playwright


STATE = {
    "account": {"market_open": False, "equity": 100000, "buying_power": 100000},
    "open_positions": [],
    "closed_positions": [],
    "cycle_history": [],
    "daily": {},
    "latest_cycle": {"timestamp": "2026-09-02T13:00:00Z", "llm_available": False, "results": []},
    "evaluation": {"stats": {"total_closed": 0, "win_rate": None, "total_pl": 0, "by_strategy": {}}, "lessons": [], "postmortems": []},
}
BAR_DATA = {
    "symbol": "SPY",
    "timeframe": "1H",
    "bars": [
        {"timestamp": "2026-09-02T13:00:00Z", "close": 500.0},
        {"timestamp": "2026-09-02T14:00:00Z", "close": 501.5},
        {"timestamp": "2026-09-02T15:00:00Z", "close": 500.8},
    ],
}


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        errors = []
        page.on("console", lambda message: errors.append(f"console {message.type}: {message.text}") if message.type == "error" else None)
        page.on("pageerror", lambda error: errors.append(f"pageerror: {error}"))
        page.on("requestfailed", lambda request: errors.append(f"requestfailed: {request.url}: {request.failure}"))
        page.route("**/api/state", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(STATE)))
        page.route("**/api/bars/**", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(BAR_DATA)))

        page.goto("http://127.0.0.1:8000/", wait_until="networkidle")
        page.wait_for_selector("#trend_chart polyline")
        assert page.get_by_role("heading", name="Trend Historical").is_visible()
        assert page.get_by_label("Pilih symbol grafik").is_visible()
        assert page.get_by_label("Pilih timeframe grafik").is_visible()
        assert "close terakhir" in page.locator("#chart_status").inner_text()

        page.set_viewport_size({"width": 390, "height": 844})
        assert page.locator("body").evaluate("element => element.scrollWidth <= document.documentElement.clientWidth")
        assert not errors, errors
        browser.close()


if __name__ == "__main__":
    main()
