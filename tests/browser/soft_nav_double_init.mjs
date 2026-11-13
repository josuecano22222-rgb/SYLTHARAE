// Duplicate initialisation on soft navigation, for pages the universal
// initializer does not register (they go through navigation-swap's
// importUnregisteredPageModules path).
//
// That path imports the page module under a new URL, so the module's top-level
// self-run executes, and then the swap calls the module's default export on the
// same DOM. Each page must boot once per DOM and attach its listeners once.
//
// Covers: operations/input (ingestion-studio-page.js) and
//         analyst/categorization (analyst-categorization-page.js).
// Each endpoint of the page must be requested exactly once.
//
// Run:   node tests/browser/soft_nav_double_init.mjs
// Needs: puppeteer-core plus CHROME_PATH (a Chromium executable) or @sparticuz/chromium.
//        Without either the test skips with exit 0; a skip is not a pass.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');

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
    console.log('SKIP soft_nav_double_init: needs puppeteer-core plus CHROME_PATH or @sparticuz/chromium');
    process.exit(0);
}

const PAGES = [
    {
        label: 'operations/input',
        file: 'static/js/pages/ingestion-studio-page.js',
        endpoints: ['/api/input/sources', '/api/input/sides', '/api/input/options-info'],
    },
    {
        label: 'analyst/categorization',
        file: 'static/js/pages/analyst-categorization-page.js',
        endpoints: ['/api/analyst/assignments', '/api/analyst/log'],
    },
];

// Create every element the module asks for by id, with a kind that suits its use.
function domFor(source) {
    // ids the module reads directly, plus ids it passes to helpers (renderPickList('sourceList', ...))
    const direct = [...source.matchAll(/(?:\bel|getElementById)\(\s*['"]([A-Za-z][\w-]*)['"]/g)].map((m) => m[1]);
    const passed = [...source.matchAll(/['"]([a-z][A-Za-z]+(?:List|Display|Box|Filter|Chart|Count|Panel|Area|Input|Button|Status|Rail|Dock|Summary|Table|Body|Form|Container|Root|Cards?|Toolbar|Select|Log|Info|Note|Label))['"]/g)].map((m) => m[1]);
    const ids = [...new Set([...direct, ...passed])];
    return ids.map((id) => {
        if (/count|size|rate|total|label|info|status|display/i.test(id)) return `<span id="${id}"></span>`;
        if (/filter|select|picker/i.test(id)) return `<select id="${id}"></select>`;
        if (/^input|query|search|date|name/i.test(id)) return `<input id="${id}">`;
        return `<div id="${id}"></div>`;
    }).join('');
}

const pageRuns = [];
const browser = await deps.puppeteer.launch({ executablePath: deps.executablePath, args: deps.args, headless: true });

// Optional filter: node soft_nav_double_init.mjs analyst | ingestion (default: all)
const ONLY = process.argv[2] || "";
for (const pg of PAGES.filter((p) => !ONLY || p.file.includes(ONLY))) {
    const source = fs.readFileSync(path.join(ROOT, pg.file), 'utf8');
    const modulePath = '/' + pg.file;
    const island = pg.file.includes('ingestion') ? '<script type="application/json" id="ingestionPageData">{"strings":{}}</script>' : '';
    // btnStart must contain .ing-primary-label (renderDock writes into it)
    const dockButton = pg.file.includes('ingestion') ? '<button id="btnStart"><span class="ing-primary-label"></span></button>' : '';
    const body = `<div id="mainContent">${island}${dockButton}${domFor(source)}</div>`;
    const html = `<!doctype html><html><head><meta charset="utf-8"></head><body>${body}
<script type="module">
  // Full-load path: the module evaluates (self-run), then the swap calls its default export on the same DOM.
  window.__ready = import('${modulePath}?v=A').then(async (m) => { window.__mod = m; await m.default(); });
</script></body></html>`;

    const counts = {};
    const errors = [];
    const page = await browser.newPage();
    await page.setRequestInterception(true);
    page.on('pageerror', (e) => errors.push('PAGEERROR: ' + e.message + ' @ ' + String(e.stack).split('\n').slice(1,3).join(' | ')));
    page.on('console', (m) => { if (m.type() === 'error') errors.push('CONSOLE: ' + m.text().split('\n')[0]); });
    page.on('request', (req) => {
        const url = new URL(req.url());
        if (url.host !== 'harness.test') return req.continue();
        if (url.pathname === '/') return req.respond({ status: 200, contentType: 'text/html', body: html });
        if (url.pathname.startsWith('/static/')) {
            const file = path.join(ROOT, 'static', url.pathname.slice('/static/'.length));
            if (!fs.existsSync(file)) return req.respond({ status: 404, body: '' });
            return req.respond({ status: 200, contentType: 'application/javascript', body: fs.readFileSync(file) });
        }
        if (url.pathname.startsWith('/api/')) {
            counts[url.pathname] = (counts[url.pathname] || 0) + 1;
            return req.respond({ status: 200, contentType: 'application/json', body: JSON.stringify({ success: true, sources: [], sides: [], assignments: [], entries: [], logs: [], options: {}, total: 0, pagination: {}, data: [], categories: [] }) });
        }
        return req.respond({ status: 404, body: '' });
    });
    await page.goto('http://harness.test/', { waitUntil: 'load' });
    await page.waitForFunction(() => window.__ready !== undefined, { timeout: 15000 });
    await page.evaluate(() => window.__ready).catch((e) => errors.push('READY: ' + e.message));
    await new Promise((r) => setTimeout(r, 1500));
    pageRuns.push({ label: pg.label, endpoints: pg.endpoints, counts: Object.fromEntries(pg.endpoints.map((u) => [u, counts[u] || 0])), errors: errors.slice(0, 4) });
    await page.close();
}
await browser.close();

const results = [];
for (const run of pageRuns) {
    const once = run.endpoints.every((u) => run.counts[u] === 1);
    results.push({ label: `${run.label}: each endpoint requested once per DOM`, ok: once, detail: run.counts });
    results.push({ label: `${run.label}: no page error during boot`, ok: run.errors.filter((e) => !/Failed to load resource/.test(e)).length === 0, detail: run.errors });
}
let failed = 0;
for (const r of results) {
    console.log(`${r.ok ? 'ok  ' : 'FAIL'} ${r.label} ${JSON.stringify(r.detail).slice(0, 500)}`);
    if (!r.ok) failed++;
}
console.log(`${results.length - failed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
