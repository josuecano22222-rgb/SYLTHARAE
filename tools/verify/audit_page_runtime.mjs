/**
 * Runtime check of the Audit Log page module against a running server.
 *
 *   node tools/verify/audit_page_runtime.mjs <base-url> <state-dir>
 *
 * Run after runtime_check_audit.py (it saves the page the live server
 * rendered and an administrator's session). The shipped
 * static/js/pages/audit-page.js is executed unmodified in the repository's
 * DOM stub (tests/js/_dom_stub.mjs), built from that live HTML; every fetch
 * goes to the live server with the session. Checked: what a reader sees
 * after the initial load, a filter, Older/Newer paging, a click on a
 * resource, the detail panel and a refused filter.
 *
 * The stub is not a browser: layout, CSS and real bidi rendering are not
 * exercised (no headless browser is available in this environment).
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';

import { FakeElement, installDom, treeFromHtml } from '../../tests/js/_dom_stub.mjs';

const [base, stateDir] = process.argv.slice(2);
const html = readFileSync(path.join(stateDir, 'evidence', 'audit_page.html'), 'utf8');
const cookie = readFileSync(path.join(stateDir, 'audit_session.txt'), 'utf8').trim();
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
globalThis.window = globalThis.window || globalThis;
globalThis.window.location = { href: `${base}/admin/audit`, search: '' };

const requests = [];
const nativeFetch = globalThis.fetch;
globalThis.fetch = (url, options = {}) => {
    requests.push(`${options.method || 'GET'} ${url}`);
    return nativeFetch(new URL(url, base), { ...options, headers: { ...(options.headers || {}), Cookie: cookie } });
};

const byId = (id) => document.getElementById(id);
const rows = () => byId('auditRows').children.filter((r) => r.tagName === 'TR');
const text = (id) => byId(id).textContent.replace(/\s+/g, ' ').trim();
const hidden = (id) => byId(id).classList.contains('d-none');
async function settle(quietMs = 300) {
    for (let i = 0; i < 200; i += 1) {
        const before = requests.length;
        await new Promise((resolve) => setTimeout(resolve, quietMs));
        if (requests.length === before) return;
    }
}
function fire(node, type) {
    // A browser does not deliver clicks to a disabled button.
    if (node.disabled) throw new Error(`${node.getAttribute('id') || node.tagName} is disabled`);
    node.dispatch(type, { preventDefault() {} });
}
const detailButton = (row) => row.children[5].children[0];

const labels = JSON.parse(byId('audit-page-labels').textContent);
const pageData = JSON.parse(byId('audit-page-data').textContent);

await import(path.resolve('static/js/pages/audit-page.js'));
await settle();

check(requests[0] === 'GET /api/audit/actions', `first request is the action menu (${requests[0]})`);
const actionOptions = byId('auditAction').children.map((o) => o.getAttribute('value'));
check(actionOptions[0] === '' && actionOptions.includes('DATA_EXPORTED') && actionOptions.includes('report.run'),
      `action menu from the server (${actionOptions.length - 1} actions)`);
check(rows().length === pageData.page_size, `first page has the server page size (${rows().length})`);
check(text('auditNote').includes(labels.more) && text('auditNote').includes(labels.not_counted),
      `note: ${text('auditNote')}`);
check(byId('auditNewer').disabled === true && byId('auditOlder').disabled === false, 'Newer off, Older on');
check(hidden('auditError'), 'no error after load');

// Older then Newer on the unfiltered log (several pages).
const firstPage = rows().map((r) => detailButton(r).getAttribute('data-entry'));
fire(byId('auditOlder'), 'click');
await settle();
const secondPage = rows().map((r) => detailButton(r).getAttribute('data-entry'));
check(requests.at(-1).includes(`before_id=${firstPage.at(-1)}`), `Older uses the server cursor (${requests.at(-1)})`);
check(secondPage.length > 0 && Number(secondPage[0]) < Number(firstPage.at(-1)), 'Older page is older, no overlap');
check(byId('auditNewer').disabled === false, 'Newer enabled on the second page');
fire(byId('auditNewer'), 'click');
await settle();
check(JSON.stringify(rows().map((r) => detailButton(r).getAttribute('data-entry'))) === JSON.stringify(firstPage),
      'Newer returns the same first page');

// Filter by action through the form; fewer than a page, so Older is off.
byId('auditAction').value = 'DATA_EXPORTED';
fire(byId('auditForm'), 'submit');
await settle();
check(requests.at(-1).includes('action=DATA_EXPORTED') && !requests.at(-1).includes('before_id'),
      `filter sent to the server (${requests.at(-1)})`);
check(rows().length > 0 && rows().every((r) => r.children[2].textContent === 'DATA_EXPORTED'), 'every row is DATA_EXPORTED');
check(byId('auditOlder').disabled === (rows().length < pageData.page_size) && text('auditNote').includes(
      rows().length < pageData.page_size ? labels.no_more : labels.more), `note: ${text('auditNote')}`);

// Click a resource: filter by its prefix.
const resourceButton = rows()[0].children[3].children[0];
const resource = resourceButton.textContent;
fire(resourceButton, 'click');
await settle();
check(byId('auditResource').value === resource && requests.at(-1).includes('resource='),
      `resource click filters on ${resource}`);
check(rows().every((r) => r.children[3].textContent.startsWith(resource)), 'every row starts with that resource');

// Detail panel.
fire(detailButton(rows()[0]), 'click');
await settle();
const detail = JSON.parse(byId('auditDetailJson').textContent);
check(!hidden('auditDetail') && typeof detail === 'object' && detail !== null,
      `detail panel shows the JSON (${Object.keys(detail).length} keys)`);
check(/^[0-9a-f]{64}$/.test((detail.artifact && detail.artifact.sha256) || detail.manifest_sha256 || ''),
      'the detail carries the digest');

// A refused filter: the server's message verbatim.
byId('auditSince').value = '2030-01-02T00:00';
byId('auditUntil').value = '2030-01-01T00:00';
fire(byId('auditForm'), 'submit');
await settle();
check(!hidden('auditError') && text('auditError').includes('since must be earlier than until (VALIDATION_FAILED)'),
      `refused filter shown verbatim: ${text('auditError')}`);

// Clear.
fire(byId('auditReset'), 'click');
await settle();
check(hidden('auditError') && rows().length === pageData.page_size && !requests.at(-1).includes('action='),
      'Clear returns to the unfiltered first page');

console.log(`\n${requests.length} requests, ${failures.length} failure(s)`);
process.exit(failures.length ? 1 : 0);
