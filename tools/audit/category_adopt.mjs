/**
 * Item 2: the parent-above / adopt create path from the categories list page.
 *
 * Opens the dialog the way a reader does, picks the "Create a parent above"
 * mode, names the new parent, adopts existing categories into it and saves.
 * Records the requests, the response bodies and the resulting table rows, so
 * the run proves the round trip rather than the form's own opinion of it.
 *
 * Driven by tools/audit/browser_category_adopt.py.
 */
import chrom from '@sparticuz/chromium';
import puppeteer from 'puppeteer-core';

const cfg = JSON.parse(process.env.ADOPT_CONFIG);
const exe = await chrom.executablePath();
const browser = await puppeteer.launch({
  executablePath: exe,
  args: [...chrom.args.filter((a) => !/single-process|no-zygote/.test(a)),
         '--no-sandbox', '--disable-dev-shm-usage'],
  headless: true,
});
const page = await browser.newPage();
await page.setViewport({ width: 1440, height: 900 });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const checks = [];
const check = (label, ok, detail) => checks.push({ ok: !!ok, label, detail });
const requests = [];
const responses = [];
page.on('request', (r) => {
  if (!r.url().includes('/static/')) requests.push(`${r.method()} ${r.url()}`);
});
page.on('response', async (r) => {
  if (r.url().includes('/api/categories')) {
    responses.push({ url: r.url(), status: r.status() });
  }
});
const consoleErrors = [];
page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 200)); });
page.on('pageerror', (e) => consoleErrors.push('pageerror: ' + String(e).slice(0, 200)));
page.on('dialog', async (d) => { try { await d.accept(); } catch (e) {} });

// sign in
await page.goto(cfg.base + '/auth/login', { waitUntil: 'domcontentloaded' });
const login = await page.evaluate(async (u, p) => {
  const meta = document.querySelector('meta[name="csrf-token"]');
  const r = await fetch('/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-CSRFToken': meta?.content || '' },
    body: new URLSearchParams({ username: u, password: p }),
    redirect: 'manual',
  });
  return r.status;
}, cfg.user, cfg.password);
// redirect: 'manual' makes a browser fetch report 0 for the login redirect;
// the pages that follow prove the session either way.
check('sign in', [0, 200, 302].includes(login), login);

// create the two categories that will be adopted (through the same dialog)
async function createRoot(name) {
  await page.goto(cfg.base + '/categories', { waitUntil: 'networkidle2' });
  await sleep(1200);
  await page.evaluate(() => {
    const btn = document.querySelector('[data-on-click^="openCategoryModal"]');
    if (btn) btn.click();
  });
  await sleep(800);
  await page.evaluate((n) => {
    const el = document.getElementById('categoryName');
    el.value = n;
    el.dispatchEvent(new Event('input', { bubbles: true }));
  }, name);
  await page.evaluate(() => {
    const btn = document.querySelector('[data-on-click="saveCategory()"]');
    if (btn) btn.click();
  });
  await sleep(2500);
  // read the id from the server rather than from the table: the list is
  // paged, and a new row can land on a page the browser is not showing.
  return page.evaluate(async (n) => {
    const r = await fetch('/api/categories/tree?limit=500&total=0',
                          { headers: { Accept: 'application/json' } });
    const data = await r.json();
    const hit = (data.rows || []).find((c) => c.name === n);
    return hit ? { id: String(hit.id), parentId: hit.parent_id } : null;
  }, name);
}

const stamp = cfg.stamp;
const first = await createRoot(`${stamp}-a`);
const second = await createRoot(`${stamp}-b`);
check('two categories exist to adopt', !!(first && second), { first, second });

// the parent-above / adopt path
const wantName = `${stamp}-parent`;
const requestStart = requests.length;
await page.goto(cfg.base + '/categories', { waitUntil: 'networkidle2' });
await sleep(1200);
await page.evaluate(() => {
  const btn = document.querySelector('[data-on-click^="openCategoryModal"]');
  if (btn) btn.click();
});
await sleep(900);

const dialog = await page.evaluate(() => ({
  adoptGroupVisible: (() => {
    const g = document.getElementById('categoryAdoptGroup');
    return g ? g.style.display !== 'none' : false;
  })(),
  modes: [...document.querySelectorAll('input[name="categoryMode"]')].map((r) => r.value),
}));
check('the dialog offers the parent-above mode', dialog.modes.includes('parent-above'), dialog.modes);

// pick the mode the way the reader does (a click on the radio, not assignment)
await page.evaluate(() => {
  const radio = document.getElementById('categoryModeAbove');
  radio.click();
});
await sleep(600);
const adoptVisible = await page.evaluate(() => {
  const g = document.getElementById('categoryAdoptGroup');
  return g ? g.style.display !== 'none' : false;
});
check('choosing parent-above reveals the adopt list', adoptVisible, adoptVisible);

// the adopt list fills from the server; wait for the two categories to appear
let adoptOptions = 0;
for (let i = 0; i < 40; i += 1) {
  adoptOptions = await page.evaluate(() => {
    const s = document.getElementById('categoryAdoptSelect');
    return s ? s.options.length : 0;
  });
  if (adoptOptions >= 2) break;
  await sleep(250);
}
check('the adopt list is populated', adoptOptions >= 2, adoptOptions);

await page.evaluate((n, ids) => {
  const el = document.getElementById('categoryName');
  el.value = n;
  el.dispatchEvent(new Event('input', { bubbles: true }));
  // adopt exactly the two categories this run created, by their ids
  const sel = document.getElementById('categoryAdoptSelect');
  [...sel.options].forEach((o) => {
    o.selected = ids.includes(String(o.value));
  });
  sel.dispatchEvent(new Event('change', { bubbles: true }));
}, wantName, [first && first.id, second && second.id].filter(Boolean));

const adoptedIds = await page.evaluate(() => {
  const s = document.getElementById('categoryAdoptSelect');
  return [...s.selectedOptions].map((o) => o.value);
});
check('two categories are selected for adoption', adoptedIds.length === 2, adoptedIds);

await page.evaluate(() => {
  const btn = document.querySelector('[data-on-click="saveCategory()"]');
  if (btn) btn.click();
});
await sleep(3500);

const posted = requests.slice(requestStart).filter((r) => r.includes('/api/categories/tree'));
check('the page posts the adopt request', posted.some((r) => r.includes('/parent-above')),
      posted);
check('the response is a success', responses.some((r) => r.url.includes('/parent-above') && r.status < 300),
      responses.filter((r) => r.url.includes('/parent-above')));

// what the round trip left behind, read back from the server
await page.goto(cfg.base + '/categories?search=' + encodeURIComponent(wantName), { waitUntil: 'networkidle2' });
await sleep(1500);
const rows = await page.evaluate(() => [...document.querySelectorAll('tr.category-item')].map((tr) => ({
  id: tr.getAttribute('data-category-id') || tr.getAttribute('data-id'),
  parentId: tr.getAttribute('data-parent-id'),
  cls: tr.className,
  text: (tr.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 80),
})));
check('the new parent is listed', rows.some((r) => r.text.includes(wantName)), rows);

const parentId = await page.evaluate(async (n) => {
  const r = await fetch('/api/categories/tree?limit=500&total=0', { headers: { Accept: 'application/json' } });
  const data = await r.json();
  const list = data.rows || (Array.isArray(data) ? data : []);
  const hit = list.find((c) => (c.name || c.category_name || '').includes(n));
  return hit ? { id: hit.id, children: hit.children || hit.child_ids || null } : null;
}, wantName);

const tree = await page.evaluate(async (pid) => {
  const r = await fetch('/api/categories/' + pid, { headers: { Accept: 'application/json' } });
  if (!r.ok) return { status: r.status };
  return { status: r.status, body: await r.json() };
}, parentId ? parentId.id : 0);

const children = await page.evaluate(async (pid) => {
  const r = await fetch('/api/categories/tree?limit=500&total=0', { headers: { Accept: 'application/json' } });
  const data = await r.json();
  const list = data.rows || (Array.isArray(data) ? data : []);
  return { status: r.status,
           parent: list.find((c) => Number(c.id) === Number(pid)) || null,
           rows: list.filter((c) => Number(c.parent_id) === Number(pid))
             .map((c) => ({ id: c.id, parent_id: c.parent_id, name: c.name })) };
}, parentId ? parentId.id : 0);

check('the adopted categories now sit under the new parent',
      (children.rows || []).length >= 2, children);

await page.screenshot({ path: cfg.shot, fullPage: false });
const out = {
  base: cfg.base, stamp, dialog, adoptOptions, adoptedIds, wantName, parentId, tree,
  children, rows, posted, responses, checks,
  consoleErrors: consoleErrors.slice(0, 20),
  failed: checks.filter((c) => !c.ok).length,
};
await browser.close();
console.log(JSON.stringify(out, null, 2));
