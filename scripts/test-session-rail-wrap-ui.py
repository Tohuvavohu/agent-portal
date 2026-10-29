#!/usr/bin/env python3
"""Exercise multi-row session bars using the built dashboard and API fixtures.

Build frontend with trunk, install Python Playwright + Chromium, then run from
this checkout. No agent or real conversation is used.
"""
import asyncio
import functools
import http.server
import json
import re
from pathlib import Path
import threading
from urllib.parse import urlparse
from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[1] / "frontend" / "dist"
USER = "00000000-0000-4000-8000-000000000001"
SESSION = "00000000-0000-4000-8000-000000000002"


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if urlparse(self.path).path == "/dashboard":
            self.path = "/index.html"
        super().do_GET()

    def log_message(self, *_args):
        pass


async def main():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    session = dict(id=SESSION, user_id=USER, session_key=SESSION, session_name="Original session",
                   working_directory="/home/test/project", status="active", paused=False,
                   hostname="test-host", agent_type="codex", my_role="owner",
                   last_activity="2026-01-01T00:00:00", created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00")

    async def api(route):
        path = urlparse(route.request.url).path
        assert route.request.method == "GET", "Layout changes must not mutate sessions"
        if path == "/api/sessions":
            data = dict(sessions=[dict(session, id=f"00000000-0000-4000-8000-{i:012d}", session_name=f"Session {i:02d}", hostname="alpha" if i < 10 else "beta") for i in range(2, 20)])
        elif path == "/api/auth/me":
            data = dict(id=USER, email="test@example.invalid", name="Test user", is_admin=False)
        elif path.endswith("/turn-metrics") or path == "/api/metrics/recent":
            data = dict(metrics=[], latest_session_metrics=[])
        elif path.endswith("/edit-stack"):
            data = dict(items=[])
        elif path.endswith("/messages"):
            data = dict(messages=[], total=0)
        elif path == "/api/launchers":
            data = []
        else:
            data = {}
        await route.fulfill(status=200, content_type="application/json", body=json.dumps(data))

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport=dict(width=1280, height=900))
            await page.route("**/api/**", api)
            await page.route_web_socket("**/ws/client", lambda ws: None)
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            await page.goto(f"{base}/dashboard?session={SESSION}")
            rail = page.locator(".session-rail")
            await expect(page.locator(".session-pill")).to_have_count(18)
            assert await rail.evaluate("(e) => e.scrollWidth > e.clientWidth")
            await page.get_by_role("button", name="Settings", exact=True).click()
            await page.get_by_role("button", name="Appearance", exact=True).click()
            toggle = page.locator(".appearance-setting").filter(has=page.get_by_role("heading", name="Wrap sessions onto multiple rows")).get_by_role("checkbox")
            await toggle.check()
            await page.get_by_role("button", name="< Back", exact=True).click()
            await expect(page.locator(".dashboard-body")).to_have_class(re.compile("rail-wrap"))
            assert await rail.evaluate("(e) => e.scrollWidth <= e.clientWidth")
            assert len(set(await page.locator(".session-pill").evaluate_all("(es) => es.map(e => Math.round(e.getBoundingClientRect().top))"))) >= 3
            await page.reload()
            await expect(page.locator(".dashboard-body")).to_have_class(re.compile("rail-wrap"))
            # Grouping and both horizontal positions wrap without clipping pills.
            for position in ("top", "bottom"):
                for grouped in (False, True):
                    await page.evaluate("([p,g]) => { localStorage.setItem('claude-portal-rail-orientation',p); localStorage.setItem('claude-portal-session-rail-group-by-host',String(g)); }", [position, grouped])
                    await page.reload()
                    await expect(page.locator(".session-pill")).to_have_count(18)
                    assert await rail.evaluate("(e) => e.scrollWidth <= e.clientWidth")
                    # Menu remains reachable on a later row.
                    await page.locator(".pill-menu-toggle").nth(12).click()
                    await expect(page.get_by_role("button", name="Rename session Change display name")).to_be_visible()
                    await page.locator(".session-name-button").click()
                    await page.keyboard.press("Escape")
            for position in ("left", "right"):
                await page.evaluate("(p) => localStorage.setItem('claude-portal-rail-orientation',p)", position)
                await page.reload()
                await expect(page.locator(".session-pill")).to_have_count(18)
                assert await rail.evaluate("(e) => getComputedStyle(e).flexWrap === 'nowrap'")
            # A very short window retains conversation space and scrolls vertically.
            await page.evaluate("() => { localStorage.setItem('claude-portal-rail-orientation','top'); localStorage.setItem('claude-portal-session-rail-group-by-host','false'); }")
            await page.set_viewport_size(dict(width=1000, height=500))
            await page.reload()
            await expect(page.locator(".session-pill")).to_have_count(18)
            assert await rail.evaluate("(e) => e.clientHeight <= innerHeight * .35 + 30 && e.scrollHeight > e.clientHeight")
            await page.set_viewport_size(dict(width=1280, height=900))
            await page.reload()
            await expect(page.locator(".session-pill")).to_have_count(18)
            await page.get_by_role("button", name="Settings", exact=True).click()
            await page.get_by_role("button", name="Appearance", exact=True).click()
            await toggle.uncheck()
            await page.get_by_role("button", name="< Back", exact=True).click()
            assert await rail.evaluate("(e) => e.scrollWidth > e.clientWidth")
            await page.set_viewport_size(dict(width=390, height=844))
            await page.reload()
            await expect(page.locator(".mobile-session-selector")).to_be_visible()
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert not errors, errors
            print("PASS: wrapping, persistence, grouping, menus, top/bottom, vertical rails, height cap, toggle off, mobile")
            await browser.close()
    finally:
        server.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
