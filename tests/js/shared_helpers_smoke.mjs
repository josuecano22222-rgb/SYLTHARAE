/**
 * Contract harness for the canonical helpers in static/js/modules/core/utils.js
 * that page modules now import instead of defining their own copies.
 *
 * Run with no arguments: `node tests/js/shared_helpers_smoke.mjs`.
 * Exits non-zero on the first failed check.
 *
 * escapeHtml() is DOM-based in the browser (textContent -> innerHTML). This
 * harness models only that serialisation: a text node's innerHTML escapes
 * &, < and > and nothing else, which is the same rule the browser applies to
 * text content. Attribute contexts are covered by escapeAttribute(), which
 * this file also checks. Browser behaviour is verified separately.
 */

let failures = 0;
const checks = [];
function check(name, ok, detail = '') {
    checks.push(name);
    if (!ok) {
        failures += 1;
        console.error(`FAIL ${name}${detail ? ` :: ${detail}` : ''}`);
    }
}

// --- minimal DOM for text serialisation and csrf meta lookup ---------------
class TextNode {
    constructor() { this._text = ''; }
    set textContent(value) { this._text = value === null || value === undefined ? '' : String(value); }
    get textContent() { return this._text; }
    get innerHTML() {
        return this._text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }
}
let metaContent = null;
globalThis.document = {
    createElement() { return new TextNode(); },
    querySelector(selector) {
        if (selector !== 'meta[name="csrf-token"]' || metaContent === null) return null;
        return { getAttribute: (name) => (name === 'content' ? metaContent : null) };
    },
};

const utils = await import('../../static/js/modules/core/utils.js');
const { escapeHtml, escapeAttribute, getCSRFToken } = utils;

// --- escapeHtml ------------------------------------------------------------
check('escapeHtml null is empty', escapeHtml(null) === '');
check('escapeHtml undefined is empty (was the text "undefined" in 10 copies)',
    escapeHtml(undefined) === '', `got ${JSON.stringify(escapeHtml(undefined))}`);
check('escapeHtml keeps numeric zero', escapeHtml(0) === '0', `got ${JSON.stringify(escapeHtml(0))}`);
check('escapeHtml keeps empty string', escapeHtml('') === '');
check('escapeHtml escapes markup', escapeHtml('<b>&</b>') === '&lt;b&gt;&amp;&lt;/b&gt;',
    `got ${escapeHtml('<b>&</b>')}`);
check('escapeHtml leaves quotes (text-node contract)', escapeHtml('a"b') === 'a"b');
check('escapeHtml stringifies numbers', escapeHtml(42) === '42');
check('escapeHtml stringifies booleans', escapeHtml(false) === 'false');

// --- escapeAttribute (the attribute-context contract) -----------------------
check('escapeAttribute escapes quotes', escapeAttribute('x" onmouseover="y') === 'x&quot; onmouseover=&quot;y');
check('escapeAttribute escapes single quotes', escapeAttribute("it's") === 'it&#39;s');
check('escapeAttribute null is empty', escapeAttribute(null) === '');
check('escapeAttribute keeps zero', escapeAttribute(0) === '0');

// --- getCSRFToken ----------------------------------------------------------
metaContent = null;
check('getCSRFToken without meta is empty', getCSRFToken() === '');
metaContent = 'tok-123';
check('getCSRFToken reads the meta content', getCSRFToken() === 'tok-123');

// --- every name a migrated page imports must be exported --------------------
for (const name of ['escapeHtml', 'getCSRFToken']) {
    check(`utils exports ${name}`, typeof utils[name] === 'function');
}

if (failures) {
    console.error(`${failures} of ${checks.length} checks failed`);
    process.exit(1);
}
console.log(`shared helper checks passed (${checks.length})`);
