/**
 * Runtime check of the Horizon & Signal Explorer page module against a
 * running server.
 *
 *   node tools/verify/signals_page_runtime.mjs <base-url> <state-dir>
 *
 * Run after runtime_check_signals.py (it saves the page the live server
 * rendered and a logged-in session). The shipped static/js/pages/signals-page.js
 * is executed unmodified in the repository's DOM stub (tests/js/_dom_stub.mjs),
 * built from that live HTML; every fetch goes to the live server with the
 * session. What is checked is what a reader would see after each action.
 *
 * The stub is not a browser: layout, CSS and real bidi rendering are not
 * exercised (no headless browser is available in this environment). The few
 * DOM APIs the stub lacks (ParentNode.append, createTextNode, FormData,
 * requestSubmit) are provided below with standard semantics.
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

import { FakeElement, installDom, treeFromHtml } from '../../tests/js/_dom_stub.mjs';

const [base, stateDir] = process.argv.slice(2);
const html = readFileSync(path.join(stateDir, 'evidence', 'signals_page.html'), 'utf8');
const cookie = readFileSync(path.join(stateDir, 'session.txt'), 'utf8').trim();
const failures = [];
const check = (ok, message) => {
    console.log(`${ok ? 'PASS' : 'FAIL'} ${message}`);
    if (!ok) failures.push(message);
};

// --- the few standard DOM APIs the stub does not provide ----------------------
FakeElement.prototype.append = function append(...nodes) {
    for (const node of nodes) this.appendChild(node instanceof FakeElement ? node : document.createTextNode(node));
};
FakeElement.prototype.requestSubmit = function requestSubmit() { this.dispatch('submit'); };
FakeElement.prototype.toggleAttribute = function toggleAttribute(name, on) {
    if (on) this.setAttribute(name, ''); else this.removeAttribute(name);
};
function controls(node, out = []) {
    for (const child of node.children) {
        if (['INPUT', 'SELECT', 'TEXTAREA'].includes(child.tagName)) out.push(child);
        controls(child, out);
    }
    return out;
}
globalThis.FormData = class {
    constructor(form) {
        this.pairs = [];
        for (const c of controls(form)) {
            const name = c.getAttribute('name');
            if (!name || c.hasAttribute('disabled')) continue;
            if (c.getAttribute('type') === 'checkbox') {
                if (c.checked) this.pairs.push([name, c.getAttribute('value')]);
            } else if (c.tagName === 'SELECT') {
                for (const o of c.children.filter((o) => o.selected)) this.pairs.push([name, o.getAttribute('value')]);
            } else {
                this.pairs.push([name, c.value]);
            }
        }
    }
    entries() { return this.pairs[Symbol.iterator](); }
};

// --- the live page -------------------------------------------------------------
const { documentRoot, document } = installDom();
document.createTextNode = (text) => { const n = new FakeElement('#text'); n._text = String(text); return n; };
globalThis.Node = FakeElement;
const body = html.replace(/<!DOCTYPE[^>]*>/i, '').replace(/<!--[\s\S]*?-->/g, '');
documentRoot.appendChild(treeFromHtml(body));
const offcanvasShown = [];
globalThis.bootstrap.Offcanvas = { getOrCreateInstance: (node) => ({ show: () => offcanvasShown.push(node) }) };
const requests = [];
const nativeFetch = globalThis.fetch;
globalThis.fetch = (url, options = {}) => {
    requests.push(url);
    return nativeFetch(new URL(url, base), { ...options, headers: { ...(options.headers || {}), Cookie: cookie } });
};

const byId = (id) => document.getElementById(id);
const rows = (id) => byId(id).children.filter((r) => r.tagName === 'TR');
async function settle() {
    for (let i = 0; i < 100; i += 1) {
        const before = requests.length;
        await new Promise((resolve) => setTimeout(resolve, 50));
        if (requests.length === before) {
            await new Promise((resolve) => setTimeout(resolve, 150));
            if (requests.length === before) return;
        }
    }
}
function fillReference(value) { byId('fReferenceDate').value = value; }

// Reference date is set before the module's first request, as a reader would.
fillReference('2026-10-01');
const signalsPage = await import(pathToFileURL(path.resolve('static/js/pages/signals-page.js')).href);
if (typeof signalsPage.default === 'function') signalsPage.default();
await settle();

check(requests.some((u) => u.startsWith('/api/signals/horizon?') && u.includes('reference_date=2026-10-01')),
    'initial load asks the horizon API with the reference date');
const cards = byId('horizonBuckets').children;
check(cards.length === 6, `six bucket cards rendered (${cards.length})`);
const cardText = cards.map((c) => c.textContent.replace(/\s+/g, ' ').trim());
check(cardText[1].includes('2') && cardText[0].includes('1'), `bucket counts shown: ${cardText.join(' | ')}`);
check(rows('horizonRows').length === 7, `seven horizon rows (${rows('horizonRows').length})`);
const notices = byId('horizonNotices').textContent;
check(/1 references have no single date/.test(notices), 'undated references are reported, not dropped');
check(/All 2 matching documents/.test(notices), 'coverage notice: every matching document measured');
const firstRow = rows('horizonRows')[0];
const bdi = firstRow.querySelectorAll('bdi');
check(bdi.length > 0 && bdi.every((b) => b.getAttribute('dir') === 'auto'), 'document text is isolated with dir="auto"');
check(byId('signalError').classList.contains('d-none'), 'no error shown');

// Click the "week" card: only that bucket is listed.
cards[1].querySelector('button').dispatch('click');
await settle();
check(requests.at(-1).includes('bucket=week'), 'clicking a bucket asks for that bucket');
check(rows('horizonRows').length === 2, `week lists two references (${rows('horizonRows').length})`);
cards[1].querySelector('button').dispatch('click');   // toggle back
await settle();

// Language filter: Persian only.
byId('f_language_fa').checked = true;
byId('signalFilters').dispatch('submit');
await settle();
check(requests.at(-1).includes('language=fa'), 'the language filter reaches the API');
const faRows = rows('horizonRows');
check(faRows.length === 1 && faRows[0].textContent.includes('۱۵ مهر ۱۴۰۵'), 'Persian filter lists the Jalali date');
byId('f_language_fa').checked = false;

// Explorer tab: facets and both detectors.
byId('tabExplorer').dispatch('click');
await settle();
check(requests.at(-1).startsWith('/api/signals?'), 'explorer tab asks the explorer API');
check(!byId('panelExplorer').classList.contains('d-none') && byId('panelHorizon').classList.contains('d-none'),
    'explorer panel shown, horizon hidden');
const summary = byId('explorerSummary').textContent;
check(/17 signals in 2 documents/.test(summary), `explorer summary: ${summary}`);
check(byId('explorerFacets').textContent.includes('places'), 'facets list the places detector');
check(rows('explorerRows').length === 17, `explorer rows (${rows('explorerRows').length})`);

// Detail panel for the ambiguous Tripoli mention.
// Match on the signal cell: Zagreb and Tripoli share one evidence sentence.
const tripoliRow = rows('explorerRows').find((r) => r.children[0].querySelector('button').textContent === 'Tripoli');
tripoliRow.querySelector('button').dispatch('click');
await settle();
const detail = byId('signalDetailBody').textContent;
check(offcanvasShown.length === 1, 'detail panel opened');
check(detail.includes('Ambiguous between:') && detail.includes('/runtime/alpha.txt'), 'detail lists candidates and the occurrence');
check(detail.includes('Structured evidence') && detail.includes('gazetteer match, not named-entity recognition'),
    'detail shows the stored structured evidence');

// A request the server refuses is shown to the reader, not swallowed.
fillReference('not-a-date');
byId('signalFilters').dispatch('submit');
await settle();
const error = byId('signalError');
check(!error.classList.contains('d-none') && /reference_date/.test(error.textContent), `error surfaced: ${error.textContent}`);

console.log(`\n${failures.length} failure(s); ${requests.length} requests to the live server`);
process.exit(failures.length ? 1 : 0);
