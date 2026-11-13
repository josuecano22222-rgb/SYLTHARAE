// Live-app check: success messages are drawn at the top end of the screen, above
// every other layer (modals, sticky headers, the message system's own cards).
//
//   P1 the notification stack is fixed to the top-end corner
//   P2 its z-index is above every layer in use (highest in the app: 10020)
//   P3 Toast.success lands inside the stack, at the top, and is not covered by
//      a modal backdrop or a sticky header placed on top of it
//   P4 the message system's showSuccess also lands inside the stack
//   P5 nothing is drawn in the bottom half of the viewport for a success
//
// Needs a running app and a browser. Environment:
//   BASE_URL, APP_USER, APP_PASSWORD (required), SHOT (optional screenshot path)
//   PLAYWRIGHT_CORE / CHROMIUM_MODULE optional, as in keyword_list_regressions.mjs
// Skips (exit 0) when the browser or credentials are missing.
//
// Run:  node tests/browser/success_stack_placement.mjs

import path from 'node:path';
import { pathToFileURL } from 'node:url';

const BASE = process.env.BASE_URL || 'http://127.0.0.1:5055';
const USER = process.env.APP_USER || 'admin';
const PASSWORD = process.env.APP_PASSWORD || '';
const SHOT = process.env.SHOT || '';

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
    console.log('SKIP success_stack_placement: needs playwright-core, a Chromium binary, and APP_PASSWORD');
    process.exit(0);
}

const results = [];
function check(name, ok, detail = '') {
    results.push({ name, ok: ok === true });
    console.log(`${ok === true ? 'PASS' : 'FAIL'} ${name}${detail ? ' :: ' + detail : ''}`);
}

async function settle(page) {
    await page.waitForLoadState('networkidle').catch(() => {});
    await page.waitForTimeout(600);
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
    await page.goto(`${BASE}/keywords`, { waitUntil: 'load' });
    await settle(page);

    // P1 + P2: placement and stacking of the shared region.
    const region = await page.evaluate(() => {
        const el = document.querySelector('[data-toast-region]');
        if (!el) return null;
        const r = el.getBoundingClientRect();
        return {
            top: r.top, right: r.right, vw: window.innerWidth,
            position: getComputedStyle(el).position,
            z: Number(getComputedStyle(el).zIndex),
        };
    });
    check('P1 the notification stack exists', !!region);
    if (region) {
        check('P1 the stack is fixed to the top edge', region.position === 'fixed' && region.top <= 24,
              `position=${region.position} top=${region.top}`);
        check('P1 the stack is at the top-end (right) corner', region.right >= region.vw - 24 && region.right <= region.vw + 1,
              `right=${region.right} vw=${region.vw}`);
        check('P2 the stack z-index is above every layer in use (>10020)', region.z > 10020, `z=${region.z}`);
    }

    // Simulate the layers a success could appear under: a full-screen modal
    // backdrop and a sticky header at the top-end corner.
    await page.evaluate(() => {
        const backdrop = document.createElement('div');
        backdrop.id = 'test-backdrop';
        backdrop.style.cssText = 'position:fixed;inset:0;z-index:1055;background:rgba(0,0,0,.5)';
        document.body.appendChild(backdrop);
        const header = document.createElement('div');
        header.id = 'test-header';
        header.style.cssText = 'position:fixed;top:0;right:0;width:100vw;height:80px;z-index:10020;background:#334';
        document.body.appendChild(header);
    });

    // P3: Toast.success, over the backdrop and the sticky header.
    await page.evaluate(() => window.Toast.success('Test success: 3 rows saved.'));
    await page.waitForTimeout(400);
    const toast = await page.evaluate(() => {
        const t = document.querySelector('[data-toast-region] .toast[data-toast="success"]');
        if (!t) return null;
        const r = t.getBoundingClientRect();
        const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
        const hit = document.elementFromPoint(cx, cy);
        return {
            top: r.top, inRegion: !!hit && !!hit.closest('[data-toast-region]'),
            hitTag: hit ? (hit.id || hit.className || hit.tagName) : null,
            bottomHalf: r.top > window.innerHeight / 2,
            text: t.querySelector('[data-toast-message]')?.textContent || '',
        };
    });
    check('P3 Toast.success creates a success toast in the stack', !!toast && toast.text.includes('3 rows saved'));
    if (toast) {
        check('P3 the success toast is at the top of the screen', toast.top < 120, `top=${toast.top}`);
        check('P3 the toast is on top at its centre (not covered by backdrop/header)', toast.inRegion,
              `hit=${toast.hitTag}`);
        check('P5 the success toast is not in the bottom half', !toast.bottomHalf, `top=${toast.top}`);
    }

    // P4: window.showSuccess - the call pages make - lands in the same stack at
    // the top. (It used to draw into a separate container at the bottom.)
    await page.evaluate(() => window.showSuccess('Page success via showSuccess'));
    await page.waitForTimeout(500);
    const viaShow = await page.evaluate(() => {
        const hits = [...document.querySelectorAll('[data-toast-region] [data-toast-message], [data-toast-region] .message-text, [data-toast-region] .notification-message')]
            .filter((el) => (el.textContent || '').includes('Page success via showSuccess'));
        if (!hits.length) return null;
        const r = hits[0].getBoundingClientRect();
        const legacy = [...document.querySelectorAll('#notification-container .notification')].length;
        return { top: r.top, bottomHalf: r.top > window.innerHeight / 2, legacy };
    });
    check('P4 showSuccess is drawn inside the top stack', !!viaShow, viaShow ? '' : 'not found in [data-toast-region]');
    if (viaShow) {
        check('P4 the showSuccess message sits at the top, not the bottom', viaShow.top < 160 && !viaShow.bottomHalf,
              `top=${viaShow.top}`);
        check('P4 no message is drawn into the old bottom container', viaShow.legacy === 0, `legacy=${viaShow.legacy}`);
    }

    if (SHOT) {
        await page.screenshot({ path: SHOT });
    }
    check('no uncaught page error', pageErrors.length === 0, pageErrors.slice(0, 2).join(' | '));
} finally {
    await browser.close();
}

const failed = results.filter((r) => !r.ok).length;
console.log(`SUMMARY ${results.length - failed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
