// Browser lifecycle test for the dashboard page module (static/js/pages/dashboard-page.js).
//
// Serves the real module, chart.umd.js and progress tracker through request
// interception; /api/analytics/* answered by fixtures. Covers:
//   S0 full load (module import + DOMContentLoaded + universal init)  -> one boot
//   S1 init() again on the same DOM                                  -> no boot
//   S4 module re-run on the same DOM (cache-busted copy)             -> no boot
//   S2 three soft navigations (new island each time)                 -> one boot each
//   S3 stale in-flight response discarded in favour of the newest visit
// and fails on "Canvas is already in use" or any uncaught page error.
//
// Run:   node tests/browser/dashboard_lifecycle.mjs
// Needs: puppeteer-core and a Chromium binary. Set CHROME_PATH to a Chromium
//        executable, or install @sparticuz/chromium. Without either, the test skips.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');

async function loadDeps() {
    let puppeteer;
    try { puppeteer = (await import('puppeteer-core')).default; }
    catch { return null; }
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
    console.log('SKIP dashboard_lifecycle: needs puppeteer-core plus CHROME_PATH or @sparticuz/chromium');
    process.exit(0);
}

const CANVASES = ['categoryBarChart', 'categoryPieChart', 'contentCoverageChart', 'fileTypeChart', 'processingChart',
    'processingSpeedChart', 'storageChart', 'storageTimelineChart', 'successRateChart', 'timelineChart', 'topCategoriesChart'];
const HTML = `<!doctype html><html><head><meta charset="utf-8"></head><body>
<div id="mainContent"><script type="application/json" id="dashboard-page-data">{"translations":{}}</script>
${CANVASES.map((c) => `<canvas id="${c}"></canvas>`).join('')}
<div id="topWordsCloud"></div><div id="totalFiles"></div><div id="processedFiles"></div><div id="uniqueTypes"></div>
<div id="processingRate"></div><div id="databaseSize"></div><div id="storageSize"></div></div>
<script src="/static/js/chart.umd.js"></script>
<script type="module">
  import initDash from '/static/js/pages/dashboard-page.js';
  window.__init = initDash;
  initDash(); // the universal initializer also calls init() on a full load
  window.__swap = async (n) => {
    const main = document.getElementById('mainContent');
    const old = document.getElementById('dashboard-page-data'); if (old) old.remove();
    const data = document.createElement('script'); data.type = 'application/json'; data.id = 'dashboard-page-data';
    data.textContent = JSON.stringify({ translations: {} }); main.prepend(data);
    const m = await import('/static/js/pages/dashboard-page.js?swap=' + n);
    m.default();
  };
</script></body></html>`;

const FIXTURES = {
    '/api/analytics/dashboard-summary': { success: true, totalFiles: 10, processedFiles: 5, processingRate: 50, uniqueTypes: 3, databaseSize: 1000, recentFiles: 1, totalCategories: 1, totalKeywords: 1, totalWords: 1 },
};
const counts = {};
const errors = [];
const delayQueue = [];       // consumed one per timeline request
const timelineLog = [];
let timelineSeq = 0;

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
        const file = path.join(ROOT, 'static', url.pathname.replace('/static/', ''));
        if (!fs.existsSync(file)) { errors.push('MISSING ' + url.pathname); return req.respond({ status: 404, body: '' }); }
        return req.respond({ status: 200, contentType: file.endsWith('.js') ? 'application/javascript' : 'text/plain', body: fs.readFileSync(file) });
    }
    if (url.pathname.startsWith('/api/')) {
        counts[url.pathname] = (counts[url.pathname] || 0) + 1;
        let ms = 0;
        const seq = ++timelineSeq;
        if (url.pathname === '/api/analytics/timeline-data') {
            ms = delayQueue.length ? delayQueue.shift() : 0;
            timelineLog.push({ seq, delayMs: ms });
        }
        if (ms) await new Promise((r) => setTimeout(r, ms));
        let body = FIXTURES[url.pathname] || { success: false, error: 'no fixture' };
        if (url.pathname === '/api/analytics/timeline-data') body = { success: true, labels: ['req' + seq], fileCount: [1], processedCount: [1] };
        return req.respond({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
    }
    return req.respond({ status: 404, body: '' });
});

const settle = (ms) => new Promise((r) => setTimeout(r, ms));
const reset = () => { for (const k of Object.keys(counts)) delete counts[k]; errors.length = 0; };
const endpointCount = (name) => counts[name] || 0;
const DASH = ['/api/analytics/dashboard-summary', '/api/analytics/timeline-data',
    '/api/analytics/category-distribution', '/api/analytics/file-type-distribution'];

const results = [];
function check(label, ok, detail) { results.push({ label, ok, detail }); }
function onlyRealErrors() {
    return errors.filter((e) => !e.includes('no fixture') && !e.includes('Failed to load resource'));
}

try {
    reset();
    await page.goto('http://harness.test/', { waitUntil: 'load' });
    await page.waitForFunction(() => typeof window.__init === 'function', { timeout: 15000 });
    await settle(1200);
    const s0 = Object.fromEntries(DASH.map((u) => [u, endpointCount(u)]));
    check('S0 full load boots once', DASH.every((u) => s0[u] === 1), s0);

    reset();
    await page.evaluate(() => window.__init());
    await settle(1000);
    check('S1 init() again on same DOM makes no requests', DASH.every((u) => endpointCount(u) === 0), Object.fromEntries(DASH.map((u) => [u, endpointCount(u)])));

    reset();
    await page.evaluate(() => import('/static/js/pages/dashboard-page.js?swap=R1').then((m) => m.default()));
    await settle(1000);
    check('S4 cache-busted re-run on same DOM makes no requests', DASH.every((u) => endpointCount(u) === 0), Object.fromEntries(DASH.map((u) => [u, endpointCount(u)])));

    reset();
    for (let n = 1; n <= 3; n++) { await page.evaluate((n) => window.__swap(n), n); await settle(900); }
    check('S2 three soft navigations boot once each', DASH.every((u) => endpointCount(u) === 3), Object.fromEntries(DASH.map((u) => [u, endpointCount(u)])));

    reset();
    delayQueue.push(3000);   // the first timeline reply of the next visit is slow
    await page.evaluate(() => window.__swap(10));
    await settle(200);
    await page.evaluate(() => window.__swap(11));
    await settle(3800);
    const finalLabels = await page.evaluate(() => {
        const chart = window.Chart && window.Chart.getChart(document.getElementById('timelineChart'));
        return chart ? chart.data.labels : null;
    });
    const newest = timelineLog[timelineLog.length - 1];
    check('S3 stale response discarded; newest visit renders', !!newest && JSON.stringify(finalLabels) === JSON.stringify(['req' + newest.seq]),
        { finalLabels, newestRequest: newest });

    check('no "Canvas is already in use" or uncaught exception', onlyRealErrors().length === 0, onlyRealErrors().slice(0, 5));
} finally {
    await browser.close();
}

let failed = 0;
for (const r of results) {
    console.log(`${r.ok ? 'ok  ' : 'FAIL'} ${r.label}${r.ok ? '' : ' ' + JSON.stringify(r.detail)}`);
    if (!r.ok) failed++;
}
console.log(`${results.length - failed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
