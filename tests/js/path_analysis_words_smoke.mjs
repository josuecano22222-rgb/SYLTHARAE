// Runs the real loadPathWords() from static/js/pages/path-analysis-page.js with
// the vm DOM stub. A soft navigation can replace the page while the words
// request is in flight. The response must then be dropped quietly: it must not
// call the renderers, and so must not log "Word cloud container not found"
// into a page that no longer has a word cloud.
import fs from 'node:fs';
import vm from 'node:vm';
import { makeDom } from './_vm_dom.mjs';

const source = fs.readFileSync('static/js/pages/path-analysis-page.js', 'utf8')
  .replace(/^import \{ escapeAttribute \} from .*$/m, '')
  .replace(/^export default function init\(/m, 'function init(');

const { document } = makeDom();
const warnings = [];
const errors = [];
const fakeConsole = { log() {}, info() {}, debug() {}, warn: (...a) => warnings.push(String(a[0])), error: (...a) => errors.push(a.map((x) => String((x && x.message) || x)).join(' ')) };

let pendingWords = null;
let replaced = false;
const fetchImpl = (url) => {
  if (url.startsWith('/api/analytics/path/words')) {
    return new Promise((resolve) => {
      pendingWords = () => resolve({ ok: true, status: 200,
        json: () => Promise.resolve({ words: [{ text: 'alpha', count: 3 }, { text: 'beta', count: 1 }] }) });
    });
  }
  return new Promise(() => {}); // other requests are irrelevant here and never settle
};

class ChartStub { constructor() {} destroy() {} update() {} }
const ctx = {
  Chart: ChartStub,
  document: {
    readyState: 'complete',
    getElementById: (id) => (replaced && id === 'wordCloud' ? null : document.getElementById(id)),
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener() {},
    removeEventListener() {},
    createElement: (tag) => { const el = document.createElement(tag); if (!el.style) el.style = {}; return el; },
    createTextNode: document.createTextNode,
  },
  window: { location: { pathname: '/analysis/path', href: 'http://x/analysis/path', search: '' }, addEventListener() {} },
  fetch: fetchImpl, console: fakeConsole, escapeAttribute: (v) => String(v),
  translations: { loadingKeywords: 'LOADING', errorLoadingKeywords: 'ERR', noKeywordsFound: 'NONE', noExtractableKeywords: 'NONE2' },
  AbortController, URL, URLSearchParams, JSON, Promise, Object, Array, String, Number, Math, Set, Map,
  setTimeout, clearTimeout, setInterval: () => 0, clearInterval() {},
};
ctx.window.document = ctx.document;
vm.createContext(ctx);
vm.runInContext(source, ctx);
// Spy on the renderers at the boundary. The DOM stub cannot host the real
// renderers, and the contract under test is whether a response reaches them.
const rendered = [];
ctx.renderWordCloud = (words) => { rendered.push(['cloud', words.length]); };
ctx.renderWordFrequencyChart = (words) => { rendered.push(['chart', words.length]); };

let failures = 0;
function check(name, cond, detail) {
  if (!cond) { failures += 1; console.log('FAIL', name, detail === undefined ? '' : detail); } else console.log('ok', name);
}
const tick = () => new Promise((r) => setTimeout(r, 20));

// ---- 1: control. The page is still mounted, so the words are rendered.
{
  const wc = document.getElementById('wordCloud');
  wc.isConnected = true;
  vm.runInContext('currentLoadingPath = "A"; loadPathWords("A")', ctx);
  await tick();
  pendingWords(); await tick(); await tick();
  check('mounted page hands the words to the renderers', rendered.length === 2 && rendered[0][0] === 'cloud' && rendered[0][1] === 2, JSON.stringify({ rendered, warnings, errors }));
}

// ---- 2: the page is replaced while the request is pending.
{
  warnings.length = 0; errors.length = 0; rendered.length = 0;
  const wc = document.getElementById('wordCloud');
  wc.isConnected = true;
  const before = wc.innerHTML;
  vm.runInContext('currentLoadingPath = "B"; loadPathWords("B")', ctx);
  await tick();
  replaced = true; wc.isConnected = false; // soft nav swapped the content out
  pendingWords(); await tick(); await tick();
  check('replaced page: no "container not found" warning', !warnings.some((w) => /container not found|element not found/.test(w)), warnings.join(' | '));
  check('replaced page: no error logged', errors.length === 0, errors.join(' | '));
  check('replaced page: renderers are not called', rendered.length === 0, JSON.stringify(rendered));
  check('replaced page: detached node is left alone', wc.innerHTML === before, wc.innerHTML.slice(0, 80));
  replaced = false;
}

if (failures) { console.log(`${failures} failure(s)`); process.exit(1); }
