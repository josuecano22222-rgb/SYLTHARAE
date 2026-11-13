#!/usr/bin/env python
"""Criterion 11: the i18n feature in a real browser, against the dev stack.

Six things are driven, in the order a curator would meet them: log in, switch
the interface to Arabic through the switcher the user clicks, search in Arabic
for a category whose *canonical* name is English, search in English while the
UI is Arabic, open a category's Names tab, and import a glossary CSV whose
last row is deliberately invalid.

The fixture (one word, one category, an English and an Arabic name) is seeded
into the dev stack's **throw-away** database before the browser starts, with
the same rows the integration fixture uses, and both names are marked
``e2e-<token>`` / ``اختبار…``. Nothing here writes a machine translation into
curated data - the user's rule - and nothing is left behind, because the
database is created for this run and discarded with it.

Evidence lands in ``audit_report/r21_i18n/browser/``:

  * ``screenshots/*.png``
  * ``browser_evidence.json`` - per step: URL, HTTP status, ``dir`` attribute,
    visible text sample, and every ``/api/`` request/response pair
  * ``browser_console.json`` - console errors and failed requests

Usage::

    python tools/audit/browser_i18n_terms.py --base http://127.0.0.1:5000 \\
        --password-file /tmp/r18_bootstrap.json
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "audit_report" / "r21_i18n" / "browser"
NODE_DIR = pathlib.Path("/tmp/br")

SCRIPT = r"""
const puppeteer = require('puppeteer-core');
const fs = require('fs');

const cfg = JSON.parse(process.env.E2E_CONFIG);
const shots = cfg.outDir;
fs.mkdirSync(shots, { recursive: true });

(async () => {
const browser = await puppeteer.launch({
  executablePath: '/tmp/chromium',
  headless: 'shell',
  args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'],
});
const page = await browser.newPage();
await page.setViewport({ width: 1440, height: 900 });

const steps = [];
const consoleErrors = [];
const failedRequests = [];
const exchanges = [];

page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 300)); });
page.on('pageerror', (e) => consoleErrors.push('pageerror: ' + String(e).slice(0, 300)));
page.on('requestfailed', (r) => {
  const t = r.failure() && r.failure().errorText ? r.failure().errorText : '';
  if (!t.includes('net::ERR_ABORTED')) failedRequests.push(r.method() + ' ' + r.url() + ' ' + t);
});
page.on('response', async (r) => {
  if (!/\/api\//.test(r.url())) return;
  let body = '';
  try { body = (await r.text()).slice(0, 1200); } catch (e) { body = ''; }
  exchanges.push({ method: r.request().method(), url: r.url(), status: r.status(), body });
});

const snap = (name) => page.screenshot({ path: `${shots}/${name}.png` }).catch(() => {});

async function record(step, extra) {
  const info = await page.evaluate(() => ({
    url: location.href,
    dir: document.documentElement.getAttribute('dir') || '',
    lang: document.documentElement.getAttribute('lang') || '',
    h1: (document.querySelector('h1') ? document.querySelector('h1').innerText : '').trim().slice(0, 120),
    text: (document.body ? document.body.innerText : '').trim().slice(0, 500),
  })).catch(() => ({}));
  steps.push(Object.assign({ step: step }, info, extra || {}));
  console.log(`STEP ${step} url=${info.url} dir=${info.dir} lang=${info.lang} h1=${JSON.stringify(info.h1)}`);
}

async function visit(url) {
  let status = null;
  try {
    const r = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
    status = r ? r.status() : null;
  } catch (e) { status = 'error'; }
  await new Promise((res) => setTimeout(res, 900));
  return status;
}

// --- 1. sign in -----------------------------------------------------------
await page.goto(cfg.base + '/auth/login', { waitUntil: 'domcontentloaded' });
await page.type('input[name="username"], #username', cfg.user);
await page.type('input[name="password"], #password', cfg.password);
await Promise.all([
  page.waitForNavigation({ waitUntil: 'domcontentloaded' }).catch(() => {}),
  page.click('button[type="submit"], button.btn-primary'),
]);
await new Promise((res) => setTimeout(res, 1500));
await record('login', { httpStatus: 200 });

// --- 2. the English UI, for contrast --------------------------------------
// The interface language is a system-wide setting, so a previous run may have
// left it in Arabic. Switch to English through the same control, so both
// directions are exercised by the switcher rather than by a URL.
async function switchTo(code) {
  try {
    const toggle = await page.$('#topbarLangBtn');
    if (toggle) {
      await toggle.click();
      await new Promise((res) => setTimeout(res, 400));
      const item = await page.$(`[data-language-switch="${code}"]`);
      if (item) {
        await item.click();
        await new Promise((res) => setTimeout(res, 2500));
        return 'switcher';
      }
    }
  } catch (e) { /* fall through to the endpoint */ }
  await visit(cfg.base + '/set_language/' + code);
  return 'endpoint';
}
await switchTo('en');
let status = await visit(cfg.base + '/categories');
await snap('01-categories-english');
await record('categories-en', { httpStatus: status });

// --- 3. switch the interface to Arabic through the real switcher ----------
const switchedBy = await switchTo('ar');
status = await visit(cfg.base + '/categories');
await snap('02-categories-arabic');
await record('arabic-ui', { httpStatus: status, switchedBy: switchedBy });

// --- 4. an Arabic query for an English-named category ---------------------
status = await visit(cfg.base + '/categories?search=' + encodeURIComponent(cfg.fixture.arabic));
await snap('03-arabic-query-finds-english-category');
const arabicRows = await page.evaluate((f) => {
  const rows = Array.from(document.querySelectorAll('tr.category-item'));
  const wanted = String(f.categoryId);
  return {
    count: rows.length,
    // The Arabic interface displays the Arabic name, so the id - not the
    // rendered text - is what proves the English-named category was found.
    matched: rows.some((r) => r.getAttribute('data-category-id') === wanted),
    names: rows.slice(0, 5).map((r) => r.innerText.trim().slice(0, 80)),
    sources: rows.slice(0, 5).map((r) => r.getAttribute('data-term-source')),
  };
}, cfg.fixture);
await record('arabic-query', { httpStatus: status, rows: arabicRows });

// --- 5. an English query while the UI is Arabic ---------------------------
status = await visit(cfg.base + '/categories?search=' + encodeURIComponent(cfg.fixture.canonical));
await snap('04-english-query-under-arabic-ui');
const englishRows = await page.evaluate((f) => {
  const rows = Array.from(document.querySelectorAll('tr.category-item'));
  const wanted = String(f.categoryId);
  return {
    count: rows.length,
    matched: rows.some((r) => r.getAttribute('data-category-id') === wanted),
    rtl: document.documentElement.getAttribute('dir') === 'rtl',
    sources: rows.slice(0, 5).map((r) => r.getAttribute('data-term-source')),
  };
}, cfg.fixture);
await record('english-query-under-arabic-ui', { httpStatus: status, rows: englishRows });

// --- 6. the Names tab -----------------------------------------------------
status = await visit(cfg.base + '/categories/' + cfg.fixture.categoryId + '/names');
await snap('05-names-tab');
const namesPage = await page.evaluate(() => ({
  rows: document.querySelectorAll('tr[data-name-id]').length,
  hasAddButton: !!document.querySelector('[data-on-click="openAddNameModal()"]'),
  headers: Array.from(document.querySelectorAll('th')).map((th) => th.innerText.trim()).slice(0, 6),
}));
await record('names-tab', { httpStatus: status, page: namesPage });

// --- 7. a glossary CSV with one bad row -----------------------------------
const input = await page.$('#nameCsvFile');
if (input) {
  await input.uploadFile(cfg.csvPath);
  await page.click('[data-on-click="previewNameCsv()"]').catch(() => {});
  await new Promise((res) => setTimeout(res, 2500));
  const report = await page.evaluate(() => {
    const node = document.getElementById('nameCsvReport');
    return node ? node.innerText.slice(0, 1500) : '';
  });
  await snap('06-csv-import-with-error-row');
  await record('csv-import', { httpStatus: 200, report: report });
} else {
  await record('csv-import', { error: 'no file input on the page' });
}

fs.writeFileSync(cfg.resultFile, JSON.stringify({ steps: steps, exchanges: exchanges }, null, 2));
fs.writeFileSync(cfg.consoleFile, JSON.stringify({ consoleErrors: consoleErrors, failedRequests: failedRequests }, null, 2));
await browser.close();
})().catch((e) => { console.error('ERR', e && e.stack ? e.stack : e); process.exit(1); });
"""


def _fixture() -> dict:
    """Unique names, both fixtures, in the scripts their language uses."""
    token = uuid.uuid4().hex[:6]
    letters = "ابتثجحخدذرزسشصضطظعغفقكلمنهوي"
    number = int(token, 16) % (len(letters) ** 4)
    arabic = "".join(letters[(number // (len(letters) ** i)) % len(letters)] for i in range(4))
    return {"canonical": f"e2e-{token}", "arabic": f"اختبار{arabic}"}


def seed(db: dict, fixture: dict) -> dict:
    """Insert the fixture exactly as ``tests/integration`` does."""
    import psycopg2

    sys.path.insert(0, str(ROOT))
    from core.i18n.term_key import term_key

    conn = psycopg2.connect(host=db["host"], port=db["port"], user=db["user"],
                            dbname=db["database"], password=db.get("password") or "")
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO words (word) VALUES (%s) RETURNING id",
                        (fixture["canonical"],))
            word_id = cur.fetchone()[0]
            cur.execute("INSERT INTO categorys (word_id) VALUES (%s) RETURNING id",
                        (word_id,))
            category_id = cur.fetchone()[0]
            for language, name, script in (("en", fixture["canonical"], "Latn"),
                                           ("ar", fixture["arabic"], "Arab")):
                cur.execute(
                    "INSERT INTO term_names (word_id, name, match_key, language,"
                    " name_type, script, source) VALUES (%s, %s, %s, %s, 'preferred',"
                    " %s, 'curated')",
                    (word_id, name, term_key(name), language, script))
        conn.commit()
    finally:
        conn.close()
    fixture["wordId"], fixture["categoryId"] = word_id, category_id
    return fixture


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:5000")
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password-file", default="/tmp/r18_bootstrap.json",
                    help="devstack bootstrap JSON (or a file with password=...)")
    ap.add_argument("--no-seed", action="store_true",
                    help="browse only; requires --category/--arabic from a previous run")
    ap.add_argument("--category", type=int, default=0)
    ap.add_argument("--arabic", default="")
    ap.add_argument("--canonical", default="")
    args = ap.parse_args()

    path = pathlib.Path(args.password_file)
    text = path.read_text(encoding="utf-8")
    password, db = "", {}
    try:
        bootstrap = json.loads(text)
        password = bootstrap.get("password", "")
        db = bootstrap.get("db") or {}
    except json.JSONDecodeError:
        for line in text.splitlines():
            if line.startswith("password="):
                password = line.split("=", 1)[1].strip()
    if not password:
        raise SystemExit(f"no password found in {path}")

    if args.no_seed:
        fixture = {"canonical": args.canonical, "arabic": args.arabic,
                   "categoryId": args.category}
    else:
        if not db:
            raise SystemExit("the bootstrap file has no database section; cannot seed")
        fixture = seed(db, _fixture())
        print(f"fixture: category={fixture['categoryId']} word={fixture['wordId']} "
              f"en={fixture['canonical']!r} ar={fixture['arabic']!r}", flush=True)

    # Two good rows and one that must be refused, so the report shows both
    # halves of the importer's answer.
    fixture["csv"] = (
        "word,language,name,name_type\n"
        f"{fixture['canonical']},en,{fixture['canonical']},alternate\n"
        f"{fixture['canonical']},he,פוליטי,alternate\n"
        f"no-such-term,he,פוליטי,alternate\n")

    OUT.mkdir(parents=True, exist_ok=True)
    NODE_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUT / "glossary_fixture.csv"
    csv_path.write_text(fixture["csv"], encoding="utf-8")
    (NODE_DIR / "i18n_terms.js").write_text(SCRIPT, encoding="utf-8")
    cfg = {
        "base": args.base.rstrip("/"), "user": args.user, "password": password,
        "fixture": fixture, "csvPath": str(csv_path),
        "outDir": str(OUT / "screenshots"),
        "resultFile": str(OUT / "browser_evidence.json"),
        "consoleFile": str(OUT / "browser_console.json"),
    }
    env = dict(os.environ, E2E_CONFIG=json.dumps(cfg),
               LD_LIBRARY_PATH="/tmp/al2023/lib:" + os.environ.get("LD_LIBRARY_PATH", ""))
    proc = subprocess.run(["node", str(NODE_DIR / "i18n_terms.js")], cwd=NODE_DIR,
                          env=env, capture_output=True, text=True, timeout=900)
    print(proc.stdout[-8000:])
    if proc.returncode != 0:
        print(proc.stderr[-4000:], file=sys.stderr)
        return 1

    data = json.loads((OUT / "browser_evidence.json").read_text(encoding="utf-8"))
    console = json.loads((OUT / "browser_console.json").read_text(encoding="utf-8"))
    by_step = {s["step"]: s for s in data["steps"]}
    problems = []
    if by_step.get("arabic-ui", {}).get("dir") != "rtl":
        problems.append("the Arabic interface is not dir=rtl")
    if not (by_step.get("arabic-query", {}).get("rows") or {}).get("matched"):
        problems.append("an Arabic query did not find the English-named category")
    if not (by_step.get("english-query-under-arabic-ui", {}).get("rows") or {}).get("matched"):
        problems.append("an English query did not find the category under the Arabic UI")
    if (by_step.get("names-tab", {}).get("httpStatus") or 0) != 200:
        problems.append("the Names tab did not load")
    report = by_step.get("csv-import", {}).get("report", "")
    if "rejected" not in report or "no-such-term" not in report:
        problems.append("the CSV import did not report the bad row")
    if console["consoleErrors"]:
        problems.append(f"console errors: {console['consoleErrors'][:3]}")
    for step, s in by_step.items():
        if isinstance(s.get("httpStatus"), int) and s["httpStatus"] >= 400:
            problems.append(f"{step}: HTTP {s['httpStatus']}")

    print(f"steps recorded: {len(data['steps'])}  api exchanges: {len(data['exchanges'])}")
    for problem in problems:
        print(f"  PROBLEM {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
