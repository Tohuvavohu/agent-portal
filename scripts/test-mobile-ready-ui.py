#!/usr/bin/env python3
"""Exercise the built UI with cross-session API fixtures in Chromium and WebKit.

Run after `trunk build` with Playwright installed. No real sessions are mutated.
"""
import argparse
import asyncio
import functools
import http.server
import json
import threading
from pathlib import Path
from urllib.parse import urlparse
from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
USER = '00000000-0000-4000-8000-000000000001'
A = '00000000-0000-4000-8000-000000000002'
B = '00000000-0000-4000-8000-000000000003'
C = '00000000-0000-4000-8000-000000000004'
REPLY = '00000000-0000-4000-8000-000000000005'
AT = '2026-10-31T19:00:00.000001'

class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if urlparse(self.path).path in ('/dashboard', '/settings'):
            self.path = '/index.html'
        super().do_GET()
    def log_message(self, *_):
        pass

async def exercise(browser, base, device, name):
    context = await browser.new_context(**device, service_workers='block')
    page = await context.new_page()
    errors, fetched, updates = [], [], []
    page.on('pageerror', lambda error: errors.append(f'{error}\n{error.stack}'))
    sessions = [dict(id=id, user_id=USER, session_key=id, session_name=label,
                     working_directory='/work/project', status='active', paused=False,
                     hostname='test-host', agent_type='codex', my_role='owner',
                     last_activity=AT, created_at=AT, updated_at=AT)
                for id, label in [(A, 'Portal dev'), (B, 'AstroPIC'), (C, 'Alignment Tools')]]
    rows = {id: dict(session_id=id, reply_id=None, reply_at=None, preview='', unread=False,
                    needs_approval=False, working=False, connected=True, muted=False)
            for id in (A, B, C)}
    rows[B].update(working=True)
    rows[C].update(needs_approval=True)
    async def api(route):
        path = urlparse(route.request.url).path
        if path == '/api/session-attention':
            data = dict(sessions=list(rows.values()))
        elif path.endswith('/attention'):
            id = path.split('/')[3]
            req = route.request.post_data_json
            updates.append((id, req))
            if req.get('seen_reply_id') == rows[id]['reply_id'] and req.get('seen_reply_id'):
                rows[id]['unread'] = False
            if req.get('muted') is not None:
                rows[id]['muted'] = req['muted']
            await route.fulfill(status=204)
            return
        elif path == '/api/sessions':
            data = dict(sessions=sessions)
        elif path == '/api/config':
            data = dict(app_title='Agent Portal', server_version='2.15.1822', protocol_version=1)
        elif path == '/api/auth/me':
            data = dict(id=USER, email='test@example.invalid', name='Test', is_admin=False)
        elif path == '/api/portal-connections':
            data = dict(connections=[])
        elif path == '/api/agent/sessions':
            data = dict(sessions=[])
        elif path.endswith('/messages'):
            id = path.split('/')[3]
            fetched.append(id)
            messages = []
            if id == B and rows[B]['reply_id']:
                messages = [dict(role='unknown', created_at=AT,
                                content=json.dumps(dict(type='turn.completed', usage=dict(input_tokens=1, output_tokens=1, cached_input_tokens=0))),
                                meta=dict(created_at=AT))]
            data = dict(messages=messages, total=len(messages))
        elif path.endswith('/turn-metrics') or path == '/api/metrics/recent':
            data = dict(metrics=[], latest_by_session=[])
        elif path.endswith('/edit-stack'):
            data = dict(items=[])
        elif path == '/api/push/vapid-key':
            await route.fulfill(status=404, body='Not configured')
            return
        elif path == '/api/launchers':
            data = []
        else:
            data = {}
        await route.fulfill(content_type='application/json', body=json.dumps(data))
    async def ws(route):
        def receive(raw):
            packet = json.loads(raw)
            if packet['type'] == 'Hello':
                route.send(json.dumps(dict(type='HelloAck', protocol_version=1, success=True)))
        route.on_message(receive)
    await page.route('**/api/**', api)
    await page.route_web_socket('**/ws/**', ws)
    await page.goto(f'{base}/dashboard?session={A}')
    await expect(page.get_by_role('button', name='Ready sessions: 1', exact=True)).to_be_visible(timeout=60000)
    textarea = page.locator('.session-view-wrapper.focused textarea').first
    await expect(textarea).to_be_enabled()
    await textarea.fill('Keep this unsent draft')
    # Complete an unopened session. No transcript subscription is required.
    rows[B].update(working=False, unread=True, reply_id=REPLY, reply_at=AT+'Z', preview='The CAD changes are ready for review.')
    await expect(page.get_by_role('button', name='Ready sessions: 2', exact=True)).to_be_visible(timeout=12000)
    assert B not in fetched, 'background transcript must stay unloaded'
    await expect(page.locator('.ready-toast')).to_contain_text('AstroPIC')
    await expect(textarea).to_have_value('Keep this unsent draft')
    await page.get_by_role('button', name='Ready sessions: 2', exact=True).click()
    dialog = page.get_by_role('dialog', name='Sessions', exact=True)
    await expect(dialog.get_by_text('Needs approval', exact=True)).to_be_visible()
    await expect(dialog.get_by_text('New reply', exact=True)).to_be_visible()
    await expect(dialog.get_by_text('The CAD changes are ready for review.', exact=True)).to_be_visible()
    assert await dialog.evaluate('(e) => e.getBoundingClientRect().width <= innerWidth')
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    # Opening the sheet alone must not acknowledge either session.
    assert not any(id == B and req.get('seen_reply_id') for id, req in updates)
    await dialog.get_by_role('button', name='Mute notifications for AstroPIC').click()
    await expect(dialog.get_by_role('button', name='Unmute notifications for AstroPIC')).to_be_visible(timeout=12000)
    (ROOT/'builds').mkdir(exist_ok=True)
    await page.screenshot(path=str(ROOT/'builds'/f'mobile-ready-{name}.png'))
    await dialog.locator('.ready-open').filter(has_text='AstroPIC').click()
    await expect(page.locator('.ready-current')).to_contain_text('AstroPIC')
    await expect(page.get_by_role('button', name='Ready sessions: 1', exact=True)).to_be_visible(timeout=15000)
    assert any(id == B and req.get('seen_reply_id') == REPLY for id,req in updates)
    # Unresolved approvals remain in the queue after viewing.
    await page.get_by_role('button', name='Next ready session').click()
    await expect(page.locator('.ready-current')).to_contain_text('Alignment Tools')
    await expect(page.get_by_role('button', name='Ready sessions: 1', exact=True)).to_be_visible()
    await page.locator('.ready-current').click()
    await page.get_by_role('dialog').locator('.ready-open').filter(has_text='Portal dev').click()
    await expect(page.locator('.session-view-wrapper.focused textarea').first).to_have_value('Keep this unsent draft')
    # Receipt survives reload; queue is not reconstructed from every idle session.
    # Observe the new page's lease, not the previous page's final heartbeat.
    updates.clear()
    await page.reload()
    await expect(page.get_by_role('button', name='Ready sessions: 1', exact=True)).to_be_visible(timeout=15000)
    await page.set_viewport_size(dict(width=320,height=520))
    await expect(page.locator('.mobile-ready-bar')).to_be_visible()
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    # A fresh mobile page starts without a selected transcript.
    await page.locator('.ready-current').click()
    await page.get_by_role('dialog').locator('.ready-open').filter(has_text='Portal dev').click()
    # Settings covers the transcript, so it must release the viewing lease.
    for _ in range(80):
        if updates and updates[-1][1].get('viewing'):
            break
        await asyncio.sleep(0.1)
    assert updates and updates[-1][1].get('viewing'), 'visible conversation should renew its lease'
    await page.get_by_role('button', name='Settings', exact=True).click()
    for _ in range(80):
        if not updates[-1][1].get('viewing'):
            break
        await asyncio.sleep(0.1)
    assert not updates[-1][1].get('viewing'), 'settings overlay must not silence phone pushes'
    await page.get_by_text('Notifications', exact=True).first.click()
    await expect(page.get_by_text('Android:', exact=False) if name=='android' else page.get_by_text('iPhone/iPad:', exact=False)).to_be_visible()
    assert not errors, errors
    await context.close()
    print(f'PASS {name}: background completion, queue, approval, mute, precise receipt, draft, reload, narrow viewport, settings releases presence, setup guidance')

async def main(selected):
    server = http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=str(ROOT/'frontend/dist')))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        async with async_playwright() as p:
            for name, engine, device in [('android', p.chromium, 'Pixel 7'), ('iphone', p.webkit, 'iPhone 13')]:
                if selected not in ('all', name):
                    continue
                browser = await engine.launch()
                await exercise(browser, f'http://127.0.0.1:{server.server_port}', p.devices[device], name)
                await browser.close()
    finally:
        server.shutdown()

if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser', choices=['all', 'android', 'iphone'], default='all')
    asyncio.run(main(parser.parse_args().browser))
