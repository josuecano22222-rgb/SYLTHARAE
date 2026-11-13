#!/usr/bin/env python
"""Drive every registered interface in a real browser and record what happens.

Runs against a live server (``BASE_URL``, default ``http://127.0.0.1:5000``)
using the Chromium bundle installed from npm (``@sparticuz/chromium`` +
``playwright-core``), which is the only source reachable from this sandbox.
For each interface route it records: HTTP status, console errors, uncaught page
errors, failed requests, a screenshot, and whether the page rendered any
content.  Results: ``audit_report/browser/walk.json`` + screenshots.

Usage::

    python tools/audit/browser_walk.py --base http://127.0.0.1:5000 \
        --user admin --password-file /tmp/.../initial_admin_password.txt
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "audit_report" / "browser"
NODE_DIR = pathlib.Path("/tmp/pw")

SCRIPT = r"""
import chrom from '@sparticuz/chromium';
import { chromium } from 'playwright-core';
import fs from 'fs';

const cfg = JSON.parse(process.env.WALK_CONFIG);
const exe = await chrom.executablePath();
const browser = await chromium.launch({
  executablePath: exe,
  args: [...chrom.args, '--no-sandbox', '--disable-dev-shm-usage'],
  headless: true,
});
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();
const results = [];
const shots = cfg.outDir;
fs.mkdirSync(shots, { recursive: true });

page.on('console', (m) => {
  if (m.type() === 'error') (page._errors ||= []).push(m.text().slice(0, 300));
});
page.on('pageerror', (e) => { (page._pageErrors ||= []).push(String(e).slice(0, 300)); });
page.on('requestfailed', (r) => {
  const t = r.failure()?.errorText || '';
  if (!t.includes('net::ERR_ABORTED')) (page._failed ||= []).push(`${r.method()} ${r.url()} ${t}`.slice(0, 300));
});

// sign in
await page.goto(cfg.base + '/auth/login', { waitUntil: 'domcontentloaded' });
const user = await page.$('input[name="username"], #username');
if (user) {
  await page.fill('input[name="username"], #username', cfg.user);
  await page.fill('input[name="password"], #password', cfg.password);
  await Promise.all([
    page.waitForLoadState('domcontentloaded'),
    page.click('button[type="submit"], button.btn-primary'),
  ]);
  await page.waitForTimeout(1500);
}
const afterLogin = page.url();

for (const route of cfg.routes) {
  page._errors = []; page._pageErrors = []; page._failed = [];
  let status = null, error = null;
  try {
    const resp = await page.goto(cfg.base + route, { waitUntil: 'domcontentloaded', timeout: 45000 });
    status = resp ? resp.status() : null;
    await page.waitForTimeout(700);
  } catch (e) { error = String(e).slice(0, 200); }
  const bodyLen = await page.evaluate(() => document.body ? document.body.innerText.trim().length : 0).catch(() => 0);
  const title = await page.title().catch(() => '');
  const h1 = await page.evaluate(() => {
    const el = document.querySelector('h1');
    if (!el) return '';
    return (el.innerText || el.textContent || '').trim().slice(0, 120);
  }).catch(() => '');
  const shot = route.replace(/[^a-z0-9]+/gi, '_') || 'root';
  await page.screenshot({ path: `${shots}/${shot}.png`, fullPage: false }).catch(() => {});
  results.push({
    route, status, title, h1, bodyLen, error,
    url: page.url(),
    consoleErrors: page._errors.slice(0, 6),
    pageErrors: page._pageErrors.slice(0, 6),
    failedRequests: page._failed.slice(0, 6),
    screenshot: `${shot}.png`,
  });
  console.log(`WALK ${route} status=${status} text=${bodyLen} h1=${JSON.stringify(h1)} errors=${(page._errors||[]).length + (page._pageErrors||[]).length}`);
}
fs.writeFileSync(cfg.resultFile, JSON.stringify({ afterLogin, results }, null, 2));
await browser.close();
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:5000")
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password-file", required=True)
    args = ap.parse_args()

    text = pathlib.Path(args.password_file).read_text(encoding="utf-8")
    password = ""
    for line in text.splitlines():
        if line.startswith("password="):
            password = line.split("=", 1)[1].strip()
    if not password:
        raise SystemExit("no password in the given file")

    from core.interfaces import registry
    from apps.web.app import app

    # ``interface.route`` is an endpoint name (``files.files_list``), not a
    # URL. Browsing the name would request a path Flask may answer with a
    # catch-all or a redirect, which is why the first walk produced the same
    # 463 characters for every "page". Resolve through the url map.
    url_by_endpoint = {r.endpoint: r.rule for r in app.url_map.iter_rules()}
    routes = []
    unresolved = []
    for entry in registry.REGISTRY:
        rule = url_by_endpoint.get(entry.route)
        if not rule or "<" in rule:
            unresolved.append(entry.route)
            continue
        routes.append(rule)
    routes = sorted(set(routes))
    print(f"interfaces: {len(registry.REGISTRY)}  urls: {len(routes)}  "
          f"unresolved: {unresolved}")

    OUT.mkdir(parents=True, exist_ok=True)
    (NODE_DIR / "walk.mjs").write_text(SCRIPT, encoding="utf-8")
    cfg = {"base": args.base, "user": args.user, "password": password,
           "routes": routes, "outDir": str(OUT / "screenshots"),
           "resultFile": str(OUT / "walk.json")}
    import os

    env = dict(os.environ, WALK_CONFIG=json.dumps(cfg))
    proc = subprocess.run(["node", str(NODE_DIR / "walk.mjs")], cwd=NODE_DIR,
                          env=env, capture_output=True, text=True, timeout=1800)
    print(proc.stdout[-6000:])
    if proc.returncode != 0:
        print(proc.stderr[-3000:], file=sys.stderr)
    data = json.loads((OUT / "walk.json").read_text(encoding="utf-8"))
    bad = [r for r in data["results"]
           if (r["status"] or 500) >= 400 or r["pageErrors"] or r["error"]
           or r["bodyLen"] < 20]
    print(f"routes walked: {len(data['results'])}  problems: {len(bad)}")
    for r in bad:
        print(f"  {r['route']} status={r['status']} text={r['bodyLen']} "
              f"url={r['url']} errors={r['pageErrors']} {r['error'] or ''}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
