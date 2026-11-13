// Live-app regression test for the file detail page.
//
//   F1 /file/<multi-page id> returns 200 (was HTTP 500: stale `show_more` keyword)
//   F2 pagination on that page moves to page 2 and keeps the file
//   F3 "Chart canvas is required" is never raised, and charts are not duplicated
//   F4 chart type change does not raise an uncaught error
//   F5 a file with no content chunks renders a page, not a crash
//
// Needs a running app and a browser. Environment:
//   BASE_URL, APP_USER, APP_PASSWORD (required)
//   MULTI_PAGE_FILE_ID  file whose content spans several pages (default 316)
//   EMPTY_FILE_ID       file with no content chunks (default 20)
//   PLAYWRIGHT_CORE / CHROMIUM_MODULE optional, as in the other browser tests
// Skips (exit 0) when the browser or credentials are missing.
//
// Run:  node tests/browser/file_detail_regressions.mjs

import path from 'node:path';
import { pathToFileURL } from 'node:url';

const BASE = process.env.BASE_URL || 'http://127.0.0.1:5055';
const USER = process.env.APP_USER || 'admin';
const PASSWORD = process.env.APP_PASSWORD || '';
const MULTI = process.env.MULTI_PAGE_FILE_ID || '316';
const EMPTY = process.env.EMPTY_FILE_ID || '20';

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
    console.log('SKIP file_detail_regressions: needs playwright-core, a Chromium binary, and APP_PASSWORD');
    process.exit(0);
}

const results = [];
function check(name, ok, detail = '') {
    results.push({ name, ok: ok === true });
    console.log(`${ok === true ? 'PASS' : 'FAIL'} ${name}${detail ? ' :: ' + detail : ''}`);
}

async function settle(page) {
    await page.waitForLoadState('networkidle').catch(() => {});
    await page.waitForTimeout(900);
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
    const consoleErrors = [];
    const failedRequests = [];
    page.on('pageerror', (e) => pageErrors.push(e.message));
    const loadedLogs = [];
    page.on('console', (m) => {
        if (m.type() === 'error') consoleErrors.push(m.text());
        if (m.text().includes('File detail page loaded')) loadedLogs.push(m.text());
    });
    page.on('response', (r) => { if (r.status() >= 500) failedRequests.push(`${r.status()} ${r.url()}`); });
    await signIn(page);

    // F1 + F2: the multi-page file renders, and pagination moves to page 2.
    const first = await page.goto(`${BASE}/file/${MULTI}`, { waitUntil: 'load' });
    await settle(page);
    check('F6 one navigation initialises the file detail page once',
          loadedLogs.length === 1, `init logs=${loadedLogs.length}`);
    check('F1 multi-page file returns 200', first && first.status() === 200, `status=${first && first.status()}`);
    const pager = page.locator('.unified-pagination-container').first();
    const hasPager = await pager.count();
    check('F1 multi-page file shows pagination', hasPager > 0);
    if (hasPager) {
        const next = page.locator('.unified-pagination-container a[href*="page=2"]').first();
        const nextCount = await next.count();
        check('F2 a link to page 2 exists', nextCount > 0);
        if (nextCount) {
            const resp = await Promise.all([page.waitForNavigation({ waitUntil: 'load' }).catch(() => null), next.click()]);
            await settle(page);
            check('F2 clicking page 2 keeps the file and moves to page 2',
                  page.url().includes(`/file/${MULTI}`) && page.url().includes('page=2'), page.url());
            check('F2 page 2 loads without a server error', !resp[0] || resp[0].status() < 500,
                  resp[0] ? `status=${resp[0].status()}` : 'no navigation');
        }
    }

    // F3 + F4: charts. No "Chart canvas is required", no duplicate customise buttons,
    // and a chart type change raises no uncaught error.
    const chartState = await page.evaluate(() => ({
        canvases: document.querySelectorAll('canvas').length,
        toggles: document.querySelectorAll('.chart-customize-toggle').length,
        typeSelects: document.querySelectorAll('select[id^="chartTypeSelect_"]').length,
    }));
    const duplicateToggles = await page.evaluate(() => {
        const ids = [...document.querySelectorAll('.chart-customize-toggle')].map((b) => b.dataset.chartId || '');
        return ids.length !== new Set(ids).size;
    });
    check('F3 no uncaught "Chart canvas is required"',
          !pageErrors.some((m) => m.includes('Chart canvas is required')), pageErrors.join(' | ').slice(0, 200));
    check('F3 each chart has at most one customise button', !duplicateToggles,
          `canvases=${chartState.canvases} toggles=${chartState.toggles}`);
    if (chartState.typeSelects > 0) {
        const select = page.locator('select[id^="chartTypeSelect_"]').first();
        const before = pageErrors.length;
        await select.selectOption('pie').catch(() => {});
        await page.waitForTimeout(800);
        await select.selectOption('bar').catch(() => {});
        await page.waitForTimeout(800);
        check('F4 chart type changes raise no uncaught error', pageErrors.length === before,
              pageErrors.slice(before).join(' | ').slice(0, 200));
        const toggles2 = await page.evaluate(() => document.querySelectorAll('.chart-customize-toggle').length);
        check('F4 chart type changes do not add customise buttons', toggles2 <= chartState.toggles,
              `before=${chartState.toggles} after=${toggles2}`);
    } else {
        console.log('NOTE F4 no chart type selector on this file; chart-type change not exercised');
    }

    // F5: a file with no content chunks renders a deliberate state, not a crash.
    const empty = await page.goto(`${BASE}/file/${EMPTY}`, { waitUntil: 'load' });
    await settle(page);
    check('F5 file with no content returns 200', empty && empty.status() === 200, `status=${empty && empty.status()}`);
    const emptyText = await page.evaluate(() => document.body.innerText || '');
    check('F5 file with no content shows the page, not an error page',
          !/Internal Server Error|Traceback|TypeError/.test(emptyText));

    check('no uncaught page error in the whole session', pageErrors.length === 0, pageErrors.slice(0, 3).join(' | ').slice(0, 300));
    check('no HTTP 5xx from any request in the session', failedRequests.length === 0, failedRequests.slice(0, 3).join(' | '));

    console.log(`INFO console errors seen: ${consoleErrors.length}`);
    for (const e of consoleErrors.slice(0, 8)) console.log(`INFO   console: ${e.slice(0, 180)}`);
} finally {
    await browser.close();
}

const failed = results.filter((r) => !r.ok).length;
console.log(`SUMMARY ${results.length - failed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
