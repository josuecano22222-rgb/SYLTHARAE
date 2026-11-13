// Runs the real job-detail and import-center page modules against a minimal
// DOM and checks that server-provided error text is rendered as text.
import fs from 'node:fs';
import vm from 'node:vm';

const HOSTILE = '<img src=x onerror="window.__pwned=1">';
const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

function makeDom() {
  const els = {};
  function el(tag = 'div') {
    const e = {
      tagName: tag, _text: '', _html: '', value: '', className: '', children: [], listeners: {},
      classList: { add() {}, remove() {}, toggle() {} }, style: {}, dataset: {}, setAttribute() {}, removeAttribute() {},
      files: [],
      addEventListener(t, f) { (this.listeners[t] ||= []).push(f); },
      replaceChildren(...c) { this.children = c; this._html = c.map(x => x.outerHTML).join(''); },
      prepend() {}, appendChild(c) { this.children.push(c); },
      get textContent() { return this._text; },
      set textContent(v) { this._text = String(v); this._html = esc(v); },
      get innerHTML() { return this._html; },
      set innerHTML(v) { this._html = String(v); },
      get outerHTML() { return `<${tag} class="${this.className}">${this._html}</${tag}>`; },
    };
    return e;
  }
  const document = {
    getElementById(id) { return (els[id] ||= el()); },
    createElement: el,
    querySelectorAll() { return []; }, querySelector() { return null; },
    readyState: 'complete',
    addEventListener() {},
  };
  document.getElementById('job-detail-page-data').textContent = JSON.stringify({ jobId: 'J1' });
  document.getElementById('import-center-page-data').textContent = JSON.stringify({});
  return { document, els };
}

async function run(file, fetchImpl) {
  const { document, els } = makeDom();
  const ctx = { document, window: { location: { pathname: '/operations/import' } },
                fetch: fetchImpl, setInterval() {}, alert() {},
                console, JSON, Promise, Object, String, Math, FormData: class {} };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(file, 'utf8'), ctx);
  await new Promise(r => setTimeout(r, 20));
  return { els, ctx };
}
const ok = (body) => Promise.resolve({ json: () => Promise.resolve(body) });
let failures = 0;
function check(name, cond, detail) {
  if (!cond) { failures++; console.log('FAIL', name, detail || ''); } else console.log('ok', name);
}

// 1. job detail: errors and warnings
{
  const { els } = await run('static/js/pages/job-detail-page.js', (url) => {
    if (url.endsWith('/errors')) return ok({ errors: [HOSTILE], warnings: [HOSTILE] });
    if (url.includes('/events')) return ok({ events: [] });
    return ok({ success: true, job: { status: 'FAILED', statistics: {}, errors: [], warnings: [] } });
  });
  for (const f of els.errFilter.listeners.input || []) f();
  const html = els.jErrors.innerHTML;
  check('job errors rendered', html.includes('&lt;img'), html);
  check('job errors not injected as markup', !html.includes('<img'), html);
}

// 2. import center: failure message
{
  const { els } = await run('static/js/pages/import-center-page.js', (url) => {
    if (url.includes('/api/input/')) return ok({ sources: [{ name: HOSTILE }], sides: [] });
    return ok({ success: false, error: { message: HOSTILE } });
  });
  check('source options escaped', !els.biSource.innerHTML.includes('<img'), els.biSource.innerHTML);
  for (const f of els.diValidate.listeners.click || []) await f();
  const box = els.importMsg.children[0];
  check('message shown', box && box.textContent === HOSTILE, box && box.textContent);
  check('message not injected as markup', !els.importMsg.innerHTML.includes('<img'), els.importMsg.innerHTML);
}
process.exit(failures ? 1 : 0);
