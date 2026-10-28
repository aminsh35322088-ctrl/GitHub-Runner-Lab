#!/usr/bin/env python3
"""One-time credential submission page. Listens on 127.0.0.1 only; exposed to the tailnet by `tailscale serve`.

Write-only: it never displays or returns a stored value, and it never logs request lines or bodies.
"""
import html
import hmac
import http.server
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.parse

import lab_credentials as cr

ROUTE = re.compile(r'^/r/([0-9a-f]{16})/([A-Za-z0-9_-]{20,64})$')
MAX_BODY = 8192
MAX_ATTEMPTS = 5
VALUE = re.compile(r'\S{8,4096}')

PAGE = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Lab credential</title><style>body{{font:16px system-ui,sans-serif;max-width:34rem;margin:2rem auto;padding:0 1rem}}
input[type=password]{{width:100%;padding:.6rem;margin:.4rem 0 1rem;box-sizing:border-box}}button{{padding:.6rem 1.2rem}}
code{{background:#8881;padding:.1rem .3rem;border-radius:3px}}.err{{color:#b00020}}</style>{body}</html>"""


def page(body):
    return PAGE.format(body=body).encode()


def form(meta, message=''):
    cmds = ', '.join(f'<code>{html.escape(c)}</code>' for c in meta['allowed_commands'])
    help_url = meta.get('help_url')
    help_html = f'<p><a href="{html.escape(help_url)}" rel="noreferrer noopener" target="_blank">Create a token</a></p>' if help_url else ''
    purpose = html.escape(meta['purpose']) if meta['purpose'] else '(none given)'
    err = f'<p class="err">{html.escape(message)}</p>' if message else ''
    return page(f"""<h1>Add a credential</h1>{err}
<p>Name: <code>{html.escape(meta['name'])}</code> &middot; variable: <code>{html.escape(meta['env'])}</code></p>
<p>Requested for: {purpose}</p><p>Only these commands will receive it: {cmds}</p>
<p>{html.escape(meta.get('guidance') or '')}</p>{help_html}
<form method="post"><input type="hidden" name="csrf" value="{html.escape(meta['csrf'])}">
<label>Token<input type="password" name="value" autocomplete="off" required></label>
<fieldset><legend>How long should it stay?</legend>
<label><input type="radio" name="lifetime" value="session" checked> Until this runner ends</label><br>
<label><input type="radio" name="lifetime" value="1h"> 1 hour</label><br>
<label><input type="radio" name="lifetime" value="24h"> 24 hours</label></fieldset><p></p>
<button type="submit">Save</button></form>
<p>This link works once and expires {cr.REQUEST_TTL // 60} minutes after it was created. The value is never shown again.</p>""")


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = 'lab-cred'
    sys_version = ''

    def log_message(self, *args):  # never log request lines: they contain the one-time key
        pass

    def respond(self, status, body):
        self.send_response(status)
        for key, value in (('Content-Type', 'text/html; charset=utf-8'), ('Cache-Control', 'no-store'),
                           ('Referrer-Policy', 'no-referrer'), ('X-Content-Type-Options', 'nosniff'),
                           ('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'"),
                           ('Content-Length', str(len(body)))):
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def identity_ok(self):
        allowed = [x.strip() for x in os.environ.get('AGENT_CRED_ALLOWED_LOGIN', '').split(',') if x.strip()]
        return not allowed or self.headers.get('Tailscale-User-Login', '') in allowed

    def locate(self):
        match = ROUTE.match(self.path)
        if not match or not self.identity_ok():
            self.respond(404, page('<h1>Not found</h1>'))
            return None
        meta = cr.load_request(match[1])
        if not cr.request_usable(meta, match[2]):
            self.respond(410, page('<h1>This link is no longer valid</h1><p>Ask for a new one.</p>'))
            return None
        return meta

    def do_GET(self):
        meta = self.locate()
        if meta:
            meta['csrf'] = secrets.token_urlsafe(24)
            cr.save_request(meta)
            self.respond(200, form(meta))

    def do_POST(self):
        meta = self.locate()
        if not meta:
            return
        length = int(self.headers.get('Content-Length') or 0)
        if length <= 0 or length > MAX_BODY:
            return self.respond(400, page('<h1>Bad request</h1>'))
        fields = urllib.parse.parse_qs(self.rfile.read(length).decode('utf-8', 'replace'), keep_blank_values=True)
        get = lambda k: (fields.get(k) or [''])[0]
        if not meta.get('csrf') or not hmac.compare_digest(meta['csrf'], get('csrf')):
            return self.respond(400, page('<h1>Bad request</h1><p>Reload the page and try again.</p>'))
        meta['attempts'] += 1
        lifetime, value = get('lifetime'), get('value').strip()
        if meta['attempts'] >= MAX_ATTEMPTS:
            meta['state'] = 'locked'
            cr.save_request(meta)
            return self.respond(429, page('<h1>Too many attempts</h1><p>Ask for a new link.</p>'))
        if lifetime not in cr.LIFETIMES or not VALUE.fullmatch(value):
            cr.save_request(meta)
            return self.respond(400, form(meta, 'Enter the token and choose how long to keep it.'))
        ok, message = cr.validate_value(meta, value)
        if not ok:
            cr.save_request(meta)
            return self.respond(400, form(meta, message))
        cr.store_credential(meta, value, lifetime)
        meta.update(state='fulfilled', nonce_sha256='', csrf=None)
        cr.save_request(meta)
        self.respond(200, page(f'<h1>Saved</h1><p>The credential <code>{html.escape(meta["name"])}</code> is ready ({html.escape(message)}). '
                               'You can close this page and tell the assistant to continue.</p>'))


def pending_exists():
    for path in (cr.cred_dir() / 'requests').glob('*.json'):
        meta = cr.read_json(path)
        if meta and meta.get('state') == 'pending' and cr.now() <= meta['expires']:
            return True
    return False


def serve():
    os.umask(0o077)
    server = http.server.ThreadingHTTPServer(('127.0.0.1', cr.panel_port()), Handler)
    port = server.server_address[1]
    state = cr.cred_dir() / 'panel.json'
    cr.atomic(state, {'pid': os.getpid(), 'port': port, 'started': cr.now()})
    idle_limit = int(os.environ.get('AGENT_CRED_IDLE_SECONDS', '600'))

    def watch():
        last_busy = time.time()
        while True:
            time.sleep(5)
            if pending_exists():
                last_busy = time.time()
            elif time.time() - last_busy > idle_limit:
                server.shutdown()
                return
    threading.Thread(target=watch, daemon=True).start()
    try:
        server.serve_forever()
    finally:
        state.unlink(missing_ok=True)
        try:
            cr.tailscale_serve('off')
        except ValueError:
            pass


if __name__ == '__main__':
    if sys.argv[1:] == ['serve']:
        serve()
    else:
        print('usage: lab_cred_panel.py serve', file=sys.stderr)
        raise SystemExit(2)
