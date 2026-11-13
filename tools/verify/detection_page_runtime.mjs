/**
 * Runtime check of the Detection page module against a running server.
 *
 *   node tools/verify/detection_page_runtime.mjs <base-url> <state-dir>
 *
 * Run after runtime_check_detection.py (it saves the page the live server
 * rendered and an administrator's session). The shipped
 * static/js/pages/detection-page.js is executed unmodified in the
 * repository's DOM stub (tests/js/_dom_stub.mjs), built from that live HTML;
 * every fetch goes to the live server with the session, and writes carry the
 * page's CSRF token as a browser would. Checked: the coverage table shows the
 * server's numbers; run rows link their file; a filter narrows on the server;
 * a bad content id is refused before any request; a listed-ids re-detection
 * submitted through the form is followed as a job to its end, after which
 * the page reloads and the server has the rewritten run.
 *
 * The stub is not a browser: layout, CSS and real bidi rendering are not
 * exercised (no headless browser is available in this environment).
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

import { FakeElement, installDom, treeFromHtml } from '../../tests/js/_dom_stub.mjs';

const [base, stateDir] = process.argv.slice(2);
const html = readFileSync(path.join(stateDir, 'evidence', 'detection_page.html'), 'utf8');
const cookie = readFileSync(path.join(stateDir, 'detection_session.txt'), 'utf8').trim();
const env = JSON.parse(readFileSync(path.join(stateDir, 'env.json'), 'utf8'));
const failures = [];
const check = (ok, message) => {
    console.log(`${ok ? 'PASS' : 'FAIL'} ${message}`);
    if (!ok) failures.push(message);
};

FakeElement.prototype.append = function append(...nodes) {
    for (const node of nodes) {
        if (node === null || node === undefined) throw new Error(`append(${node}) would insert the text "${node}"`);
        this.appendChild(node instanceof FakeElement ? node : document.createTextNode(node));
    }
};
// Standard replaceChildren(...nodes): the stub's ignores its arguments.
const stubReplace = FakeElement.prototype.replaceChildren;
FakeElement.prototype.replaceChildren = function replaceChildren(...nodes) {
    stubReplace.call(this);
    this.append(...nodes);
};

const { documentRoot, document } = installDom();
document.createTextNode = (text) => { const n = new FakeElement('#text'); n._text = String(text); return n; };
globalThis.Node = FakeElement;
const body = html.replace(/<!DOCTYPE[^>]*>/i, '').replace(/<!--[\s\S]*?-->/g, '');
documentRoot.appendChild(treeFromHtml(body));
const nativeQuery = document.querySelector;
document.querySelector = (selector) => {
    const m = /^(\w+)\[([\w-]+)="([^"]*)"\]$/.exec(selector);
    if (!m) return nativeQuery(selector);
    return documentRoot.querySelectorAll(m[1]).find((n) => n.getAttribute(m[2]) === m[3]) || null;
};
globalThis.window = globalThis.window || globalThis;
globalThis.window.location = { href: `${base}/signals/detection`, search: '' };

const requests = [];
const nativeFetch = globalThis.fetch;
globalThis.fetch = (url, options = {}) => {
    requests.push(`${options.method || 'GET'} ${url}`);
    return nativeFetch(new URL(url, base), { ...options, headers: { ...(options.headers || {}), Cookie: cookie } });
};
const api = async (p) => (await nativeFetch(new URL(p, base), { headers: { Cookie: cookie, Accept: 'application/json' } })).json();

const byId = (id) => document.getElementById(id);
const rows = (id) => byId(id).children.filter((r) => r.tagName === 'TR');
const text = (id) => byId(id).textContent.replace(/\s+/g, ' ').trim();
async function settle(quietMs = 400) {
    for (let i = 0; i < 400; i += 1) {
        const before = requests.length;
        await new Promise((resolve) => setTimeout(resolve, quietMs));
        if (requests.length === before) return;
    }
}
function fire(node, type) {
    if (node.disabled) throw new Error(`${node.getAttribute('id') || node.tagName} is disabled`);
    node.dispatch(type, { preventDefault() {} });
}

const labels = JSON.parse(byId('detection-page-labels').textContent);

await import(pathToFileURL(path.resolve('static/js/pages/detection-page.js')).href);
await settle();

check(requests.some((r) => r.startsWith('GET /api/signals/detection/status'))
      && requests.some((r) => r.startsWith('GET /api/signals/detection/runs')), `initial requests: ${requests.join(', ')}`);
const status = await api('/api/signals/detection/status');
const cov = rows('coverageRows');
check(cov.length === status.detectors.length, `one coverage row per detector (${cov.length})`);
for (const [i, d] of status.detectors.entries()) {
    const cells = cov[i].children.map((c) => c.textContent.trim());
    check(cells[0].includes(d.detector) && cells[7] === Number(d.never_analysed).toLocaleString()
          && cells[8] === Number(d.stale).toLocaleString(),
          `${d.detector}: never ${cells[7]}, stale ${cells[8]} as the server says`);
    const action = cov[i].children[9];
    check(d.stale > 0 ? action.textContent.includes(labels.redetect_stale) : action.textContent.includes(labels.nothing_stale),
          `${d.detector}: action cell "${action.textContent.trim()}"`);
}
check(text('coverageNote').includes(Number(status.analysable_contents).toLocaleString()), `note: ${text('coverageNote')}`);

const runsApi = await api('/api/signals/detection/runs?limit=50');
const runRows = rows('runRows');
check(runRows.length === runsApi.items.length, `run rows = server page (${runRows.length})`);
const link = runRows[0].querySelectorAll('a').find((a) => (a.getAttribute('href') || '').startsWith('/file/'));
check(link && link.getAttribute('href') === `/file/${runsApi.items[0].path_id}`, `first run links ${link && link.getAttribute('href')}`);

byId('runDetector').value = 'places';
fire(byId('runFilters'), 'submit');
await settle();
check(requests.at(-1).includes('detector=places'), `filter sent (${requests.at(-1)})`);
check(rows('runRows').length > 0 && rows('runRows').every((r) => r.getAttribute('data-detector') === 'places' && r.children[3].textContent === 'places'),
      'every row is a places run');
byId('runDetector').value = '';

// Bad id: refused locally.
byId('redetectScope').value = 'hash_ids';
byId('redetectScope').dispatch('change', {});
byId('redetectIds').value = '1 abc';
const before = requests.length;
fire(byId('redetectForm'), 'submit');
await settle();
check(requests.length === before && text('detectionError').includes('abc'), `bad id refused locally: ${text('detectionError')}`);

// Listed id through the form, followed as a job.
const hashId = Object.entries(env.ids).filter(([k]) => k.startsWith('hash_')).sort()[1][1];
byId('redetectIds').value = String(hashId);
byId('redetect_places').checked = false;
byId('redetect_temporal').checked = true;
fire(byId('redetectForm'), 'submit');
await settle(1500);
const post = requests.find((r) => r === 'POST /api/signals/redetect');
check(Boolean(post), 'POST /api/signals/redetect sent');
const jobGets = requests.filter((r) => r.startsWith('GET /api/jobs/'));
check(jobGets.length >= 1, `job followed (${jobGets.length} polls)`);
check(/: COMPLETED(_WITH_WARNINGS)?\./.test(text('redetectJob')) && text('redetectJob').includes('1'),
      `job line states the terminal status and the server's counts: ${text('redetectJob')}`);
const lastStatus = requests.map((r, i) => [r, i]).filter(([r]) => r.startsWith('GET /api/signals/detection/status')).at(-1)[1];
check(lastStatus > requests.indexOf(post), 'coverage reloaded after the job');
const jobId = jobGets[0].split('/').at(-1);
const run = (await api(`/api/signals/detection/runs?hash_id=${hashId}&detector=temporal`)).items[0];
check(run && run.trigger === 'redetection' && run.job_id === jobId, `server run: ${run && run.trigger} by ${run && run.job_id}`);

console.log(`\n${failures.length} failure(s), ${requests.length} requests`);
process.exit(failures.length ? 1 : 0);
