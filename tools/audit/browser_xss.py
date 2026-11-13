#!/usr/bin/env python
"""Stored-XSS probe in a real browser.

The API returning ``application/json`` proves nothing about the pages that
draw the value: a name that reaches ``innerHTML`` runs. This probe stores a
payload through the public API, loads the page that lists it, and asks the
browser whether the payload executed (``window.__xss_executed``) or was
inserted as markup (an ``img`` element inside the rendered node).

Runs against a live server with the Chromium bundle installed from npm
(``@sparticuz/chromium`` + ``playwright-core``). Writes
``audit_report/findings/browser_xss.json``; exit 1 on any executed payload.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "audit_report" / "findings"
NODE_DIR = pathlib.Path("/tmp/pw")

SCRIPT = r"""
import chrom from '@sparticuz/chromium';
import { chromium } from 'playwright-core';

const cfg = JSON.parse(process.env.XSS_CONFIG);
const browser = await chromium.launch({
  executablePath: await chrom.executablePath(),
  args: [...chrom.args, '--no-sandbox', '--disable-dev-shm-usage'],
  headless: true,
});
const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
const out = { loggedIn: false, created: null, deleted: null, executed: null,
              markup: null, renderedText: null, consoleErrors: [] };
page.on('console', (m) => { if (m.type() === 'error') out.consoleErrors.push(m.text().slice(0, 200)); });
page.on('dialog', async (d) => { out.dialog = d.message(); await d.dismiss(); });

await page.goto(cfg.base + '/auth/login', { waitUntil: 'domcontentloaded' });
await page.fill('input[name="username"], #username', cfg.user);
await page.fill('input[name="password"], #password', cfg.password);
await Promise.all([
  page.waitForLoadState('domcontentloaded'),
  page.click('button[type="submit"], button.btn-primary'),
]);
await page.waitForTimeout(1200);
out.loggedIn = !page.url().includes('/auth/login');

// Store the payload through the same API the UI uses (CSRF from the meta tag).
out.created = await page.evaluate(async (name) => {
  const meta = document.querySelector('meta[name="csrf-token"]');
  const r = await fetch('/api/sources', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json',
               'X-CSRFToken': meta ? meta.content : '' },
    body: JSON.stringify({ name }),
  });
  let body = null;
  try { body = await r.json(); } catch (e) { body = null; }
  return { status: r.status, id: body && body.id };
}, cfg.payload);

await page.goto(cfg.base + cfg.page, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(2500);

out.executed = await page.evaluate(async () => {
  await new Promise((r) => setTimeout(r, 800));
  return window.__xss_executed === true;
});
out.markup = await page.evaluate((marker) => {
  const hits = [];
  for (const img of document.querySelectorAll('img[src="x"], img[src="x/"]')) {
    hits.push(img.outerHTML.slice(0, 200));
  }
  const text = document.body ? document.body.innerText : '';
  return { injectedNodes: hits, payloadVisibleAsText: text.includes(marker),
           scripts: document.querySelectorAll('script').length };
}, cfg.marker);
out.renderedText = out.markup.payloadVisibleAsText;

// remove the probe row so the dataset is not polluted with a test artifact
if (out.created && out.created.id) {
  out.deleted = await page.evaluate(async (id) => {
    const meta = document.querySelector('meta[name="csrf-token"]');
    const r = await fetch('/api/sources/' + id, {
      method: 'DELETE',
      headers: { 'X-CSRFToken': meta ? meta.content : '' },
    });
    return { status: r.status };
  }, out.created.id);
}

await browser.close();
console.log('XSS ' + JSON.stringify(out));
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:5000")
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password-file", required=True)
    ap.add_argument("--page", default="/sources")
    args = ap.parse_args()

    password = ""
    for line in pathlib.Path(args.password_file).read_text(encoding="utf-8").splitlines():
        if line.startswith("password="):
            password = line.split("=", 1)[1].strip()
    if not password:
        raise SystemExit("no password in the given file")

    marker = "XSSPROBE"
    payload = f'<img src=x onerror="window.__xss_executed=true"> {marker}'
    cfg = {"base": args.base, "user": args.user, "password": password,
           "page": args.page, "marker": marker, "payload": payload}

    (NODE_DIR / "xss.mjs").write_text(SCRIPT, encoding="utf-8")
    env = dict(os.environ, XSS_CONFIG=json.dumps(cfg))
    proc = subprocess.run(["node", str(NODE_DIR / "xss.mjs")], cwd=NODE_DIR,
                          env=env, capture_output=True, text=True, timeout=600)
    line = [l for l in proc.stdout.splitlines() if l.startswith("XSS ")]
    if proc.returncode != 0 or not line:
        print(proc.stdout[-2000:])
        print(proc.stderr[-3000:], file=sys.stderr)
        return 2
    result = json.loads(line[-1][4:])

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "browser_xss.json").write_text(json.dumps(result, indent=2), encoding="utf-8")

    print(json.dumps(result, indent=2))
    ok = (result["loggedIn"] and result["executed"] is False
          and not result["markup"]["injectedNodes"])
    print(f"\npayload executed: {result['executed']}  "
          f"injected nodes: {len(result['markup']['injectedNodes'])}  "
          f"visible as text: {result['renderedText']}")
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
