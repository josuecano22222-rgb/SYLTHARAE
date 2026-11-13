// Browser lifecycle test for static/js/pages/charts-dashboard-page.js.
//
// Loads the real module (and chart.umd.js) in headless Chromium, with every
// element id the module references created in the DOM and /api/* answered by
// fixtures. The words endpoint can be held back to simulate a slow response.
// Covers:
//   C1 the same DOM booted by two module copies (universal init + swap)  -> one request each
//   C2 a visit replaced while its words response is in flight           -> no late render, no page error
//   C3 the page left (DOM without this section) while a response is open -> no page error
// Fails on any uncaught exception and on any console error about a null element.
//
// Run:   node tests/browser/charts_dashboard_lifecycle.mjs
// Needs: puppeteer-core plus CHROME_PATH (a Chromium executable) or @sparticuz/chromium.
//        Without either the test skips with exit 0.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const MODULE = path.join(ROOT, 'static/js/pages/charts-dashboard-page.js');

async function loadDeps() {
    let puppeteer;
    try { puppeteer = (await import('puppeteer-core')).default; } catch { return null; }
    let executablePath = process.env.CHROME_PATH || null;
    let args = ['--no-sandbox', '--disable-gpu'];
    if (!executablePath) {
        try {
            const chromium = (await import('@sparticuz/chromium')).default;
            executablePath = await chromium.executablePath();
            args = chromium.args;
        } catch { return null; }
    }
    return { puppeteer, executablePath, args };
}

const deps = await loadDeps();
if (!deps) {
    console.log('SKIP charts_dashboard_lifecycle: needs puppeteer-core plus CHROME_PATH or @sparticuz/chromium');
    process.exit(0);
}

// Every quoted kebab-case id the module uses becomes an element of the right kind.
const source = fs.readFileSync(MODULE, 'utf8');
const ids = [...new Set([...source.matchAll(/['"`]([a-z][a-z0-9]*(?:-[a-z0-9]+)+)['"`]/g)].map((m) => m[1]))]
    .filter((id) => !id.startsWith('/') && !/^(text|application|multi)-/.test(id) && !id.includes('.'));
function elementFor(id) {
    if (/-count$|-count-|-size$|-rate$/.test(id) && !/-chart$/.test(id)) return `<span id="${id}"></span>`;
    if (/filter/.test(id)) return `<select id="${id}" multiple></select>`;
    if (/-chart(-[a-z]+)?$/.test(id) || /-chart-(count|size|rate)$/.test(id)) return `<div class="chart-container-layout"><canvas id="${id}"></canvas></div>`;
    return `<div id="${id}"></div>`;
}
const section = (inner) => `<div class="chart-section" id="section-words"><div class="chart-header"><h3></h3></div>${inner}</div>`;
const pageData = '<script type="application/json" id="charts-dashboard-page-data">{"translations":{}}</script>';
const FULL_DOM = `${pageData}${ids.map(elementFor).join('')}`;
const HTML = `<!doctype html><html><head><meta charset="utf-8"></head><body>
<div id="mainContent">${FULL_DOM}</div>
<script src="/static/js/chart.umd.js"></script>
<script type="module">
  import init from '/static/js/pages/charts-dashboard-page.js';
  window.__init = init;
  init();                                     // universal initializer
  document.dispatchEvent(new Event('DOMContentLoaded')); // full-load listener (readyState is already complete)
  window.__sameDomCopy = () => import('/static/js/pages/charts-dashboard-page.js?swap=R1').then((m) => m.default());
</script></body></html>`;

const counts = {};
const errors = [];
let wordsDelayMs = 0;
const wordsDelayQueue = [];   // per words request: delay in ms (consumed in order)
const FIXTURE = {
    '/api/categories': [{ id: 1, name: 'history' }],
    '/api/sources': [{ id: 1, name: 'src' }],
    '/api/sides': [{ id: 1, name: 'side' }],
    '/api/keywords?per_page=100&page=1': { success: true, keywords: [{ id: 1, name: 'k' }] },
    '/api/analytics/file-type-distribution': { types: [] },
};

const browser = await deps.puppeteer.launch({ executablePath: deps.executablePath, args: deps.args, headless: true });
const page = await browser.newPage();
await page.setRequestInterception(true);
page.on('pageerror', (e) => errors.push('PAGEERROR: ' + e.message));
page.on('console', (m) => { if (m.type() === 'error') errors.push('CONSOLE: ' + m.text().split('\n')[0]); });
page.on('request', async (req) => {
    const url = new URL(req.url());
    if (url.host !== 'harness.test') return req.continue();
    if (url.pathname === '/') return req.respond({ status: 200, contentType: 'text/html', body: HTML });
    if (url.pathname.startsWith('/static/')) {
        const file = path.join(ROOT, 'static', url.pathname.slice('/static/'.length));
        if (!fs.existsSync(file)) return req.respond({ status: 404, body: '' });
        return req.respond({ status: 200, contentType: file.endsWith('.js') ? 'application/javascript' : 'text/plain', body: fs.readFileSync(file) });
    }
    if (url.pathname.startsWith('/api/')) {
        const key = url.pathname + url.search;
        counts[key] = (counts[key] || 0) + 1;
        let body = FIXTURE[key] ?? FIXTURE[url.pathname];
        if (url.pathname === '/api/dashboard/words') {
            const delay = wordsDelayQueue.length ? wordsDelayQueue.shift() : wordsDelayMs;
            // A delayed (stale) response carries three words, a fresh one carries one.
            const n = delay ? 3 : 1;
            if (delay) await new Promise((r) => setTimeout(r, delay));
            body = { success: true, words: Array.from({ length: n }, (_, i) => ({ word: 'w' + i, file_count: 1 })), unsorted_words: [], words_by_category: [] };
        }
        if (body === undefined) body = { success: true };
        return req.respond({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
    }
    return req.respond({ status: 404, body: '' });
});

const settle = (ms) => new Promise((r) => setTimeout(r, ms));
const wordsCount = () => Object.entries(counts).filter(([k]) => k.startsWith('/api/dashboard/words')).reduce((a, [, v]) => a + v, 0);
// Resolves once the server has received at least n words requests, so a held
// request is genuinely in flight before the next visit starts.
const waitForWords = async (n) => {
    for (let i = 0; i < 250; i++) {
        if (wordsCount() >= n) return;
        await settle(20);
    }
    throw new Error('words request ' + n + ' never arrived');
};
const bad = () => errors.filter((e) => /null|undefined|Cannot|not a function/.test(e));
const results = [];
const check = (label, ok, detail) => results.push({ label, ok, detail });

try {
    await page.goto('http://harness.test/', { waitUntil: 'load' });
    await page.waitForFunction(() => typeof window.__init === 'function', { timeout: 15000 });
    await settle(1500);
    const first = Object.entries(counts).filter(([k]) => k.startsWith('/api/dashboard/') || k.startsWith('/api/categories')).map(([k, v]) => [k, v]);
    check('C1 one request per section when two copies boot the same DOM', first.length > 0 && first.every(([, v]) => v === 1), { counts: first, errors: bad() });

    // C2: visit A's words request is held at the server; visit B then replaces the
    // DOM with a complete page and boots its own run. A's late response must not render.
    for (const k of Object.keys(counts)) delete counts[k];
    errors.length = 0;
    wordsDelayQueue.push(2500);
    await page.evaluate((dom) => {
        document.getElementById('mainContent').innerHTML = dom;
        import('/static/js/pages/charts-dashboard-page.js?swap=A').then((m) => m.default());
    }, FULL_DOM);
    await waitForWords(1);                   // A's request is now in flight at the server
    await page.evaluate(async (dom) => {
        document.getElementById('mainContent').innerHTML = dom;   // visit B: new nodes
        const m = await import('/static/js/pages/charts-dashboard-page.js?swap=B');
        m.default();
    }, FULL_DOM);
    await settle(3500);                      // A's held response arrives here
    // The held response carries three words; the fresh visit carries one. A stale render would leave three.
    const labels = await page.evaluate(() => { const c = window.Chart && window.Chart.getChart(document.getElementById('words-chart')); return c ? c.data.labels.length : null; });
    check('C2 late response from the replaced visit is not rendered', bad().length === 0 && labels === 1, { errors: bad().slice(0, 5), chartLabels: labels });

    // C3: visit A's words request is held; the page is then replaced by a page with no charts section.
    for (const k of Object.keys(counts)) delete counts[k];
    errors.length = 0;
    wordsDelayQueue.push(2500);
    await page.evaluate((dom) => {
        document.getElementById('mainContent').innerHTML = dom;
        import('/static/js/pages/charts-dashboard-page.js?swap=C').then((m) => m.default());
    }, FULL_DOM);
    await waitForWords(1);
    await page.evaluate(() => { document.getElementById('mainContent').innerHTML = '<p id="other-page">reports</p>'; });
    await settle(3000);
    check('C3 response after leaving the page is dropped quietly', bad().length === 0, { errors: bad().slice(0, 5) });
} finally {
    await browser.close();
}

let failed = 0;
for (const r of results) {
    console.log(`${r.ok ? 'ok  ' : 'FAIL'} ${r.label} ${JSON.stringify(r.detail).slice(0, 700)}`);
    if (!r.ok) failed++;
}
console.log(`${results.length - failed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
