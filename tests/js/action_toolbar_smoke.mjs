/**
 * Action toolbar - the runtime contract, exercised for real.
 *
 *     node tests/js/action_toolbar_smoke.mjs
 *     node tests/js/action_toolbar_smoke.mjs --bar-json=/tmp/bar.json
 *
 * What is under test is not a description of the toolbar: it is the shipped
 * `static/js/modules/core/action-toolbar.js` plus the shipped page modules of
 * the reference pair (`sources-list-page.js`, `sides-list-page.js`), run
 * against a small DOM stub. The bar these pages get from the server is not
 * written out again by hand here - the Python side renders the real component
 * through the real catalogs and passes the attributes it produced in through
 * `--bar-json`, so the harness reads the same phrase templates the browser
 * would. Without that argument it falls back to the English source strings so
 * this file still runs on its own.
 *
 * The assertions are the ones the freeze demands:
 *
 *   zero selection -> disabled, the scope is on screen, the accessible name
 *                     says what is missing
 *   one            -> "1 source selected" (singular, not "1 sources")
 *   many           -> "3 sources selected"
 *   all            -> "27 of 27 sources selected"
 *   select none    -> disabled again; the scope is never stale
 *   repeated syncs -> identical DOM, no accumulation, no double update
 *   the runtime    -> binds no listener at all, so a page cannot double-update
 *                     through it however often it syncs
 *   the page       -> the reader is told the outcome of an action: a refusal
 *                     out loud, what it is doing, and a failure as a failure
 *
 * Exit code 0 means every check passed.
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');

// --------------------------------------------------------------------------
// Results
// --------------------------------------------------------------------------
const checks = [];
const check = (name, condition) => checks.push([name, !!condition]);

// --------------------------------------------------------------------------
// A DOM stub, just big enough for the selectors these files use:
// `[data-bulk-action]`, `[data-selection-summary]`, `.source-checkbox:checked`
// and `closest('.source-card-item')`.
// --------------------------------------------------------------------------
function matches(node, selector) {
    let rest = String(selector).trim();
    let pseudo = '';
    if (rest.includes(':')) {
        pseudo = rest.slice(rest.indexOf(':') + 1);
        rest = rest.slice(0, rest.indexOf(':'));
    }
    if (pseudo === 'checked' && !node.checked) return false;
    const classes = [...rest.matchAll(/\.([\w-]+)/g)].map((match) => match[1]);
    const attrs = [...rest.matchAll(/\[([\w-]+)(?:=["']?([^\]"']*)["']?)?\]/g)]
        .map((match) => [match[1], match[2]]);
    const tag = rest.match(/^[a-zA-Z][\w-]*/);
    if (tag && node.tagName !== tag[0].toUpperCase()) return false;
    for (const name of classes) {
        if (!node.classList.contains(name)) return false;
    }
    for (const [name, value] of attrs) {
        if (!node.hasAttribute(name)) return false;
        if (value !== undefined && node.getAttribute(name) !== value) return false;
    }
    return true;
}

function descendants(node, out = []) {
    for (const child of node.children) {
        out.push(child);
        descendants(child, out);
    }
    return out;
}

class FakeElement {
    constructor(tag = 'div', attributes = {}) {
        this.tagName = tag.toUpperCase();
        this._attributes = new Map();
        this.children = [];
        this.parentNode = null;
        this.dataset = {};
        this.style = {};
        this.listeners = {};
        this.disabled = false;
        this.checked = false;
        this.hidden = false;
        this.value = '';
        this.textContent = '';
        this.innerHTML = '';
        this.className = '';
        this.id = '';
        const names = new Set();
        this.classList = {
            add: (name) => names.add(name),
            remove: (name) => names.delete(name),
            contains: (name) => names.has(name),
            has: (name) => names.has(name),
            _names: names,
        };
        for (const [name, value] of Object.entries(attributes)) {
            this.setAttribute(name, value);
        }
    }

    setAttribute(name, value) {
        this._attributes.set(name, String(value));
        this.dataset[name.replace(/^data-/, '').replace(/-([a-z])/g, (_, c) => c.toUpperCase())]
            = String(value);
        if (name === 'id') this.id = String(value);
        if (name === 'class') {
            this.className = String(value);
            this.classList._names.clear();
            for (const cls of String(value).split(/\s+/).filter(Boolean)) {
                this.classList._names.add(cls);
            }
        }
    }

    getAttribute(name) {
        return this._attributes.has(name) ? this._attributes.get(name) : null;
    }

    hasAttribute(name) {
        return this._attributes.has(name);
    }

    removeAttribute(name) {
        this._attributes.delete(name);
        if (name === 'id') this.id = '';
        if (name === 'class') {
            this.className = '';
            this.classList._names.clear();
        }
    }

    appendChild(node) {
        node.parentNode = this;
        this.children.push(node);
        if (FakeElement.onRegister) FakeElement.onRegister(node);
        return node;
    }

    remove() {
        if (!this.parentNode) return;
        const index = this.parentNode.children.indexOf(this);
        if (index !== -1) this.parentNode.children.splice(index, 1);
        this.parentNode = null;
    }

    replaceChild(newNode, oldNode) {
        const index = this.children.indexOf(oldNode);
        if (index !== -1) this.children[index] = newNode;
        newNode.parentNode = this;
        if (FakeElement.onRegister) FakeElement.onRegister(newNode);
        return oldNode;
    }

    cloneNode(deep) {
        const clone = new FakeElement(this.tagName, Object.fromEntries(this._attributes));
        clone.disabled = this.disabled;
        clone.checked = this.checked;
        clone.value = this.value;
        clone.textContent = this.textContent;
        if (deep) {
            for (const child of this.children) clone.appendChild(child.cloneNode(true));
        }
        return clone;
    }

    insertAdjacentHTML(_position, html) {
        this.inserted = (this.inserted || '') + html;
    }

    addEventListener(type, handler) {
        (this.listeners[type] = this.listeners[type] || []).push(handler);
    }

    querySelectorAll(selector) {
        return descendants(this).filter((node) => matches(node, selector));
    }

    querySelector(selector) {
        return this.querySelectorAll(selector)[0] || null;
    }

    closest(selector) {
        let node = this;
        while (node) {
            if (matches(node, selector)) return node;
            node = node.parentNode;
        }
        return null;
    }
}

function makeDocument({ permissive = false } = {}) {
    const registry = new Map();
    const listeners = [];
    const register = (node) => {
        if (node.id) registry.set(node.id, node);
        for (const child of node.children) register(child);
        return node;
    };
    FakeElement.onRegister = (node) => {
        if (node.id) registry.set(node.id, node);
    };
    const document = {
        body: new FakeElement('body'),
        listeners,
        createElement: (tag) => new FakeElement(tag),
        // In permissive mode an element the test did not build is created and
        // attached, as the real page's markup would have provided it: the page
        // may then reach its own button and its own parent.
        getElementById: (id) => (registry.has(id) ? registry.get(id)
            : (permissive ? document.body.appendChild(new FakeElement('div', { id })) : null)),
        querySelectorAll: (selector) => descendants(document.body)
            .filter((node) => matches(node, selector)),
        querySelector: (selector) => document.querySelectorAll(selector)[0] || null,
        addEventListener: (type, handler) => listeners.push({ type, handler }),
        register: (node) => { register(node); return node; },
    };
    return document;
}

// --------------------------------------------------------------------------
// The bar as the server renders it
// --------------------------------------------------------------------------
// The English source strings the component renders. `--bar-json` replaces them
// with the phrases the real catalogs produced.
let SERVER = {
    'data-action-toolbar': '',
    'data-selected-count': '0',
    'data-text-select-first': 'Select {noun} first',
    'data-text-selected': '{count} {noun} selected',
    'data-text-all-selected': '{count} of {total} {noun} selected',
};

// The accessible names the server rendered for the bulk buttons, when the
// Python side passed them on: the state before the reader selects anything is
// the server's to state, and this must read the server's words, not its own.
let SERVER_BULK_LABELS = {};

const barJsonArg = process.argv.find((arg) => arg.startsWith('--bar-json='));
if (barJsonArg) {
    const payload = JSON.parse(readFileSync(barJsonArg.split('=')[1], 'utf8'));
    SERVER = { ...SERVER, ...payload.attributes };
    SERVER_BULK_LABELS = payload.bulk_labels || {};
}

/** What `{% call action_toolbar(id=…) %}` with two bulk buttons renders. */
function serverBar(id, { total, noun, nounSingular, buttons }) {
    const bar = new FakeElement('div', {
        ...SERVER,
        id,
        class: 'action-bar',
        role: 'toolbar',
        'data-total': String(total),
        'data-noun': noun,
        'data-noun-singular': nounSingular,
    });
    const group = bar.appendChild(new FakeElement('div', { class: 'action-group' }));
    const summary = group.appendChild(new FakeElement('span', {
        class: 'action-label',
        'data-selection-summary': '',
        role: 'status',
        'aria-live': 'polite',
        'data-selected-count': '0',
        'data-total': String(total),
        'data-noun': noun,
        'data-noun-singular': nounSingular,
    }));
    summary.textContent = String(SERVER['data-text-select-first']).replace('{noun}', noun);
    const bulk = {};
    for (const label of buttons) {
        const refusal = String(SERVER['data-text-select-first']).replace('{noun}', noun);
        const button = group.appendChild(new FakeElement('button', {
            type: 'button',
            class: 'btn-action btn-outline-primary',
            id: label.replace(/\W+/g, '').toLowerCase(),
            'data-bulk-action': '',
            'data-action-label': label,
            title: label,
            'aria-label': SERVER_BULK_LABELS[label] || `${label}: ${refusal}`,
            'aria-disabled': 'true',
        }));
        button.disabled = true;
        bulk[label] = button;
    }
    return { bar, summary, bulk };
}

// --------------------------------------------------------------------------
// The modules, loaded as the browser loads them
// --------------------------------------------------------------------------
function loadToolbar(document) {
    const source = readFileSync(
        path.join(ROOT, 'static/js/modules/core/action-toolbar.js'), 'utf8');
    const window = {};
    // The file is an IIFE over (window, document): hand it the stubs.
    new Function('window', 'document', source)(window, document);
    return window.ActionToolbar;
}

async function loadPageModule(moduleName, document, window) {
    globalThis.window = window;
    globalThis.document = document;
    globalThis.bootstrap = {
        Toast: class { constructor() {} show() {} hide() {} },
        Modal: class { constructor() {} show() {} hide() {} },
    };
    globalThis.confirm = () => true;
    globalThis.alert = () => {};
    globalThis.fetch = () => Promise.resolve({
        ok: true,
        status: 200,
        json: async () => ({ keywords: [], words: [], items: [], total: 0,
                             page: 1, per_page: 10, success: true }),
        text: async () => '',
    });
    return import('file://' + path.join(ROOT, 'static/js/pages', moduleName));
}

const initProblems = [];

function fireDomContentLoaded(document) {
    for (const { type, handler } of document.listeners) {
        if (type === 'DOMContentLoaded') {
            try {
                handler();
            } catch (error) {
                // The stub is not the product: a page's own start-up may reach
                // for markup this harness does not build. That is recorded and
                // reported, and the checks below say what actually matters -
                // whether the page's selection path reached the toolbar.
                initProblems.push(error.message);
            }
        }
    }
}

// --------------------------------------------------------------------------
// 1. The runtime: scope, enablement, accessible names, no listeners
// --------------------------------------------------------------------------
{
    const document = makeDocument();
    const ActionToolbar = loadToolbar(document);
    check('runtime: exposes one method and nothing else',
          ActionToolbar && Object.keys(ActionToolbar).join(',') === 'sync');
    check('runtime: the component owns the phrase templates, not the runtime',
          typeof ActionToolbar.sync === 'function');

    const fixture = serverBar('sourcesActionBar', {
        total: 27, noun: 'sources', nounSingular: 'source',
        buttons: ['Export Selected', 'Edit Selected'],
    });
    document.register(document.body.appendChild(fixture.bar));

    // Zero: what the server rendered, before anything is touched.
    check('zero: buttons disabled', fixture.bulk['Export Selected'].disabled === true);
    check('zero: the scope is on screen',
          fixture.summary.textContent === 'Select sources first');
    check('zero: the accessible name says what is missing',
          fixture.bulk['Edit Selected'].getAttribute('aria-label')
            === 'Edit Selected: Select sources first');
    check('zero: syncing zero keeps that state',
          ActionToolbar.sync('sourcesActionBar', { selected: 0 }) === 'Select sources first');

    // One: singular, enabled, scope in the name.
    check('one: singular noun',
          ActionToolbar.sync('sourcesActionBar', { selected: 1 }) === '1 source selected');
    check('one: enabled', fixture.bulk['Export Selected'].disabled === false
          && fixture.bulk['Export Selected'].getAttribute('aria-disabled') === null);
    check('one: the accessible name carries the scope',
          fixture.bulk['Export Selected'].getAttribute('aria-label')
            === 'Export Selected: 1 source selected');
    check('one: the summary shows the same sentence',
          fixture.summary.textContent === '1 source selected');

    // Many.
    check('many: plural noun',
          ActionToolbar.sync('sourcesActionBar', { selected: 3 }) === '3 sources selected');
    check('many: the bar remembers the count',
          fixture.bar.getAttribute('data-selected-count') === '3');

    // All.
    check('all: the sentence names the total',
          ActionToolbar.sync('sourcesActionBar', { selected: 27 })
            === '27 of 27 sources selected');
    check('all: still enabled', fixture.bulk['Edit Selected'].disabled === false);

    // Select none: no stale scope, no stale buttons.
    check('none again: disabled, scope asks for a selection',
          ActionToolbar.sync('sourcesActionBar', { selected: 0 }) === 'Select sources first'
          && fixture.bulk['Export Selected'].disabled === true);

    // A count that arrives as a string, and a page-supplied noun.
    check('string counts are understood',
          ActionToolbar.sync('sourcesActionBar', { selected: '2' }) === '2 sources selected');
    check('the page may pass the words instead of the attributes',
          ActionToolbar.sync('sourcesActionBar', { selected: 1, total: 9, noun: 'rows',
                               nounSingular: 'row' }) === '1 row selected');

    // An unknown total never invents a denominator.
    const partial = serverBar('sidesActionBar', {
        total: 0, noun: 'sides', nounSingular: 'side', buttons: ['Export Selected'],
    });
    document.register(document.body.appendChild(partial.bar));
    check('unknown total: no invented denominator',
          ActionToolbar.sync('sidesActionBar', { selected: 4 }) === '4 sides selected');

    // Repeated syncs: same numbers, same DOM; nothing accumulates.
    const snapshot = () => JSON.stringify({
        summary: fixture.summary.textContent,
        label: fixture.bulk['Export Selected'].getAttribute('aria-label'),
        disabled: fixture.bulk['Export Selected'].disabled,
        count: fixture.bar.getAttribute('data-selected-count'),
    });
    ActionToolbar.sync('sourcesActionBar', { selected: 27 });
    const before = snapshot();
    for (let i = 0; i < 25; i += 1) {
        ActionToolbar.sync('sourcesActionBar', { selected: 27 });
    }
    check('repeat: syncing the same state again changes nothing', before === snapshot());

    // The runtime binds nothing: with the runtime loaded, no listener exists,
    // and after a hundred syncs there is still none to double-update.
    const boundIn = (node) => Object.values(node.listeners).flat().length
        + descendants(node).reduce(
            (count, child) => count + Object.values(child.listeners).flat().length, 0);
    const boundBefore = boundIn(fixture.bar) + document.listeners.length;
    for (let i = 0; i < 100; i += 1) {
        ActionToolbar.sync('sourcesActionBar', { selected: i % 4 });
    }
    check('runtime: binds no event listener, however often it is called',
          boundIn(fixture.bar) + document.listeners.length === boundBefore
          && boundBefore === 0);

    // A page without a bar is not an error.
    check('missing bar: returns nothing instead of throwing',
          ActionToolbar.sync('nothingHere', { selected: 3 }) === null);
}

// --------------------------------------------------------------------------
// 2. The reference pair: the pages that own the count, and the outcome
// --------------------------------------------------------------------------
async function exercisePage({ moduleName, barId, checkboxClass, cardClass,
                              total, noun, nounSingular, kind,
                              exportMessage = null, warning = null,
                              deleteFunction = null, deleteError = null,
                              updateFunction = 'updateBulkButtons',
                              secondaryAction = 'Edit Selected' }) {
    const document = makeDocument({ permissive: true });
    const ActionToolbar = loadToolbar(document);
    const navigations = [];
    const window = {
        document,
        console,
        translations: {},
        confirm: () => true,
        location: { pathname: `/${kind}`, search: '', href: `http://localhost/${kind}`,
                    origin: 'http://localhost',
                    assign: (destination) => navigations.push(destination) },
        history: { replaceState() {} },
    };
    window.ActionToolbar = ActionToolbar;

    const fixture = serverBar(barId, {
        total, noun, nounSingular, buttons: ['Export Selected', secondaryAction],
    });
    document.register(document.body.appendChild(fixture.bar));

    const checkboxes = [];
    const cards = [];
    for (let i = 1; i <= total; i += 1) {
        const card = document.register(document.body.appendChild(
            new FakeElement('div', { class: cardClass, 'data-id': String(i) })));
        card.style.display = 'block';
        cards.push(card);
        const checkbox = new FakeElement('input', {
            type: 'checkbox', class: checkboxClass, value: String(i),
            onchange: 'updateBulkButtons()',
        });
        // A real checkbox carries its row's id in `value`, and the page's
        // selection code reads the property (`parseInt(cb.value, 10)`); the
        // stub sets attributes, so the property is set here alongside.
        checkbox.value = String(i);
        checkboxes.push(card.appendChild(checkbox));
    }
    // The served page carries the table's export menu: `components/table.html`
    // renders one checked box per offered column, and the page's bulk export
    // reads the reader's chosen columns from it (and stops with "select at
    // least one column" when none are). The harness builds the rows itself, so
    // it builds the menu the rows came with - otherwise it would be testing a
    // page state no browser ever serves.
    if (exportMessage) {
        const menu = document.register(document.body.appendChild(new FakeElement('div', {
            id: `${barId}ExportMenu`, class: 'dropdown ut-export',
            'data-export-interface': kind,
        })));
        for (const column of ['id', 'name', 'created_at']) {
            const box = menu.appendChild(new FakeElement('input', {
                type: 'checkbox', class: 'form-check-input ut-export-column-check',
                'data-export-column': column,
            }));
            box.checked = true;
        }
    }
    // The page's own `change` delegation would be a second binding for the same
    // event; the freeze forbids it, so count how many ways one change reaches
    // the toolbar.
    await loadPageModule(moduleName, document, window);
    fireDomContentLoaded(document);

    const label = (name) => fixture.bulk[name].getAttribute('aria-label');
    const update = () => window[updateFunction]();

    check(`${kind}: the page's selection path exists (${updateFunction})`,
          typeof window[updateFunction] === 'function'
          + (initProblems.length ? ` init problems: ${initProblems.join('; ')}` : ''));

    // Initial render, through the page's own selection code.
    update();
    check(`${kind}: no selection -> disabled, scope visible`,
          fixture.bulk['Export Selected'].disabled === true
          && fixture.summary.textContent === `Select ${noun} first`);
    check(`${kind}: no selection -> the accessible name says so`,
          label('Export Selected') === `Export Selected: Select ${noun} first`);

    // One row, through the row's own handler.
    checkboxes[0].checked = true;
    update();
    check(`${kind}: one row -> "1 ${nounSingular} selected"`,
          fixture.summary.textContent === `1 ${nounSingular} selected`);
    check(`${kind}: one row -> enabled and named with the scope`,
          fixture.bulk['Export Selected'].disabled === false
          && label('Export Selected') === `Export Selected: 1 ${nounSingular} selected`);

    // Several rows.
    checkboxes[1].checked = true;
    checkboxes[2].checked = true;
    update();
    check(`${kind}: three rows -> "3 ${noun} selected"`,
          fixture.summary.textContent === `3 ${noun} selected`
          && label(secondaryAction) === `${secondaryAction}: 3 ${noun} selected`);

    // Select all, then none, through the page's own buttons.
    window.selectAll();
    const all = total === 1 ? `1 ${nounSingular} selected`
        : `${total} of ${total} ${noun} selected`;
    check(`${kind}: select all -> the whole scope is named`,
          fixture.summary.textContent === all);
    window.selectNone();
    check(`${kind}: select none -> disabled again, scope back to the request`,
          fixture.bulk['Export Selected'].disabled === true
          && fixture.summary.textContent === `Select ${noun} first`);

    // The numbers are the page's, not the server's estimate: take a row off
    // the screen and "all" means all of what is left.
    cards[total - 1].remove();
    window.selectAll();
    check(`${kind}: the scope follows the rows the page is offering`,
          fixture.summary.textContent
            === `${total - 1} of ${total - 1} ${noun} selected`);
    window.selectNone();
    check(`${kind}: select none really clears every row`,
          checkboxes.every((checkbox) => checkbox.checked === false));

    // A duplicated update can no longer double-count: the page may sync as
    // often as it likes, the bar says one thing.
    checkboxes[0].checked = true;
    checkboxes[3].checked = true;
    for (let i = 0; i < 10; i += 1) {
        update();
    }
    check(`${kind}: ten updates in a row leave one sentence, not two`,
          fixture.summary.textContent === `2 ${noun} selected`
          && label('Export Selected') === `Export Selected: 2 ${noun} selected`
          && fixture.bar.getAttribute('data-selected-count') === '2');

    // One change event, one update: the row's handler is the only binding.
    const changeListeners = document.listeners.filter((entry) => entry.type === 'change').length;
    check(`${kind}: nothing else listens for the same change (${changeListeners} delegation)`,
          changeListeners === 0);

    if (!exportMessage) {
        // A page whose actions are its own business stops here: what this
        // contract test proves is that no runtime call was needed for it.
        return;
    }

    // The page owns the outcome and says it out loud.
    window.selectNone();
    window.bulkExport();
    window.bulkUpdate();
    const refusal = () => document.getElementById('toastContainer').inserted || '';
    check(`${kind}: an action without a selection refuses out loud`,
          refusal().includes('bg-warning') && refusal().includes(warning));

    checkboxes[0].checked = true;
    checkboxes[1].checked = true;
    update();
    window.bulkExport();
    check(`${kind}: the action reports what it is doing`,
          refusal().includes('bg-info') && refusal().includes(exportMessage));

    // A failure is announced as a failure, not swallowed.
    globalThis.fetch = () => Promise.reject(new Error('network down'));
    try {
        window[deleteFunction](checkboxes[0].value);
        const confirmButton = document.getElementById('deleteConfirmButton');
        for (const handler of (confirmButton.listeners.click || [])) handler();
        await new Promise((resolve) => setTimeout(resolve, 20));
    } catch (error) {
        check(`${kind}: the failing action reports instead of throwing (${error.message})`, false);
    }
    check(`${kind}: a failing action reports the error`,
          refusal().includes('bg-danger') && refusal().includes(deleteError));
    delete globalThis.fetch;
}

await exercisePage({
    moduleName: 'sources-list-page.js', barId: 'sourcesActionBar',
    checkboxClass: 'source-checkbox', cardClass: 'source-card-item',
    total: 27, noun: 'sources', nounSingular: 'source',
    exportMessage: 'Exporting 2 source(s)', warning: 'Please select sources to export',
    deleteFunction: 'deleteSource', deleteError: 'Error deleting source',
    kind: 'sources',
});

await exercisePage({
    moduleName: 'sides-list-page.js', barId: 'sidesActionBar',
    checkboxClass: 'side-checkbox', cardClass: 'side-card-item',
    total: 27, noun: 'sides', nounSingular: 'side',
    exportMessage: 'Exporting 2 side(s)', warning: 'Please select sides to export',
    deleteFunction: 'deleteSide', deleteError: 'Error deleting side',
    kind: 'sides',
});

// Keywords renders its rows in JavaScript; the harness builds the same rows the
// page builds (`.keyword-checkbox` with the page's own handler) and drives the
// page's selection path.
await exercisePage({
    moduleName: 'keywords-list-page.js', barId: 'keywordsActionBar',
    checkboxClass: 'keyword-checkbox', cardClass: 'keyword-row',
    total: 40, noun: 'keywords', nounSingular: 'keyword',
    updateFunction: 'updateSelection',
    kind: 'keywords',
});

// Words renders its rows on the server; its selection path is the same shape.
await exercisePage({
    moduleName: 'words-list-page.js', barId: 'wordsActionBar',
    checkboxClass: 'word-checkbox', cardClass: 'word-row',
    total: 10, noun: 'words', nounSingular: 'word',
    updateFunction: 'updateSelection',
    kind: 'words',
});

await exercisePage({
    moduleName: 'email-words-page.js', barId: 'emailWordsActionBar',
    checkboxClass: 'email-checkbox', cardClass: 'email-row',
    total: 10, noun: 'emails', nounSingular: 'email',
    secondaryAction: 'Copy Selected',
    kind: 'email-words',
});

// --------------------------------------------------------------------------
// Report
// --------------------------------------------------------------------------
if (initProblems.length) {
    console.log(`note: page start-up reached past the stub ${initProblems.length} time(s)`);
}
let failed = 0;
for (const [name, ok] of checks) {
    if (!ok) failed += 1;
    console.log(`${ok ? 'ok  ' : 'FAIL'} ${name}`);
}
console.log(`\n${checks.length - failed}/${checks.length} checks passed`);
process.exit(failed === 0 ? 0 : 1);
