"""The analyst card inside the file preview pop-up.

Requested behaviour: "in the pop-up interface to display the content, add the
analyst classification feature". The pop-up (the archives page's File Details
modal) shows one file after another - opened from a list, and stepped through
with its own previous/next buttons - so the card cannot be bound to a file
once at page load the way the detail page's card is.

What this test drives, with node and a stub DOM, is the real shipped module
(static/js/modules/analyst-classify.js) doing exactly that:

* ``bindAnalystClassify(card, fileId)`` re-points an existing card at another
  file and refreshes its badges from the per-file assignments endpoint;
* stepping to a second file updates the same card (no second card, no stale
  file id, listeners bound once);
* every lookup happens *inside* the card element, so a page that has a
  page-level card and a modal card cannot cross-wire them;
* assigning sends one file id - the file on screen - to the audit-logged
  endpoint, not the placeholder the modal rendered.

The DOM stub is deliberately minimal: the module's contract is
"card.querySelector(...) for the controls, dataset.fileId for the file", and a
stub that satisfies exactly that is a fair check of it.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
MODULE = PROJECT_ROOT / "static" / "js" / "modules" / "analyst-classify.js"

#: A DOM stub that records what the module asks of it.
DOM_STUB = """
class FakeClassList {
    constructor() { this.names = new Set(); }
    add(...names) { names.forEach(n => this.names.add(n)); }
    remove(...names) { names.forEach(n => this.names.delete(n)); }
    toggle(name, force) {
        const on = force === undefined ? !this.names.has(name) : !!force;
        if (on) this.names.add(name); else this.names.delete(name);
        return on;
    }
    contains(name) { return this.names.has(name); }
}

class FakeElement {
    constructor(tag = 'div', attrs = {}) {
        this.tagName = tag.toUpperCase();
        this.dataset = {};
        this.classList = new FakeClassList();
        this.attributes = { ...attrs };
        this.children = [];
        this.listeners = {};
        this.textContent = '';
        this._innerHTML = '';
        this.id = attrs.id || '';
        this.value = attrs.value || '';
    }
    /* The module's escapeHtml() reads innerHTML after setting textContent -
       that is how the real DOM serialises an escaped string. */
    get innerHTML() {
        return this._innerHTML === '' ? String(this.textContent) : this._innerHTML;
    }
    set innerHTML(value) { this._innerHTML = String(value); }
    setAttribute(name, value) { this.attributes[name] = value; }
    getAttribute(name) { return this.attributes[name] === undefined ? null : this.attributes[name]; }
    appendChild(child) { this.children.push(child); return child; }
    removeChild(child) { this.children = this.children.filter(c => c !== child); }
    addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
    /* Only the exact selectors the module uses are resolvable; anything else
       is a programming error we want to see rather than silently ignore. */
    querySelector(selector) {
        if (selector === '[data-analyst-badges]') return this.dataset.analystBadges || null;
        if (selector === '[data-analyst-select]') return this.dataset.analystSelect || null;
        if (selector === '[data-analyst-new-name]') return this.dataset.analystNewName || null;
        if (selector === '[data-analyst-assign]') return this.dataset.analystAssign || null;
        if (selector === '[data-analyst-remove-all]') return this.dataset.analystRemoveAll || null;
        if (selector === '[data-analyst-empty]') return this.dataset.analystEmpty || null;
        if (selector === '.analyst-classify-controls select') return this.dataset.analystSelect || null;
        if (selector === '.analyst-classify-controls input') return this.dataset.analystNewName || null;
        if (selector === '.analyst-classify-badges') return this.dataset.analystBadges || null;
        return null;
    }
    querySelectorAll(selector) {
        if (selector === '.badge[data-category-id]') return this.badges || [];
        return [];
    }
    closest() { return null; }
    dispatch(type, event) { (this.listeners[type] || []).forEach(fn => fn(event)); }
}

globalThis.document = {
    readyState: 'complete',
    body: new FakeElement('body'),
    addEventListener() {},
    createElement: tag => new FakeElement(tag),
    querySelector: selector => (selector === 'meta[name="csrf-token"]'
        ? new FakeElement('meta', { content: 'token' }) : null),
    getElementById: id => (globalThis.__pageData && id === 'analyst-classify-page-data'
        ? globalThis.__pageData : null),
};
globalThis.window = globalThis;
globalThis.__FakeElement = FakeElement;
globalThis.__confirmAnswer = true;
globalThis.confirm = () => globalThis.__confirmAnswer;
"""

HARNESS = """
import '__STUB__';

const calls = [];
globalThis.fetch = async (url, options = {}) => {
    calls.push({ url: String(url), options });
    if (String(url).startsWith('/api/analyst/assignments')) {
        const fileId = String(url).match(/file_id=(\\d+)/)[1];
        return {
            ok: true,
            status: 200,
            json: async () => ({ assignments: fileId === '4242'
                ? [{ category_id: 7, category_name: 'smuggling' }]
                : [] })
        };
    }
    if (String(url) === '/api/analyst/categories') {
        return { ok: true, status: 200, json: async () => ([{ id: 7, name: 'smuggling', file_count: 3 }]) };
    }
    if (String(url) === '/api/analyst/assign') {
        return { ok: true, status: 200, json: async () => ({ success: true, assigned: 1, category_name: 'smuggling' }) };
    }
    return { ok: true, status: 200, json: async () => ({ success: true }) };
};

const PageData = globalThis.__FakeElement;
globalThis.__pageData = new PageData('script');
globalThis.__pageData.textContent = JSON.stringify({
    canCategorize: true,
    translations: { noCategoriesYet: 'No analyst categories yet.' }
});

function makeCard(id) {
    const card = new PageData('div', { id });
    card.dataset.fileId = '0';           // placeholder rendered by the template
    card.dataset.analystBadges = new PageData('div');
    card.dataset.analystSelect = new PageData('select');
    card.dataset.analystNewName = new PageData('input');
    card.dataset.analystAssign = new PageData('button');
    card.dataset.analystRemoveAll = new PageData('button');
    return card;
}

const modalCard = makeCard('modalAnalystClassifyCard');
const pageCard = makeCard('analystClassifyCard');

const mod = await import('__MODULE__');

// --- the pop-up opens file 4242 -----------------------------------------
await mod.bindAnalystClassify(modalCard, 4242);
const afterFirst = {
    modalFileId: modalCard.dataset.fileId,
    modalBadges: modalCard.dataset.analystBadges.innerHTML,
    assignmentsCalls: calls.filter(c => c.url.startsWith('/api/analyst/assignments')).map(c => c.url),
    bound: modalCard.dataset.analystBound,
    modalListeners: Object.keys(modalCard.dataset.analystAssign.listeners),
};

// --- the pop-up steps to the next file (file 77) -------------------------
await mod.bindAnalystClassify(modalCard, 77);
const afterSecond = {
    modalFileId: modalCard.dataset.fileId,
    modalBadges: modalCard.dataset.analystBadges.innerHTML,
    assignmentsCalls: calls.filter(c => c.url.startsWith('/api/analyst/assignments')).map(c => c.url),
    listenerCount: modalCard.dataset.analystAssign.listeners.click.length,
};

// --- the page-level card is a different file and must not be touched -----
await mod.bindAnalystClassify(pageCard, 9);
const afterPageCard = {
    pageFileId: pageCard.dataset.fileId,
    modalFileId: modalCard.dataset.fileId,
    pageCalls: calls.filter(c => c.url.includes('file_id=9')).length,
};

// --- assigning writes the file on screen, not the placeholder ------------
calls.length = 0;
modalCard.dataset.analystSelect.value = '7';
modalCard.dataset.analystAssign.dispatch('click', {});
await new Promise(resolve => setTimeout(resolve, 10));
const assignCall = calls.find(c => c.url === '/api/analyst/assign');
const afterAssign = assignCall
    ? { url: assignCall.url, body: JSON.parse(assignCall.options.body) }
    : null;

// --- a card with no file id does nothing ---------------------------------
const untouched = makeCard('untouched');
const bound = await mod.bindAnalystClassify(untouched, 0);

process.stdout.write(JSON.stringify({
    afterFirst, afterSecond, afterPageCard, afterAssign, untouchedBound: bound,
    exports: Object.keys(mod).sort()
}));
"""


@pytest.fixture(scope="module")
def node():
    path = shutil.which("node")
    if path is None:
        pytest.skip("node is not installed")
    return path


@pytest.fixture(scope="module")
def result(node, tmp_path_factory):
    assert MODULE.exists(), f"shipped module missing: {MODULE}"
    tmp = tmp_path_factory.mktemp("analystclassify")
    stub = tmp / "domstub.mjs"
    stub.write_text(DOM_STUB)
    harness = tmp / "harness.mjs"
    harness.write_text(
        HARNESS.replace("__STUB__", stub.as_uri()).replace("__MODULE__", MODULE.as_uri())
    )
    proc = subprocess.run([node, str(harness)], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"node failed: {proc.stderr[:2000]}"
    return json.loads(proc.stdout)


def test_the_module_exports_the_modal_entry_point(result):
    assert "bindAnalystClassify" in result["exports"]


def test_opening_a_file_points_the_card_at_it_and_shows_its_categories(result):
    first = result["afterFirst"]
    assert first["modalFileId"] == "4242"
    assert "smuggling" in first["modalBadges"]
    assert first["assignmentsCalls"] == ["/api/analyst/assignments?file_id=4242&per_page=100"]
    assert first["bound"] == "true"
    assert "click" in first["modalListeners"]


def test_stepping_to_the_next_file_reuses_one_card(result):
    """The pop-up's previous/next must not stack up cards or listeners."""
    second = result["afterSecond"]
    assert second["modalFileId"] == "77"
    assert second["assignmentsCalls"] == [
        "/api/analyst/assignments?file_id=4242&per_page=100",
        "/api/analyst/assignments?file_id=77&per_page=100",
    ]
    assert second["listenerCount"] == 1
    # File 77 has no analyst category: the card says so rather than keeping
    # the previous file's badge.
    assert "smuggling" not in second["modalBadges"]
    assert "analyst-classify-empty" in second["modalBadges"]


def test_two_cards_on_one_page_cannot_cross_wire(result):
    page = result["afterPageCard"]
    assert page["pageFileId"] == "9"
    assert page["modalFileId"] == "77", "the modal card still shows its own file"
    assert page["pageCalls"] == 1


def test_assigning_writes_the_file_on_screen(result):
    assert result["afterAssign"] is not None, "no assignment was sent"
    assert result["afterAssign"]["url"] == "/api/analyst/assign"
    assert result["afterAssign"]["body"]["path_ids"] == [77]
    assert result["afterAssign"]["body"]["category_id"] == 7


def test_a_card_without_a_file_is_not_bound(result):
    assert result["untouchedBound"] is False
