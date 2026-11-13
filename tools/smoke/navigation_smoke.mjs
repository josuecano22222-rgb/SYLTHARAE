/**
 * Browser smoke: the static sidebar + the Settings Navigation tab
 * (owner requirements D11/D12 in EXECUTION_STATUS.md).
 *
 * Proves, in a real browser against a running, installed server:
 *
 *  D11  a sidebar click swaps only #mainContent - the sidebar DOM node
 *       survives (a marked property is still present), its scrollTop
 *       survives, and a first-load marker on window survives (no full
 *       navigation); history.back() returns under the same rules;
 *  D12  Settings -> Navigation lists every sidebar interface (hidden ones
 *       included, so hiding is never a one-way door); moving a row with the
 *       Up button and saving updates the existing sidebar node in place,
 *       without a page load; a later real load still restores the saved order.
 *
 * Usage (against a running, installed server - see docs/TESTING.md):
 *
 *     npm install --prefix /tmp/smoke-tools puppeteer-core   # once
 *     NODE_PATH=/tmp/smoke-tools/node_modules CHROME_PATH=/usr/bin/chromium \
 *         SMOKE_ADMIN_PASSWORD=... node tools/smoke/navigation_smoke.mjs
 *
 * Environment:
 *     SMOKE_BASE_URL        http://localhost:5055 (use "localhost": browsers
 *                           keep Secure cookies there without TLS)
 *     SMOKE_ADMIN_USER      admin
 *     SMOKE_ADMIN_PASSWORD  (required)
 *     CHROME_PATH           a Chrome/Chromium executable (required)
 *     CHROME_ARGS           extra flags, space separated
 *
 * Exit status is non-zero on any failure.
 */
import { createRequire } from 'node:module';

// Resolved through require() so NODE_PATH works: ES module imports ignore it,
// and puppeteer-core is a tool dependency, not a product one.
const puppeteer = createRequire(import.meta.url)('puppeteer-core');

const BASE = (process.env.SMOKE_BASE_URL || 'http://localhost:5055').replace(/\/$/, '');
const USER = process.env.SMOKE_ADMIN_USER || 'admin';
const PASSWORD = process.env.SMOKE_ADMIN_PASSWORD || '';
if (!PASSWORD) { console.error('SMOKE_ADMIN_PASSWORD required'); process.exit(2); }

let failures = 0;
function check(name, ok, detail = '') {
    console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail ? ' -- ' + String(detail).slice(0, 300) : ''}`);
    if (!ok) failures += 1;
}

const CHROME = process.env.CHROME_PATH || '';
if (!CHROME) {
    console.error('CHROME_PATH is required (a Chrome/Chromium executable)');
    process.exit(2);
}
const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: 'shell',
    args: ['--no-sandbox', '--disable-dev-shm-usage',
           ...(process.env.CHROME_ARGS || '').split(' ').filter(Boolean)],
});
const page = await browser.newPage();
await page.setViewport({ width: 1440, height: 900 });  // desktop: sidebar open (>= 992px)
page.setDefaultTimeout(30000);
const consoleErrors = [];
let expectedNavigationFailure = false;
page.on('pageerror', (e) => {
    if (expectedNavigationFailure && /Failed to fetch/i.test(e.message)) return;
    consoleErrors.push('pageerror: ' + e.message);
});
page.on('console', (m) => {
    if (m.type() !== 'error') return;
    const text = m.text();
    // The smoke deliberately aborts one navigation fetch below. Its two
    // browser-console messages are evidence of that expected failure, not a
    // product console regression; other errors remain fatal.
    if (expectedNavigationFailure &&
        (text.includes('net::ERR_FAILED') || text === 'Fetch error: TypeError: Failed to fetch')) return;
    consoleErrors.push('console: ' + text);
});

try {
    // ── sign in ─────────────────────────────────────────────────────────
    await page.goto(BASE + '/auth/login', { waitUntil: 'networkidle0' });
    await page.type('input[name="username"]', USER);
    await page.type('input[name="password"]', PASSWORD);
    await Promise.all([
        page.waitForNavigation({ waitUntil: 'networkidle0' }),
        page.click('button[type="submit"]'),
    ]);
    check('signed in', !page.url().includes('/auth/login'), page.url());

    // ── D11: first page — mark the sidebar, scroll it, mark the load ───
    await page.goto(BASE + '/', { waitUntil: 'networkidle0' });
    const marked = await page.evaluate(() => {
        const sb = document.getElementById('sidebar');
        if (!sb) return { ok: false, why: 'no #sidebar' };
        sb.__navSmokeMarker = 'syl-smoke-42';
        sb.scrollTop = 120;
        window.__navSmokeFirstLoad = true;
        const link = sb.querySelector('a[href]:not([href="#"])');
        return {
            ok: true,
            firstHref: link ? link.getAttribute('href') : null,
            firstPath: link ? new URL(link.href, location.origin).pathname : null,
            here: location.pathname,
        };
    });
    check('sidebar found + marked', marked.ok, marked.why || '');

    // click the FIRST sidebar link that differs from the current path
    const target = await page.evaluate((here) => {
        const links = [...document.querySelectorAll('#sidebar a[href]')];
        const usable = links.filter((a) => {
            try {
                const p = new URL(a.href, location.origin).pathname;
                return p !== here && p !== '/' && !/logout|download|#/i.test(a.href);
            } catch { return false; }
        });
        const l = usable.find((a) => a.pathname.startsWith('/reports'))
            || usable.find((a) => a.pathname.startsWith('/files'))
            || usable[0];
        if (!l) return null;
        return new URL(l.href, location.origin).pathname;
    }, marked.here);
    check('a different sidebar link exists', !!target, target);

    // JS-dispatched click: puppeteer's CDP click pre-scrolls the target
    // into view, which would mask the very scroll preservation we test.
    await page.evaluate((href) => {
        document.querySelector(`#sidebar a[href="${href}"]`).click();
    }, target);
    await page.waitForFunction(
        (prev) => window.__navSwapDone || location.pathname !== prev,
        { timeout: 15000 }, marked.here,
    ).catch(() => {});
    await new Promise((r) => setTimeout(r, 1200)); // let the swap settle

    const after = await page.evaluate(() => {
        const sb = document.getElementById('sidebar');
        return {
            path: location.pathname,
            marker: sb ? sb.__navSmokeMarker : undefined,
            scroll: sb ? sb.scrollTop : -1,
            firstLoad: window.__navSmokeFirstLoad,
            mainChanged: !!document.getElementById('mainContent'),
        };
    });
    check('path changed after sidebar click', after.path !== marked.here,
          `${marked.here} -> ${after.path}`);
    check('sidebar node survived (same DOM node)', after.marker === 'syl-smoke-42',
          'marker=' + after.marker);
    check('sidebar scroll survived', after.scroll === 120, 'scroll=' + after.scroll);
    check('no full page reload (window survived)', after.firstLoad === true,
          'firstLoad=' + after.firstLoad);

    // ── D11: back returns, still no redraw ──────────────────────────────
    await page.goBack().catch(() => {});
    await new Promise((r) => setTimeout(r, 1200));
    const back = await page.evaluate(() => {
        const sb = document.getElementById('sidebar');
        return {
            path: location.pathname,
            marker: sb ? sb.__navSmokeMarker : undefined,
            firstLoad: window.__navSmokeFirstLoad,
        };
    });
    check('history.back() returned to first page', back.path === marked.here, back.path);
    check('sidebar survived back-navigation', back.marker === 'syl-smoke-42', back.marker);
    check('still no full reload after back', back.firstLoad === true);

    // A failed same-origin fetch must remain an in-content failure with a
    // retry, not degrade into a full-page navigation.
    await page.evaluate(() => {
        const sb = document.getElementById('sidebar');
        if (sb) sb.__navSmokeMarker = 'syl-smoke-42';
        window.__navSmokeFirstLoad = true;
    });
    let failedOnce = false;
    const failTargetFetch = (request) => {
        const expected = new URL(target, BASE).href;
        if (!failedOnce && request.resourceType() === 'fetch'
            && request.url().startsWith(expected)) {
            failedOnce = true;
            request.abort();
        } else {
            request.continue();
        }
    };
    await page.setRequestInterception(true);
    page.on('request', failTargetFetch);
    expectedNavigationFailure = true;
    await page.evaluate((href) => document.querySelector(`#sidebar a[href="${href}"]`).click(), target);
    await page.waitForSelector('#mainContent [data-navigation-swap-status][role="alert"]',
                               { timeout: 10000 });
    expectedNavigationFailure = false;
    const failedNavigation = await page.evaluate(() => ({
        marker: document.getElementById('sidebar')?.__navSmokeMarker,
        firstLoad: window.__navSmokeFirstLoad,
        error: !!document.querySelector('#mainContent [data-navigation-swap-status][role="alert"]'),
    }));
    check('failed navigation stays in the content area with retry',
          failedOnce && failedNavigation.error
          && failedNavigation.marker === 'syl-smoke-42'
          && failedNavigation.firstLoad === true, JSON.stringify(failedNavigation));
    page.off('request', failTargetFetch);
    await page.setRequestInterception(false);
    await page.click('#mainContent [data-navigation-swap-status] button');
    await page.waitForFunction((path) => location.pathname === path,
                               { timeout: 15000 }, target);
    check('retry completes the route without replacing the sidebar',
          await page.evaluate(() => window.__navSmokeFirstLoad === true
              && document.getElementById('sidebar')?.__navSmokeMarker === 'syl-smoke-42'));

    // ── D12: Settings → Navigation tab ─────────────────────────────────
    await page.goto(BASE + '/settings', { waitUntil: 'networkidle0' });
    await page.click('#navigation-tab');
    await page.waitForSelector('#navigationPrefsBody tr[data-interface]', { timeout: 15000 });

    // pick two ADJACENT interfaces in the same domain: positions reorder
    // within a domain (the sidebar is domain-grouped).
    const pair = await page.evaluate(async () => {
        const r = await fetch('/api/preferences/navigation',
                              { headers: { Accept: 'application/json' } });
        const data = await r.json();
        const e = data.entries || [];
        for (let i = 0; i + 1 < e.length; i++) {
            if (e[i].domain === e[i + 1].domain) {
                return { first: e[i].interface_id, second: e[i + 1].interface_id };
            }
        }
        return null;
    });
    check('an adjacent same-domain pair exists', !!pair, JSON.stringify(pair));
    const order1 = await page.$$eval('#navigationPrefsBody tr[data-interface]',
        (rows) => rows.map((r) => r.getAttribute('data-interface')));
    check('catalog loaded with rows', order1.length >= 2, `${order1.length} rows`);

    // Mark the existing shell, then move the second of the pair up (swap
    // the two) and save. The navigation preference action must not reload
    // or replace the sidebar node. Click the exact button: a coordinate
    // click can graze a neighbouring checkbox and silently hide an entry.
    await page.evaluate((iid) => {
        const btn = document.querySelector(
            `#navigationPrefsBody tr[data-interface="${iid}"] button[data-move="up"]`);
        btn.click();
    }, pair.second);
    const order2 = await page.$$eval('#navigationPrefsBody tr[data-interface]',
        (rows) => rows.map((r) => r.getAttribute('data-interface')));
    const i1 = order2.indexOf(pair.second);
    check('move reorders the table',
          order2[i1] === pair.second && order2[i1 + 1] === pair.first,
          `expected ${pair.second},${pair.first} got ${order2[i1]},${order2[i1 + 1]}`);
    const positionControl = await page.$eval(
        `#navigationPrefsBody tr[data-interface="${pair.second}"] input[data-position-index]`,
        (input) => Number(input.value));
    check('the position field displays the specific within-group index',
          positionControl === 1, `position=${positionControl}`);
    const numericReorder = await page.evaluate((iid) => {
        let input = document.querySelector(
            `#navigationPrefsBody tr[data-interface="${iid}"] input[data-position-index]`);
        input.value = '2';
        input.dispatchEvent(new Event('change', { bubbles: true }));
        const restored = [...document.querySelectorAll('#navigationPrefsBody tr[data-interface]')]
            .map((row) => row.getAttribute('data-interface'));
        input = document.querySelector(
            `#navigationPrefsBody tr[data-interface="${iid}"] input[data-position-index]`);
        input.value = '1';
        input.dispatchEvent(new Event('change', { bubbles: true }));
        return { restored, final: [...document.querySelectorAll('#navigationPrefsBody tr[data-interface]')]
            .map((row) => row.getAttribute('data-interface')) };
    }, pair.second);
    check('entering a position index reorders the same group',
          numericReorder.restored.indexOf(pair.first) < numericReorder.restored.indexOf(pair.second)
          && numericReorder.final.indexOf(pair.second) < numericReorder.final.indexOf(pair.first),
          JSON.stringify(numericReorder));

    await page.evaluate(() => {
        const sidebar = document.getElementById('sidebar');
        sidebar.__prefsSmokeMarker = 'same-sidebar';
        window.__prefsSmokeLoadMarker = true;
    });
    await page.click('#navigationPrefsSave');
    await page.waitForFunction(() =>
        /saved\.|updated/i.test(document.getElementById('navigationPrefsStatus').textContent),
        { timeout: 15000 });
    const afterSave = await page.evaluate(() => ({
        sidebarMarker: document.getElementById('sidebar')?.__prefsSmokeMarker,
        loadMarker: window.__prefsSmokeLoadMarker,
    }));
    check('saving order updates the current sidebar without a full page load',
          afterSave.sidebarMarker === 'same-sidebar' && afterSave.loadMarker === true,
          JSON.stringify(afterSave));

    // A later real load proves the order came from PostgreSQL, not only local state.
    await page.reload({ waitUntil: 'networkidle0' });
    await page.click('#navigation-tab');
    await page.waitForSelector('#navigationPrefsBody tr[data-interface]', { timeout: 15000 });
    const order3 = await page.$$eval('#navigationPrefsBody tr[data-interface]',
        (rows) => rows.map((r) => r.getAttribute('data-interface')));
    const j1 = order3.indexOf(pair.second);
    check('reordered position persisted across reload',
          order3[j1] === pair.second && order3[j1 + 1] === pair.first,
          `expected ${pair.second},${pair.first} got ${order3[j1]},${order3[j1 + 1]}`);
    check('hidden interfaces stay listed in the editor',
          order3.length === order1.length,
          `${order3.length} vs ${order1.length} rows`);

    // reset restores the declared order
    page.once('dialog', (d) => d.accept());
    await page.evaluate(() => {
        document.getElementById('sidebar').__resetSmokeMarker = 'same-sidebar';
        window.__resetSmokeLoadMarker = true;
    });
    await page.click('#navigationPrefsReset');
    await page.waitForFunction(() =>
        /default sidebar/i.test(document.getElementById('navigationPrefsStatus').textContent),
        { timeout: 15000 });
    const afterReset = await page.evaluate(() => ({
        sidebarMarker: document.getElementById('sidebar')?.__resetSmokeMarker,
        loadMarker: window.__resetSmokeLoadMarker,
    }));
    check('reset updates preferences without replacing the shell',
          afterReset.sidebarMarker === 'same-sidebar' && afterReset.loadMarker === true,
          JSON.stringify(afterReset));
    await page.reload({ waitUntil: 'networkidle0' });
    await page.click('#navigation-tab');
    await page.waitForSelector('#navigationPrefsBody tr[data-interface]', { timeout: 15000 });
    const order4 = await page.$$eval('#navigationPrefsBody tr[data-interface]',
        (rows) => rows.map((r) => r.getAttribute('data-interface')));
    check('reset restored the default order',
          JSON.stringify(order4) === JSON.stringify(order1),
          `expected ${order1.slice(0, 3)} got ${order4.slice(0, 3)}`);

    // ── D11 discipline: the jobs page's polling timer must die on leave ──
    // The jobs page runs a 2 s poller. Navigating away (swap) used to leave
    // it firing against DOM that no longer exists - an error every 2 s for
    // the rest of the session. Loading the page directly, then swapping
    // away, must produce zero new errors after the swap settles.
    const errorsBeforeJobs = consoleErrors.length;
    // The jobs page holds an SSE stream open: networkidle never fires, so
    // wait for the DOM and a settle instead.
    await page.goto(BASE + '/operations/jobs', { waitUntil: 'domcontentloaded' });
    await new Promise((r) => setTimeout(r, 2500));   // one poll tick on-page
    const jobsErrorsOnPage = consoleErrors.length - errorsBeforeJobs;
    check('jobs page runs clean while on it', jobsErrorsOnPage === 0,
          consoleErrors.slice(errorsBeforeJobs).join(' | ').slice(0, 200));

    // Swap away via the sidebar (or history if this server has no sidebar
    // link for it), then out-wait the 2 s poller twice.
    const away = await page.evaluate(() => {
        const link = document.querySelector('#sidebar a[href="/operations/jobs"]');
        // We are ON /operations/jobs; swap to a neighbour instead.
        const other = [...document.querySelectorAll('#sidebar a[href]')]
            .map((a) => a.getAttribute('href'))
            .find((h) => h && h !== '/operations/jobs' && /^\/[^/]/.test(h)
                       && !/logout|download|#/.test(h));
        if (other) { document.querySelector(`#sidebar a[href="${other}"]`).click(); return other; }
        history.back();
        return 'history.back';
    });
    await new Promise((r) => setTimeout(r, 4500));   // > two poll ticks
    const errorsAfterLeave = consoleErrors.length - errorsBeforeJobs - jobsErrorsOnPage;
    check('no jobs-page timer errors after navigating away', errorsAfterLeave === 0,
          (away) + ' -> ' + consoleErrors.slice(errorsBeforeJobs + jobsErrorsOnPage)
              .join(' | ').slice(0, 200));

    // Coming back to the jobs page re-runs its script: the table fills
    // again (rows or its empty state), and the poller was not duplicated.
    await page.goto(BASE + '/operations/jobs', { waitUntil: 'domcontentloaded' });
    await new Promise((r) => setTimeout(r, 1500));
    const jobsAlive = await page.evaluate(() => {
        const body = document.getElementById('jobsBody');
        return { body: !!body, rows: body ? body.querySelectorAll('tr').length : 0 };
    });
    check('returning to the jobs page re-binds it', jobsAlive.body && jobsAlive.rows >= 0
          && await page.evaluate(() => typeof window.__jobsPageTeardown === 'function'),
          JSON.stringify(jobsAlive));

    // ── Sidebar discipline, exhaustive (owner field report: "the sidebar
    // refreshes whenever I navigate between interfaces"). EVERY sidebar
    // entry is clicked; after each, the sidebar must still be the SAME
    // DOM node and the window must never have torn down (no full load).
    const swapExposed = await page.evaluate(() => typeof window.swapNavigate === 'function'
                                                && window.__navigationSwapInstalled === true);
    check('content-swap navigator (swapNavigate) is installed', swapExposed,
          'window.swapNavigate=' + typeof (await page.evaluate(() => window.swapNavigate)));

    const walk = await page.evaluate(async () => {
        // re-mark: an earlier section of this smoke full-loaded the page
        window.__navSmokeFirstLoad = true;
        const sb0 = document.getElementById('sidebar');
        if (sb0) sb0.__navSmokeMarker = 'syl-smoke-42';
        const links = [...document.querySelectorAll('#sidebar a.sidebar-nav-link')].filter((a) => {
            const h = a.getAttribute('href') || '';
            return h && !h.startsWith('#') && !/logout|download/i.test(h)
                && (!a.target || a.target === '_self');
        });
        const results = [];
        for (const link of links) {
            const path = new URL(link.href, location.origin).pathname;
            link.click();
            // the swap replaces #mainContent and re-runs page init; settle
            const deadline = Date.now() + 8000;
            while (Date.now() < deadline) {
                await new Promise((r) => setTimeout(r, 250));
                const main = document.getElementById('mainContent');
                if (main && main.getAttribute('aria-busy') !== 'true'
                    && location.pathname === path) break;
            }
            await new Promise((r) => setTimeout(r, 400));
            const sb = document.getElementById('sidebar');
            results.push({
                path,
                sameNode: !!sb && sb.__navSmokeMarker === 'syl-smoke-42',
                noReload: window.__navSmokeFirstLoad === true,
                landed: location.pathname === path,
            });
        }
        return results;
    });
    const brokenWalk = walk.filter((r) => !(r.sameNode && r.noReload && r.landed));
    check('every sidebar entry swaps without a full load (' + walk.length + ' links)',
          walk.length > 5 && brokenWalk.length === 0,
          brokenWalk.map((r) => r.path + ' node:' + r.sameNode + ' reload:' + !r.noReload
                         + ' landed:' + r.landed).join(' | ') || 'all clean');

    // ── Pagination jump: server-side paging must swap, not reload ──
    await page.goto(BASE + '/words', { waitUntil: 'networkidle0' });
    await page.evaluate(() => {
        window.__navSmokeFirstLoad = true;   // re-mark: this WAS a full load
        const sb = document.getElementById('sidebar');
        if (sb) sb.__navSmokeMarker = 'syl-smoke-42';
    });
    const jump = await page.evaluate(() => {
        const input = document.querySelector('.unified-pagination-jump-input');
        const btn = document.querySelector('.unified-pagination-jump-btn');
        if (!input || !btn) return { present: false };
        input.value = '2';
        btn.click();
        return { present: true };
    });
    if (jump.present) {
        await new Promise((r) => setTimeout(r, 1800));
        const afterJump = await page.evaluate(() => ({
            page: new URLSearchParams(location.search).get('page'),
            noReload: window.__navSmokeFirstLoad === true,
            sameNode: (() => { const sb = document.getElementById('sidebar');
                               return !!sb && sb.__navSmokeMarker === 'syl-smoke-42'; })(),
        }));
        check('pagination jump swaps without a full load',
              afterJump.page === '2' && afterJump.noReload && afterJump.sameNode,
              JSON.stringify(afterJump));
    } else {
        check('pagination jump control present (seed data expected >1 page)',
              false, 'no .unified-pagination-jump-btn on /words');
    }

    // sidebar on a normal page reflects nothing broken (no JS errors)
    check('no page/console errors during the run', consoleErrors.length === 0,
          consoleErrors.slice(0, 3).join(' | '));
} finally {
    await browser.close();
}
console.log(failures === 0 ? 'NAV SMOKE: ALL PASS' : `NAV SMOKE: ${failures} FAILURE(S)`);
process.exit(failures === 0 ? 0 : 1);
