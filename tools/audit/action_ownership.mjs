/**
 * Records, in a real browser, which script last published each action name on
 * each page, and what happens when the action's own element is clicked.
 *
 * Run by tools/audit/browser_action_ownership.py, which supplies the config in
 * ACTION_CONFIG. Output: one JSON document on stdout.
 *
 * Ownership is captured by installing a setter for every tracked name before
 * any page script runs, so every `window.<name> = ...` is recorded with the
 * file and line that made the assignment. That is the only way to tell a page
 * module assignment from the registry's, because the registry binds its
 * functions and a bound function's source is "[native code]".
 */
import chrom from '@sparticuz/chromium';
import puppeteer from 'puppeteer-core';

const cfg = JSON.parse(process.env.ACTION_CONFIG);
const TRACK = cfg.track;

const exe = await chrom.executablePath();
const browser = await puppeteer.launch({
  executablePath: exe,
  args: [...chrom.args.filter((a) => !/single-process|no-zygote/.test(a)),
         '--no-sandbox', '--disable-dev-shm-usage',
         '--disable-features=IsolateOrigins,site-per-process'],
  headless: true,
});
const page = await browser.newPage();
await page.setViewport({ width: 1440, height: 900 });

const tracer = `
(() => {
  const tracked = ${JSON.stringify(cfg.track)};
  window.__owners = {};
  for (const name of tracked) {
    let value;
    try {
      Object.defineProperty(window, name, {
        configurable: true,
        enumerable: true,
        get() { return value; },
        set(v) {
          value = v;
          const stack = (new Error().stack || '').split('\\n').slice(1, 4)
            .map((l) => l.trim()).join(' | ');
          (window.__owners[name] ||= []).push(stack);
        },
      });
    } catch (e) { /* already non-configurable */ }
  }
})();
`;
await page.evaluateOnNewDocument(tracer);

const consoleErrors = [];
page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 300)); });
page.on('pageerror', (e) => consoleErrors.push('pageerror: ' + String(e).slice(0, 300)));
page.on('dialog', async (d) => { try { await d.accept(); } catch (e) {} });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// sign in
await page.goto(cfg.base + '/auth/login', { waitUntil: 'domcontentloaded' });
const loginOk = await page.evaluate(async (u, p) => {
  const meta = document.querySelector('meta[name="csrf-token"]');
  const body = new URLSearchParams({ username: u, password: p });
  const r = await fetch('/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-CSRFToken': meta?.content || '' },
    body,
    redirect: 'manual',
  });
  return r.status;
}, cfg.user, cfg.password);

const results = [];
for (const target of cfg.pages) {
  const rec = { route: target.route, template: target.template, status: null, actions: [], errors: [] };
  const requests = [];
  const onReq = (r) => { if (!r.url().includes('/static/')) requests.push(`${r.method()} ${r.url()}`); };
  page.on('request', onReq);
  try {
    const resp = await page.goto(cfg.base + target.route, { waitUntil: 'networkidle2', timeout: 45000 });
    rec.status = resp ? resp.status() : null;
    await sleep(target.settle ?? 1200);
    rec.owners = await page.evaluate(() => window.__owners || {});
    for (const action of target.actions) {
      const entry = { name: action.name, expect: action.expect || null, found: false,
                      requests: [], effect: null, error: null };
      // A bulk toolbar can be created only after rows are selected, and a form
      // can only be submitted once its required fields are filled, so the page
      // state is prepared before the element is looked up.
      if (action.uniqueWordInto) {
        await page.evaluate((s) => {
          const el = document.querySelector(s);
          if (!el) return;
          el.value = 'r14w' + Date.now().toString().slice(-7);
          el.dispatchEvent(new Event('input', { bubbles: true }));
          el.dispatchEvent(new Event('change', { bubbles: true }));
        }, action.uniqueWordInto);
      }
      if (action.prefill) {
        for (const [fsel, value] of action.prefill) {
          try {
            await page.evaluate((s, v) => {
              const el = document.querySelector(s);
              if (!el) return;
              el.value = v;
              el.dispatchEvent(new Event('input', { bubbles: true }));
              el.dispatchEvent(new Event('change', { bubbles: true }));
            }, fsel, value);
          } catch (e) {}
        }
      }
      if (action.preClick) {
        for (const psel of action.preClick) {
          try {
            const h = await page.$(psel);
            if (h) { await h.click(); await sleep(600); }
          } catch (e) {}
        }
      }
      if (action.preSelect) {
        for (const psel of action.preSelect) {
          try {
            const h = await page.$(psel);
            if (h) { await h.click(); await sleep(action.preSelectWait ?? 2500); }
          } catch (e) {}
        }
      }
      if (action.preselect) {
        for (const psel of action.preselect) {
          // el.click() rather than a pointer click: a row checkbox can sit in
          // a table the layout scrolls, and a disabled-looking toolbar must
          // still be driven the way a reader's click drives it.
          await page.evaluate((s, n) => {
            Array.from(document.querySelectorAll(s)).slice(0, n).forEach((el) => {
              el.click();
              // Some rows style the checkbox as a card and cancel the default
              // toggle; the page's state is what the toolbar reads, so make the
              // selection real before telling the page about it.
              if (el.type === 'checkbox' && !el.checked) el.checked = true;
              el.dispatchEvent(new Event('change', { bubbles: true }));
            });
          }, psel, action.preselectCount || 1);
          await sleep(600);
        }
      }
      if (action.jquerySelect) {
        // Select2 replaces the native value with its own; setting it through
        // jQuery is what a reader picking an option does.
        for (const [ssel, value] of action.jquerySelect) {
          for (let i = 0; i < 20; i += 1) {
            const ok = await page.evaluate((s, v) => {
              const $ = window.jQuery;
              if (!$) return false;
              const el = document.querySelector(s);
              if (!el || el.options.length < 2) return false;
              $(s).val(v).trigger('change');
              return el.value === v;
            }, ssel, String(value));
            if (ok) break;
            await sleep(250);
          }
        }
      }
      if (action.setSelect) {
        for (const [ssel, value] of action.setSelect) {
          for (let i = 0; i < 20; i += 1) {
            const ok = await page.evaluate((s, v) => {
              const el = document.querySelector(s);
              if (!el || el.tagName !== 'SELECT' || el.options.length < 2) return false;
              el.value = v;
              el.dispatchEvent(new Event('change', { bubbles: true }));
              return el.value === v;
            }, ssel, String(value));
            if (ok) break;
            await sleep(250);
          }
        }
      }
      // Ownership is recorded whether or not the control is on screen: which
      // function holds the name is a property of the loaded page, while a
      // control can legitimately be absent until something is selected.
      entry.ownerBefore = await page.evaluate((n) => (window.__owners[n] || []).slice(-1)[0] || null, action.name);
      entry.handler = await page.evaluate((n) => {
        if (typeof window[n] !== 'function') return { type: typeof window[n] };
        const src = String(window[n]);
        return { type: 'function', bound: src.includes('[native code]'), source: src.slice(0, 400) };
      }, action.name);
      const sel = action.selector || `[data-on-click^="${action.name}("], [data-on-click^="${action.name}?.(\\"]`;
      let handle;
      try {
        handle = await page.$(sel);
      } catch (e) { entry.error = 'selector: ' + String(e).slice(0, 120); }
      if (!handle) {
        entry.error = entry.error || 'no element';
        entry.absentReason = await page.evaluate((n) => (
          document.querySelectorAll(`[data-on-click^="${n}"]`).length
            ? 'attribute present but not matched'
            : 'no control carries data-on-click for this name'), action.name);
        rec.actions.push(entry);
        continue;
      }
      entry.found = true;
      entry.element = await page.evaluate((el) => ({
        tag: el.tagName,
        text: (el.textContent || '').trim().slice(0, 40),
        onclick: el.getAttribute('data-on-click'),
        disabled: !!el.disabled,
      }), handle);
      entry.ownerBefore = await page.evaluate((n) => (window.__owners[n] || []).slice(-1)[0] || null, action.name);
      entry.handler = await page.evaluate((n) => {
        if (typeof window[n] !== 'function') return { type: typeof window[n] };
        const src = String(window[n]);
        return { type: 'function', bound: src.includes('[native code]'), source: src.slice(0, 400) };
      }, action.name);
      const before = requests.length;
      const urlBefore = page.url();
      try {
        await handle.click({ clickCount: 1, delay: 20 });
      } catch (e) {
        // Hidden or off-screen controls (a modal's footer button, a toolbar the
        // page shows later) still receive a real click event this way.
        try {
          await page.evaluate((el) => el.click(), handle);
          entry.error = 'dispatched: ' + String(e).slice(0, 120);
        } catch (e2) { entry.error = 'click: ' + String(e).slice(0, 160); }
      }
      await sleep(action.wait ?? 2000);
      entry.requests = requests.slice(before);
      entry.effect = await page.evaluate((u) => ({
        urlChanged: location.href !== u,
        modalsShown: document.querySelectorAll('.modal.show').length,
        ariaPressed: document.querySelector('[aria-pressed="true"]') ? true : false,
        checkedBoxes: document.querySelectorAll('input[type="checkbox"]:checked').length,
        activeTabs: [...document.querySelectorAll('.tab-button.active, .nav-link.active, .tab-pane.active')]
          .map((e) => e.id || e.getAttribute('data-tab') || e.className).slice(0, 6),
        bodyClass: document.body.className,
        toast: [...document.querySelectorAll('.toast, .alert, #toast-container > *')]
          .map((e) => (e.textContent || '').trim().slice(0, 120)).filter(Boolean).slice(0, 3),
      }), urlBefore);
      rec.actions.push(entry);
      // Leave the page as the action found it where the action legitimately
      // opens something (a modal would otherwise swallow the next click).
      try {
        await page.keyboard.press('Escape');
        await page.evaluate(() => {
          document.querySelectorAll('.modal.show').forEach((m) => {
            m.classList.remove('show');
            m.style.display = 'none';
          });
          document.body.classList.remove('modal-open');
          document.querySelectorAll('.modal-backdrop').forEach((b) => b.remove());
        });
        await sleep(400);
      } catch (e) {}
    }
  } catch (e) {
    rec.errors.push(String(e).slice(0, 300));
  }
  page.off('request', onReq);
  results.push(rec);
}

const out = { base: cfg.base, loginStatus: loginOk, pages: results, consoleErrors: consoleErrors.slice(0, 40) };
await browser.close();
console.log(JSON.stringify(out, null, 2));
