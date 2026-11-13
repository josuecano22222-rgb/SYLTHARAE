"""Unit: the file list must survive filenames it did not choose.

Reported from the running application (browser console, archives page):

    archives:1 Uncaught SyntaxError: missing ) after argument list
    (repeated)

The file cards carried their whole payload inside inline handlers:

    onclick="showFileDetails?.(12, '${safeName}', ${JSON.stringify(files)}, 0)"

safeName came from escapeHtml(), which escapes & < > but NOT quotes - it
builds its output through a text node, and a text node never contains a quote.
So a filename with an apostrophe (an archive of documents is full of them)
terminated the JavaScript string early and the browser refused to compile the
handler: "missing ) after argument list". The card did nothing when clicked,
and every file in that listing was affected. A name containing a double quote
broke out of the attribute in the same way.

The fix is structural, not another escape: identity lives in data attributes
(escaped for attribute context) and one delegated listener reads them, so no
filename is ever parsed as code.

These tests render the SHIPPED card renderers under Node - not a copy of the
markup - and then do what a browser does: compile every inline handler in the
output and check the attributes an HTML parser would see.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

MODULE = PROJECT_ROOT / "static/js/modules/views/file-view.js"
RUNTIME = PROJECT_ROOT / "static/js/modules/core/declarative-events.js"

DOM_STUB = r"""
// Minimal DOM stub: escapeHtml() serialises through a text node in the real
// browser, which escapes & < > and NOT quotes. Reproduced exactly - a stub
// that over-escaped would hide the defect this suite exists to catch.
const textSerialise = (t) => String(t)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

class El {
    constructor(tag = 'div') {
        this.tagName = String(tag).toUpperCase();
        this._html = '';
        this._attrs = {};
        this.style = {};
        this.dataset = {};
        this.children = [];
        this.classList = { add(){}, remove(){}, toggle(){}, contains(){ return false; } };
    }
    set textContent(v) { this._text = String(v); }
    get textContent() { return this._text == null ? '' : this._text; }
    get innerHTML() { return this._text != null ? textSerialise(this._text) : this._html; }
    set innerHTML(v) { this._html = String(v); this._text = null; }
    setAttribute(k, v) { this._attrs[k] = String(v); }
    getAttribute(k) { return this._attrs[k] === undefined ? null : this._attrs[k]; }
    removeAttribute(k) { delete this._attrs[k]; }
    addEventListener() {}
    removeEventListener() {}
    appendChild(c) { this.children.push(c); return c; }
    querySelector() { return null; }
    querySelectorAll() { return []; }
    focus() {}
    scrollIntoView() {}
}

// A DOM node good enough to walk: attributes + parent chain + closest().
class Node2 extends El {
    constructor(tag, attrs = {}, parent = null) {
        super(tag);
        for (const [k, v] of Object.entries(attrs)) this._attrs[k] = String(v);
        this.parentElement = parent;
    }
    closest(selector) {
        const parts = String(selector).split(',').map(s => s.trim());
        let node = this;
        while (node) {
            for (const part of parts) {
                const m = /^([a-z]*)\[([-\w]+)\]$/.exec(part);
                if (m) {
                    if ((!m[1] || node.tagName === m[1].toUpperCase())
                        && node.getAttribute(m[2]) !== null) return node;
                    continue;
                }
                const cls = /^\.([-\w]+)$/.exec(part);
                if (cls) {
                    if (node.getAttribute('class') !== null
                        && node.getAttribute('class').split(/\s+/).includes(cls[1])) return node;
                    continue;
                }
                if (/^[a-z]+$/.test(part) && node.tagName === part.toUpperCase()) return node;
            }
            node = node.parentElement;
        }
        return null;
    }
}
globalThis.Node2 = Node2;
globalThis.el = El;

const store = {};
globalThis.document = {
    createElement: (tag) => new El(tag),
    getElementById: (id) => {
        if (!store[id]) store[id] = new El('div');
        return store[id];
    },
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener() {}, removeEventListener() {},
    body: new El('body'), documentElement: new El('html')
};
globalThis.window = globalThis;
globalThis.localStorage = { getItem(){ return null; }, setItem(){}, removeItem(){} };
globalThis.requestAnimationFrame = () => 0;
globalThis.fetch = async () => ({ ok: false, status: 0, json: async () => ({}) });
globalThis.captured = () => {
    const el = store['unifiedContentView'];
    return el ? el.innerHTML : '';
};
"""

HARNESS = r"""
import { readFileSync } from 'node:fs';
import '__STUB__';
const mod = await import('__MODULE__');

// Filenames an archive can legitimately contain, including the two that used
// to break the markup: an apostrophe and a double quote.
const names = [
    "John's report.pdf",
    'The "final" draft.docx',
    "O'Brien & Sons <2026>.xlsx",
    "plain-name.txt",
    "back\\slash\\file.txt"
];
const files = names.map((name, i) => ({
    id: 100 + i, name, size: 1234, type: 'FILE', file_type: 'FILE'
}));
const pagination = { page: 1, per_page: 50, total: files.length,
                     total_pages: 1, total_size: 100, has_prev: false, has_next: false };

mod.renderFilesView(files, 'category', "Item's name", pagination);
const html = globalThis.captured();

// --- What a browser does with the emitted handlers -----------------------
// Parse every declarative handler with the shipped runtime (RES-CSP-01: the
// handlers are data-on-<event> attributes, run without inline script). A
// syntax error here is the defect the operator reported as "missing ) after
// argument list" when these were inline handlers.
new Function(readFileSync(__RUNTIME_PATH__, 'utf8'))();
const runtime = globalThis.SyltharaeDeclarativeEvents;
const decode = (v) => v.replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&#x27;/g, "'")
    .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
const handlerErrors = [];
let handlerCount = 0;
for (const m of html.matchAll(/\sdata-on-([a-z]+)\s*=\s*"([^"]*)"/gi)) {
    handlerCount++;
    try {
        runtime.parse(decode(m[2]));
    } catch (e) {
        handlerErrors.push({ event: m[1], handler: m[2].slice(0, 120), error: e.message });
    }
}

// --- What an HTML parser sees: the attribute list of every card -----------
const parseAttrs = (inner) => {
    const names = [];
    let i = 0;
    while (i < inner.length) {
        while (i < inner.length && /\s/.test(inner[i])) i++;
        if (i >= inner.length) break;
        const nm = /^[A-Za-z_:][-A-Za-z0-9_:.]*/.exec(inner.slice(i));
        if (!nm) { i++; continue; }
        names.push(nm[0]);
        i += nm[0].length;
        if (inner[i] === '=') {
            i++;
            const q = inner[i];
            if (q === '"' || q === "'") {
                i++;
                while (i < inner.length && inner[i] !== q) i++;
                i++;
            } else {
                while (i < inner.length && !/[\s>]/.test(inner[i])) i++;
            }
        }
    }
    return names;
};
const cardTags = [...html.matchAll(/<(div|a|button|input|span)\b([^>]*)>/g)]
    .map(t => ({ tag: t[1], attrs: parseAttrs(t[2]) }));
// No inline handler at all; declarative ones only for the events a card uses.
const eventAttrs = cardTags.flatMap(t => t.attrs.filter(a => /^(data-)?on/i.test(a)));
const unexpectedEventAttrs = eventAttrs.filter(a => !['data-on-click', 'data-on-mouseover', 'data-on-mouseout', 'data-on-input', 'data-on-change'].includes(a.toLowerCase()));

// The stored row the handler used to embed: no filename may appear as code.
const handlerTexts = [...html.matchAll(/\s(?:data-)?on-?[a-z]+\s*=\s*"([^"]*)"/gi)].map(m => m[1]);
const nameInHandler = handlerTexts.some(h => h.includes('report.pdf') || h.includes('final'));

// --- Wiring: the details action must still be reachable ------------------
const dataIds = [...html.matchAll(/data-file-id="(\d+)"/g)].map(m => m[1]);
const dataActions = [...html.matchAll(/data-file-action="([a-z-]+)"/g)].map(m => m[1]);

// --- Behaviour: the delegated handler must still open the right file ----
// Build the node chain a browser would give the listener: the button inside
// the card, with the attributes the markup above emitted. On the old code the
// handler does not exist at all - that is reported, not crashed on, so the
// compile test above still carries the reported defect.
const hasHandler = typeof mod.handleFileCardClick === 'function';
const calls = [];
globalThis.window.showFileDetails = (id, name, files, index) =>
    calls.push({ kind: 'open', id, name, files, index });
globalThis.window.exportFile = (id) => calls.push({ kind: 'export', id });
globalThis.window.openAnalyticalReportForFile = (id) => calls.push({ kind: 'report', id });
globalThis.window.exportFileWithRename = (id) => calls.push({ kind: 'saveas', id });
globalThis.window.openInNativeApp = (id) => calls.push({ kind: 'native', id });
globalThis.window.locateFile = (id) => calls.push({ kind: 'locate', id });

const card = new Node2('div', {
    class: 'file-card', 'data-file-id': '101', 'data-file-action': 'open',
    'data-file-name': 'The \"final\" draft.docx', 'data-file-index': '1'
});
const openBtn = new Node2('button', { class: 'action-btn', 'data-file-action': 'open' }, card);
const exportBtn = new Node2('button', { class: 'action-btn export-btn', 'data-file-action': 'export' }, card);
const reportBtn = new Node2('button', { class: 'action-btn fmas-report-btn', 'data-file-action': 'report' }, card);
const saveAsBtn = new Node2('button', { class: 'action-btn', 'data-file-action': 'saveas' }, card);
const nativeBtn = new Node2('button', { class: 'action-btn', 'data-file-action': 'native' }, card);
const locateBtn = new Node2('button', { class: 'action-btn', 'data-file-action': 'locate' }, card);
const checkbox = new Node2('input', { class: 'file-checkbox', type: 'checkbox' }, card);
const link = new Node2('a', { class: 'action-btn', 'data-file-action': 'full-view', href: '/file/101' }, card);
const nameLabel = new Node2('div', { class: 'file-card-name' }, card);

// The name the handler passes on is the UNescaped one, as the browser reads it
// back out of the attribute.
if (hasHandler) {
    mod.handleFileCardClick({ target: openBtn });
    mod.handleFileCardClick({ target: exportBtn });
    mod.handleFileCardClick({ target: reportBtn });
    mod.handleFileCardClick({ target: saveAsBtn });
    mod.handleFileCardClick({ target: nativeBtn });
    mod.handleFileCardClick({ target: locateBtn });
    mod.handleFileCardClick({ target: nameLabel });
}
const nativeClicks = hasHandler ? [
    mod.handleFileCardClick({ target: checkbox }),
    mod.handleFileCardClick({ target: link })
] : null;

process.stdout.write(JSON.stringify({
    html,
    handlerCount,
    handlerErrors,
    unexpectedEventAttrs,
    eventAttrs,
    nameInHandler,
    dataIds,
    dataActions,
    calls,
    hasHandler,
    nativeClicks,
    // A raw quote is fine as VISIBLE TEXT (that is the filename shown),
    // never inside a tag - there it would end the attribute early.
    rawQuotedNameInTagAttrs: [...html.matchAll(/<[a-zA-Z][^>]*>/g)]
        .some(t => /The "final"/.test(t[0]))
}));
"""


@pytest.fixture(scope="module")
def node():
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    return shutil.which("node")


@pytest.fixture(scope="module")
def rendered(node, tmp_path_factory):
    assert MODULE.exists(), f"shipped module missing: {MODULE}"
    tmp = tmp_path_factory.mktemp("jsfilecards")
    stub = tmp / "domstub.mjs"
    stub.write_text(DOM_STUB)
    harness = tmp / "harness.mjs"
    harness.write_text(
        HARNESS.replace("__STUB__", stub.as_uri()).replace("__MODULE__", MODULE.as_uri())
        .replace("__RUNTIME_PATH__", json.dumps(str(RUNTIME)))
    )
    proc = subprocess.run([node, str(harness)], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"node failed: {proc.stderr[:2000]}"
    return json.loads(proc.stdout)


def test_inline_handlers_compile(rendered):
    """The reported failure: a handler the browser cannot compile."""
    assert rendered["handlerErrors"] == [], rendered["handlerErrors"]
    assert rendered["handlerCount"] > 0, "no inline handler found - did rendering change?"


def test_no_extra_event_attribute_appears(rendered):
    """A filename with a quote must not become an attribute of its own."""
    assert rendered["unexpectedEventAttrs"] == [], rendered["unexpectedEventAttrs"]


def test_filename_is_never_embedded_in_a_handler(rendered):
    """Clicking is wired by data attributes; names are not code."""
    assert rendered["nameInHandler"] is False, rendered["html"][:2000]


def test_every_file_row_carries_its_identity_as_data(rendered):
    assert rendered["dataIds"] == ["100", "101", "102", "103", "104"], rendered["dataIds"]


def test_details_action_is_wired_through_delegation(rendered):
    """The card and its button both expose the action the delegate listens for."""
    assert rendered["dataActions"].count("open") >= 5, rendered["dataActions"]


def test_hostile_names_stay_readable_text(rendered):
    """The name is still shown to the user - escaped, not dropped."""
    html = rendered["html"]
    assert "John&#39;s report.pdf" in html or "John's report.pdf" in html, html[:1500]
    assert "<script" not in html.lower()


def test_quotes_inside_attributes_are_escaped(rendered):
    """A double quote in a filename must not survive raw inside a tag."""
    assert rendered["rawQuotedNameInTagAttrs"] is False, rendered["html"][:2000]
    assert "&quot;" in rendered["html"], "attribute escaping did not run"


def test_clicking_a_card_opens_that_file(rendered):
    """The action still works - with the real filename read back from data."""
    assert rendered["hasHandler"] is True, "no delegated handler exported"
    opens = [c for c in rendered["calls"] if c["kind"] == "open"]
    assert len(opens) == 2, rendered["calls"]  # the title area and the button
    assert {c["id"] for c in opens} == {101}, opens
    assert {c["name"] for c in opens} == {'The "final" draft.docx'}, opens
    assert {c["index"] for c in opens} == {1}, opens


def test_export_button_exports_instead_of_opening(rendered):
    assert rendered["hasHandler"] is True, "no delegated handler exported"
    exports = [c for c in rendered["calls"] if c["kind"] == "export"]
    assert exports == [{"kind": "export", "id": 101}], rendered["calls"]


def test_report_button_opens_analytical_report(rendered):
    assert rendered["hasHandler"] is True, "no delegated handler exported"
    reports = [c for c in rendered["calls"] if c["kind"] == "report"]
    assert reports == [{"kind": "report", "id": 101}], rendered["calls"]
    assert rendered["dataActions"].count("report") >= 1, rendered["dataActions"]


def test_saveas_native_locate_actions_are_wired(rendered):
    assert rendered["hasHandler"] is True
    kinds = {c["kind"] for c in rendered["calls"]}
    assert {"saveas", "native", "locate"}.issubset(kinds), rendered["calls"]
    for action in ("saveas", "native", "locate"):
        assert rendered["dataActions"].count(action) >= 1, rendered["dataActions"]


def test_native_controls_keep_their_own_behaviour(rendered):
    """A checkbox toggles and a link navigates - the delegate steps aside."""
    assert rendered["nativeClicks"] == [False, False], rendered["nativeClicks"]
