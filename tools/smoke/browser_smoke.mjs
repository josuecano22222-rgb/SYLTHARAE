/**
 * Browser smoke test: every page, in a real browser, under the real policy.
 *
 * The server tests prove the markup has no inline script (RES-CSP-01); only a
 * browser proves the pages still *work* once the Content-Security-Policy
 * forbids it. This signs in, crawls the same-origin links from the dashboard
 * and, on every page, fails on:
 *
 *   * a CSP violation (securitypolicyviolation event or "Refused to ..." console
 *     message) - something still needs inline script, eval or a javascript: URL;
 *   * an uncaught page error;
 *   * a same-origin response >= 400 or a request that never completed;
 *   * data-on-* handlers on a page that did not load the declarative runtime;
 *   * a data-on-* handler whose function is not reachable from window once the
 *     page's scripts (modules included) have run - it would do nothing on click.
 *
 * It then drives a few harmless controls to prove the runtime dispatches real
 * events (it never clicks arbitrary buttons: some delete things).
 *
 * Usage (against a running, installed server - see docs/TESTING.md):
 *
 *     npm install --prefix /tmp/smoke-tools puppeteer-core     # once
 *     NODE_PATH=/tmp/smoke-tools/node_modules CHROME_PATH=/usr/bin/chromium \
 *         SMOKE_ADMIN_PASSWORD=... node tools/smoke/browser_smoke.mjs
 *
 * Environment:
 *     SMOKE_BASE_URL        http://localhost:5055 (use "localhost": browsers keep
 *                           Secure cookies there without TLS)
 *     SMOKE_ADMIN_USER      admin
 *     SMOKE_ADMIN_PASSWORD  (required)
 *     CHROME_PATH           a Chrome/Chromium executable (required)
 *     CHROME_ARGS           extra flags, space separated
 *     SMOKE_MAX_PAGES       crawl limit (80)
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
const CHROME = process.env.CHROME_PATH || '';
const MAX_PAGES = Number(process.env.SMOKE_MAX_PAGES || 80);
const SKIP = /logout|delete|remove|export|download|reset|purge|\/api\/|\/static\/|\/original|\/preview|\.(zip|csv|pdf|json|txt)$/i;

if (!PASSWORD || !CHROME) {
    console.error('SMOKE_ADMIN_PASSWORD and CHROME_PATH are required');
    process.exit(2);
}

const results = [];
function check(name, ok, detail = '') {
    results.push({name, ok: !!ok});
    console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail ? ` -- ${String(detail).slice(0, 400)}` : ''}`);
    return ok;
}

const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: true,
    args: ['--no-sandbox', ...(process.env.CHROME_ARGS || '').split(' ').filter(Boolean)],
});
const page = await browser.newPage();

// Everything the browser refuses or throws, per page.
let problems = [];
page.on('console', (message) => {
    const text = message.text();
    if (/Refused to (execute|load|evaluate|apply)|Content Security Policy/i.test(text)) {
        problems.push(`CSP console: ${text}`);
    }
});
page.on('pageerror', (error) => problems.push(`page error: ${error.message}`));
// A same-origin request the server refused, or that never completed, breaks the
// page even when nothing is thrown: a missing module script silently leaves
// its handlers undefined. Navigating away aborts in-flight requests (the Jobs
// page's event stream), so net::ERR_ABORTED is expected and ignored.
const sameOrigin = (url) => url.startsWith(`${BASE}/`);
page.on('response', (response) => {
    if (sameOrigin(response.url()) && response.status() >= 400) {
        problems.push(`HTTP ${response.status()} ${response.url().replace(BASE, '')}`);
    }
});
page.on('requestfailed', (request) => {
    const reason = request.failure() && request.failure().errorText;
    if (sameOrigin(request.url()) && reason !== 'net::ERR_ABORTED') {
        problems.push(`request failed: ${request.url().replace(BASE, '')} ${reason}`);
    }
});
await page.evaluateOnNewDocument(() => {
    document.addEventListener('securitypolicyviolation', (event) => {
        (window.__cspViolations = window.__cspViolations || []).push(
            `${event.violatedDirective} blocked ${event.blockedURI || 'inline'} at ${event.sourceFile}:${event.lineNumber}`);
    });
});

// --- sign in -------------------------------------------------------------
await page.goto(`${BASE}/auth/login`, {waitUntil: 'networkidle0'});
check('login page loads without CSP violations',
    problems.length === 0 && (await page.evaluate(() => (window.__cspViolations || []).length)) === 0,
    problems.join(' | '));
await page.type('#username', USER);
await page.type('#password', PASSWORD);
await Promise.all([page.waitForNavigation({waitUntil: 'networkidle0'}), page.click('#login-btn')]);
if (!check('signed in', !page.url().includes('/login'), page.url())) {
    await browser.close();
    process.exit(1);
}

// --- per page ---------------------------------------------------------------
async function inspect() {
    return page.evaluate(() => {
        const runtime = window.SyltharaeDeclarativeEvents;
        const handlers = [...document.querySelectorAll('*')].flatMap((el) =>
            [...el.attributes].filter((a) => a.name.startsWith('data-on-')).map((a) => a.value));
        const unresolved = new Set();
        const unparsable = [];
        const roots = (node, out) => {
            if (!node || typeof node !== 'object') return;
            if (node.type === 'call' && !node.optional) {
                let callee = node.callee;
                while (callee.type === 'member') callee = callee.object;
                if (callee.type === 'identifier') out.add(callee.name);
            }
            Object.values(node).forEach((child) => {
                if (Array.isArray(child)) child.forEach((c) => roots(c, out));
                else if (child && typeof child === 'object') roots(child, out);
            });
        };
        if (runtime) {
            for (const source of new Set(handlers)) {
                try {
                    const names = new Set();
                    roots(runtime.parse(source), names);
                    names.forEach((name) => { if (!(name in window)) unresolved.add(name); });
                } catch (error) {
                    unparsable.push(`${source}: ${error.message}`);
                }
            }
        }
        const links = [...document.querySelectorAll('a[href]')]
            .map((a) => a.href).filter((href) => href.startsWith(location.origin));
        return {
            runtime: !!runtime, handlers: handlers.length, unresolved: [...unresolved],
            unparsable, violations: window.__cspViolations || [], links,
            inlineHandlers: [...document.querySelectorAll('*')].filter((el) =>
                [...el.attributes].some((a) => /^on[a-z]+$/.test(a.name))).length,
        };
    });
}

const queue = [`${BASE}/`];
const seen = new Set();
let visited = 0;
while (queue.length && visited < MAX_PAGES) {
    const url = queue.shift().split('#')[0];
    const key = url.replace(/\?.*$/, '');
    if (seen.has(key) || SKIP.test(url)) continue;
    seen.add(key);
    problems = [];
    let response;
    try {
        // 'load', not network idle: the Jobs page holds an event stream open.
        response = await page.goto(url, {waitUntil: 'load', timeout: 30000});
    } catch (error) {
        check(`${key} loads`, false, error.message);
        continue;
    }
    const type = response && response.headers()['content-type'] || '';
    if (!response || response.status() !== 200 || !type.includes('text/html')) continue;
    visited += 1;
    await new Promise((resolve) => setTimeout(resolve, 1200));   // deferred + module scripts
    const state = await inspect();
    const path = key.replace(BASE, '') || '/';
    const issues = [...problems, ...state.violations.map((v) => `CSP: ${v}`)];
    if (state.handlers && !state.runtime) issues.push('data-on-* without the runtime');
    if (state.inlineHandlers) issues.push(`${state.inlineHandlers} inline handler attributes`);
    state.unparsable.forEach((u) => issues.push(`unparsable handler ${u}`));
    if (state.unresolved.length) issues.push(`handlers call names not on window: ${state.unresolved.join(', ')}`);
    check(`${path} (${state.handlers} handlers)`, issues.length === 0, issues.join(' | '));
    for (const link of state.links) {
        const bare = link.split('#')[0];
        if (!seen.has(bare.replace(/\?.*$/, '')) && !SKIP.test(bare)) queue.push(bare);
    }
}
check(`crawled enough pages (${visited})`, visited >= 15, `${visited}`);

// --- the runtime dispatches real events ---------------------------------------
await page.goto(`${BASE}/`, {waitUntil: 'networkidle0'});
const dispatched = await page.evaluate(() => {
    // A control built the way the templates build them, driven by a real click.
    window.__smokeCalls = [];
    window.__smokeRecord = (...args) => window.__smokeCalls.push(args);
    const host = document.createElement('div');
    host.setAttribute('data-on-click', "__smokeRecord('outer')");
    const button = document.createElement('button');
    button.type = 'button';
    button.value = 'v1';
    button.setAttribute('data-on-click', "__smokeRecord('inner', this.value); return false;");
    host.appendChild(button);
    document.body.appendChild(host);
    const event = new MouseEvent('click', {bubbles: true, cancelable: true});
    button.dispatchEvent(event);
    host.remove();
    return {calls: window.__smokeCalls, prevented: event.defaultPrevented};
});
check('a real click runs the handler, then its ancestor, with this bound',
    JSON.stringify(dispatched.calls) === JSON.stringify([['inner', 'v1'], ['outer']]), JSON.stringify(dispatched));
check('return false prevents the default action', dispatched.prevented === true);

// The Settings tabs are Bootstrap tabs (data-bs-toggle, run by bootstrap.js
// under the CSP); switching one is harmless. A missing tab is a failure.
await page.goto(`${BASE}/settings`, {waitUntil: 'networkidle0'});
const tab = await page.$('[role="tab"]:not(.active)');
if (tab) {
    problems = [];
    const target = await tab.evaluate((el) => el.getAttribute('data-bs-target'));
    await tab.click();
    await new Promise((resolve) => setTimeout(resolve, 400));
    const shown = await page.evaluate((sel) => {
        const pane = document.querySelector(sel);
        return Boolean(pane && pane.classList.contains('active') && pane.classList.contains('show'));
    }, target);
    const selected = await tab.evaluate((el) => el.classList.contains('active'));
    check('a Settings tab shows its pane on click, without errors',
        shown && selected && problems.length === 0,
        `${target} shown=${shown} selected=${selected} ${problems.join(' | ')}`);
} else {
    check('the Settings page has tabs', false, 'no [role="tab"] control');
}

// An image that fails to load falls back through data-on-error (base.html logo).
const fallback = await page.evaluate(() => new Promise((resolve) => {
    const img = document.createElement('img');
    const sibling = document.createElement('span');
    sibling.style.display = 'none';
    img.setAttribute('data-on-error', "this.style.display='none'; this.nextElementSibling.style.display='';");
    document.body.append(img, sibling);
    img.addEventListener('error', () => setTimeout(() => resolve({img: img.style.display, sibling: sibling.style.display}), 0));
    img.src = '/static/does-not-exist.png';
}));
check('a failed image runs its data-on-error fallback',
    fallback.img === 'none' && fallback.sibling === '', JSON.stringify(fallback));

await browser.close();
const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
