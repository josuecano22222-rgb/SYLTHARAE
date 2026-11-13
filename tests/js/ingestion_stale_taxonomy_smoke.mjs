// Runs the real ingestion studio module (static/js/pages/ingestion-studio-page.js)
// and leaves the page while its sources/sides request is still in flight, the
// way soft navigation to another page does. When the response arrives, the
// studio must not render into the page that replaced it. Before the fix this
// threw "Cannot read properties of null (reading 'value')" from renderPickList,
// an unhandled rejection that the browser logs as an uncaught error.
import { makeDom, counters } from './_vm_dom.mjs';

// 'stale' (default): the page is replaced while the request is pending.
// 'mounted': the page stays; the response must render the pickers (control).
const MODE = process.argv[2] || 'stale';

const PAGE_IDS = ['ingestAction', 'sourceFilter', 'sideFilter', 'sourceList', 'sideList',
  'sourceDisplay', 'sideDisplay'];
const absent = new Set();

const base = makeDom();
base.document.getElementById('ingestionPageData').textContent = '{"strings":{}}';
// renderDock writes into the start button's label; the stub has no real children.
base.document.getElementById('btnStart').querySelector = () => ({ innerHTML: '', textContent: '', classList: { toggle() {}, add() {}, remove() {} } });

globalThis.document = {
  readyState: 'complete',
  getElementById(id) { return absent.has(id) ? null : base.document.getElementById(id); },
  querySelector() { return null; },
  querySelectorAll() { return []; },
  addEventListener() {},
  removeEventListener() {},
  createElement: base.document.createElement,
  createTextNode: base.document.createTextNode,
};
globalThis.window = {
  location: { pathname: '/operations/input', href: 'http://x/operations/input', search: '' },
  setInterval: () => 1,
  clearInterval() {},
  addEventListener() {},
  removeEventListener() {},
};

const pending = {};
const json = (body) => ({ ok: true, status: 200, json: () => Promise.resolve(body) });
globalThis.fetch = (url) => {
  if (url.startsWith('/api/input/sources') || url.startsWith('/api/input/sides')) {
    return new Promise((resolve) => { pending[url.split('?')[0]] = () => resolve(json({ success: true, sources: [{ name: 'a' }], sides: [{ name: 'b' }] })); });
  }
  if (url.startsWith('/api/input/options-info')) return Promise.resolve(json({ success: true }));
  return Promise.resolve(json({ success: true, counts: {}, jobs: [] }));
};

const errors = [];
process.on('unhandledRejection', (reason) => errors.push(String((reason && reason.message) || reason)));

let failures = 0;
function check(name, cond, detail) {
  if (!cond) { failures += 1; console.log('FAIL', name, detail === undefined ? '' : detail); } else console.log('ok', name);
}
const tick = () => new Promise((r) => setTimeout(r, 20));

await import('../../static/js/pages/ingestion-studio-page.js');
await tick();

const writesBefore = counters.innerHtmlWrites;
if (MODE === 'stale') {
  // The user navigates away while the taxonomy request is still pending.
  PAGE_IDS.forEach((id) => absent.add(id));
}
Object.values(pending).forEach((resolve) => resolve());
await tick();
await tick();

if (MODE === 'stale') {
  check('a stale taxonomy response throws nothing into the page that replaced it',
    errors.length === 0, errors);
  check('a stale taxonomy response does not render the studio pickers',
    counters.innerHtmlWrites === writesBefore, `writes ${writesBefore} -> ${counters.innerHtmlWrites}`);
} else {
  check('a mounted page renders its pickers when sources arrive',
    counters.innerHtmlWrites > writesBefore, `writes ${writesBefore} -> ${counters.innerHtmlWrites}`);
  check('a mounted page throws nothing', errors.length === 0, errors);
}

console.log(failures ? `${failures} check(s) failed` : 'ingestion stale taxonomy: all checks passed');
process.exit(failures ? 1 : 0);
