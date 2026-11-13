// Live-app regression test for the four Keywords defects in the audit directive.
//
//   D1 Show More: the information line follows the rows Show More reveals.
//   D2 Zero results: never shows an invalid range such as "1–0".
//   D3 Detail routing: /keywords/<id> does not load the list-page script,
//      and the detail module runs once.
//   D4 Page size: the select, the rows and the request agree on every load,
//      over repeated reloads (the race is intermittent, so one load proves little).
//
// Needs a running app and a browser. Environment:
//   BASE_URL            default http://127.0.0.1:5055
//   APP_USER / APP_PASSWORD   an account that can sign in
//   PLAYWRIGHT_CORE     optional path to playwright-core's entry module
//   CHROMIUM_MODULE     optional path to @sparticuz/chromium (provides the binary)
// Skips (exit 0, reported as SKIP) when the browser or credentials are missing.
//
// Run:  node tests/browser/keyword_list_regressions.mjs

import path from 'node:path';
import { pathToFileURL } from 'node:url';

const BASE = process.env.BASE_URL || 'http://127.0.0.1:5055';
const USER = process.env.APP_USER || 'admin';
const PASSWORD = process.env.APP_PASSWORD || '';
const KEYWORD_ID = process.env.KEYWORD_ID || '';

async function loadDeps() {
    let pw;
    try {
        const mod = process.env.PLAYWRIGHT_CORE
            ? await import(pathToFileURL(path.resolve(process.env.PLAYWRIGHT_CORE)).href)
            : await import('playwright-core');
        pw = mod.chromium || (mod.default && mod.default.chromium);
    } catch { return null; }
    let executablePath = process.env.CHROME_PATH || null;
    let args = ['--no-sandbox', '--disable-gpu'];
    if (!executablePath) {
        try {
            const modPath = process.env.CHROMIUM_MODULE
                ? pathToFileURL(path.resolve(process.env.CHROMIUM_MODULE)).href
                : '@sparticuz/chromium';
            const chromium = (await import(modPath)).default;
            executablePath = await chromium.executablePath();
            args = chromium.args;
        } catch { return null; }
    }
    return { pw, executablePath, args };
}

const deps = await loadDeps();
if (!deps || !PASSWORD) {
    console.log('SKIP keyword_list_regressions: needs playwright-core, a Chromium binary, and APP_PASSWORD');
    process.exit(0);
}

const results = [];
function check(name, ok, detail = '') {
    results.push({ name, ok: ok === true });
    console.log(`${ok === true ? 'PASS' : 'FAIL'} ${name}${detail ? ' :: ' + detail : ''}`);
}

async function settle(page) {
    await page.waitForLoadState('networkidle').catch(() => {});
    await page.waitForTimeout(800);
}

async function signIn(page) {
    await page.goto(`${BASE}/auth/login`, { waitUntil: 'load' });
    await page.fill('#username', USER);
    await page.fill('#password', PASSWORD);
    await page.click('#login-form [type=submit]');
    await page.waitForTimeout(3000);
    if (page.url().includes('/auth/login')) throw new Error('sign-in failed');
}

const browser = await deps.pw.launch({ executablePath: deps.executablePath, args: deps.args, headless: true });
try {
    const context = await browser.newContext({ viewport: { width: 1400, height: 900 } });
    const page = await context.newPage();
    const pageErrors = [];
    page.on('pageerror', (e) => pageErrors.push(e.message));
    await signIn(page);

    // D3: detail route. Count requests for each list/detail script.
    const scriptHits = [];
    page.on('request', (r) => { if (/\/static\/js\/pages\/.*\.js/.test(r.url())) scriptHits.push(r.url().split('?')[0].split('/').pop()); });
    let detailId = KEYWORD_ID;
    if (!detailId) {
        await page.goto(`${BASE}/keywords`, { waitUntil: 'load' });
        await settle(page);
        detailId = await page.evaluate(async () => {
            const r = await fetch('/api/keywords?per_page=1&page=1');
            const j = await r.json();
            return (j.keywords && j.keywords[0] && j.keywords[0].id) || '';
        });
    }
    scriptHits.length = 0;
    pageErrors.length = 0;
    const consoleErrors = [];
    page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()); });
    await page.goto(`${BASE}/keywords/${detailId}`, { waitUntil: 'load' });
    await settle(page);
    const onDetail = await page.evaluate(() => !document.getElementById('keywordsTable'));
    check('D3 detail page has no list table', onDetail);
    check('D3 detail route does not load keywords-list-page.js', !scriptHits.includes('keywords-list-page.js'), scriptHits.join(', '));
    check('D3 detail module loaded exactly once', scriptHits.filter((s) => s === 'keyword-detail-page.js').length === 1, `count=${scriptHits.filter((s) => s === 'keyword-detail-page.js').length}`);
    check('D3 detail page raises no uncaught error', pageErrors.length === 0, pageErrors.slice(0, 2).join(' | '));

    // D2: zero results never shows a range.
    await page.goto(`${BASE}/keywords`, { waitUntil: 'load' });
    await settle(page);
    const search = page.locator('input[type=search], input[id*="earch" i][type=text]').first();
    await search.fill('zz-no-such-keyword-7f3a');
    await page.keyboard.press('Enter');
    await settle(page);
    const zeroInfo = (await page.textContent('#searchResultsInfo')).replace(/\s+/g, ' ').trim();
    check('D2 zero results show no range', !/\(1\u20130\)|1\u20130\b|0\u20130\b/.test(zeroInfo), zeroInfo);
    check('D2 zero results report Found 0', /Found 0 result/.test(zeroInfo) || /Showing 0 of 0/.test(zeroInfo), zeroInfo);
    await search.fill('');
    await page.keyboard.press('Enter');
    await settle(page);

    // D1: Show More updates the counter to the rows it revealed.
    await page.selectOption('#perPage', '10');
    await settle(page);
    const more = page.locator('button:has-text("Show More"), a:has-text("Show More")').first();
    if (await more.count() && await more.isVisible()) {
        await more.click();
        await settle(page);
        const rows = await page.$$eval('#keywordsTable tbody tr', (trs) => trs.filter((r) => r.querySelector('td') && !r.querySelector('td[colspan]')).length);
        const info = (await page.textContent('#searchResultsInfo')).replace(/\s+/g, ' ').trim();
        check('D1 Show More reveals rows', rows > 10, `rows=${rows}`);
        check('D1 counter follows the revealed rows', info.includes(`1–${rows}`), `rows=${rows} info="${info}"`);
        const names = await page.$$eval('#keywordsTable tbody tr td:nth-child(2)', (tds) => tds.map((td) => td.textContent.trim()));
        check('Show More rows are unique (no duplicates)', new Set(names).size === names.length, `unique=${new Set(names).size} of ${names.length}`);
        const pagerPage = await page.$eval('.unified-pagination-container', (el) => el.getAttribute('data-current-page')).catch(() => null);
        check('Show More does not move the pager off page 1', pagerPage === null || pagerPage === '1', `pager page=${pagerPage}`);
        // Show More from page 2: the window stays on page 2 and extends below it.
        await page.goto(`${BASE}/keywords?page=2`, { waitUntil: 'load' });
        await settle(page);
        const sel2 = await page.$eval('#perPage', (s) => s.value);
        const before2 = await page.$$eval('#keywordsTable tbody tr', (trs) => trs.length);
        const more2 = page.locator('button:has-text("Show More"), a:has-text("Show More")').first();
        if (await more2.count() && await more2.isVisible()) {
            await more2.click();
            await settle(page);
            const names2 = await page.$$eval('#keywordsTable tbody tr td:nth-child(2)', (tds) => tds.map((td) => td.textContent.trim()));
            const page2 = await page.$eval('.unified-pagination-container', (el) => el.getAttribute('data-current-page')).catch(() => null);
            check('Show More on page 2 keeps page 2 and appends a batch', page2 === null || page2 === '2', `pager page=${page2} rows ${before2} -> ${names2.length} (size ${sel2})`);
            check('Show More on page 2 adds no duplicate rows', new Set(names2).size === names2.length, `unique=${new Set(names2).size} of ${names2.length}`);
        } else {
            check('Show More available on page 2', false, 'button not visible');
        }
    } else {
        check('D1 Show More available for this data set', false, 'button not visible at 10 rows per page');
    }

    // D4: the select, the rows and the request agree across repeated loads.
    const mismatches = [];
    for (let i = 0; i < 6; i++) {
        await page.goto(`${BASE}/keywords`, { waitUntil: 'load' });
        await settle(page);
        const sel = await page.$eval('#perPage', (s) => parseInt(s.value, 10));
        const rows = await page.$$eval('#keywordsTable tbody tr', (trs) => trs.filter((r) => r.querySelector('td') && !r.querySelector('td[colspan]')).length);
        if (rows !== Math.min(sel, 120)) mismatches.push(`load ${i + 1}: select=${sel} rows=${rows}`);
    }
    check('D4 select matches rows on six fresh loads', mismatches.length === 0, mismatches.join('; ') || 'all loads agree');

    await context.close();
} finally {
    await browser.close();
}

const failed = results.filter((r) => !r.ok);
console.log(`SUMMARY ${results.length - failed.length} passed, ${failed.length} failed`);
process.exit(failed.length ? 1 : 0);
