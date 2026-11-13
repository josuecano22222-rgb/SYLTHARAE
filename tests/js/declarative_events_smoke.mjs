/**
 * The declarative event runtime (RES-CSP-01), exercised against the shipped file.
 *
 * Inline handler attributes were replaced by ``data-on-<event>`` attributes run
 * by static/js/modules/core/declarative-events.js, so the Content-Security-Policy
 * can drop ``script-src 'unsafe-inline'``. This harness proves the runtime keeps
 * the inline-handler contract and nothing more:
 *
 * * ``this`` is the element carrying the attribute, ``event`` is the event;
 * * handlers on ancestors run too, until one stops propagation;
 * * ``return false`` prevents the default action;
 * * non-bubbling events (error, blur, focus) run on the target only;
 * * dangerous names (eval, Function, constructor, innerHTML, string timers...)
 *   never run - by name and by identity;
 * * every ``data-on-*`` expression in the shipped templates and scripts parses.
 *
 * Usage:
 *     node tests/js/declarative_events_smoke.mjs
 */

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';

const failures = [];
let checks = 0;
function check(what, condition, detail = '') {
    checks += 1;
    if (!condition) failures.push(`${what}${detail ? ` -- ${detail}` : ''}`);
}

// --- a minimal document: listeners by phase, elements with parents ----------
const listeners = [];
const consoleErrors = [];
globalThis.window = globalThis;
globalThis.console = {...console, error: (...args) => consoleErrors.push(args.map(String).join(' '))};
globalThis.document = {
    addEventListener(type, handler, capture) { listeners.push({type, handler, capture: !!capture}); },
};

class El {
    constructor(attrs = {}, parent = null) {
        this.attributes = {...attrs};
        this.parentElement = parent;
        this.style = {};
        this.nodeType = 1;
        this.value = attrs.value;
        this.checked = attrs.checked;
    }
    getAttribute(name) { return name in this.attributes ? this.attributes[name] : null; }
    hasAttribute(name) { return name in this.attributes; }
}

function fire(type, target, extra = {}) {
    const event = {
        type, target, defaultPrevented: false, cancelBubble: false, ...extra,
        preventDefault() { this.defaultPrevented = true; },
        stopPropagation() { this.cancelBubble = true; },
    };
    listeners.filter((l) => l.type === type).forEach((l) => l.handler(event));
    return event;
}

// eslint-disable-next-line no-new-func
new Function(readFileSync('static/js/modules/core/declarative-events.js', 'utf8'))();
const runtime = globalThis.SyltharaeDeclarativeEvents;
check('runtime is exposed', runtime && typeof runtime.parse === 'function');

// --- installation ------------------------------------------------------------
for (const type of runtime.EVENTS) {
    const entry = listeners.find((l) => l.type === type);
    check(`listens for ${type}`, !!entry);
}
check('error is caught in the capture phase', listeners.find((l) => l.type === 'error').capture === true);
check('click is caught in the bubble phase', listeners.find((l) => l.type === 'click').capture === false);
const before = listeners.length;
runtime.install(globalThis.document);
check('install is idempotent', listeners.length === before);

// --- the inline-handler contract ----------------------------------------------
const calls = [];
globalThis.record = (...args) => { calls.push(args); return 'r'; };
globalThis.ns = {deep: {method(...args) { calls.push(['method', this === globalThis.ns.deep, ...args]); }}};

let el = new El({'data-on-click': "record('a', 1, -2, true, null, this, event)"});
let ev = fire('click', el);
check('call with literals, this and event',
    calls.length === 1 && calls[0][0] === 'a' && calls[0][1] === 1 && calls[0][2] === -2
    && calls[0][3] === true && calls[0][4] === null && calls[0][5] === el && calls[0][6] === ev,
    JSON.stringify(calls[0]?.slice(0, 5)));

calls.length = 0;
fire('click', new El({'data-on-click': 'ns.deep.method(1)'}));
check('method call keeps its receiver', calls[0] && calls[0][0] === 'method' && calls[0][1] === true);

calls.length = 0;
fire('change', new El({'data-on-change': 'record(this.value, this.checked); record(parseInt(this.value))', value: '42', checked: true}));
check('this.value / this.checked / parseInt', calls.length === 2 && calls[0][0] === '42' && calls[0][1] === true && calls[1][0] === 42);

calls.length = 0;
const fileInput = new El({'data-on-change': 'record(this.files[0])'});
fileInput.files = ['first.zip'];
fire('change', fileInput);
check('index access this.files[0]', calls[0] && calls[0][0] === 'first.zip');

calls.length = 0;
ev = fire('click', new El({'data-on-click': "record('x'); return false;"}));
check('return false prevents the default', ev.defaultPrevented === true && calls.length === 1);
ev = fire('click', new El({'data-on-click': 'record(1); return false; record(2)'}));
check('return stops the handler', calls.length === 2);

calls.length = 0;
const outer = new El({'data-on-click': "record('outer')"});
const middle = new El({}, outer);
const inner = new El({'data-on-click': "record('inner')"}, middle);
const text = {nodeType: 3, parentElement: inner};
fire('click', text);
check('handlers bubble to ancestors, target first',
    calls.map((c) => c[0]).join(',') === 'inner,outer', calls.map((c) => c[0]).join(','));

calls.length = 0;
const stopper = new El({'data-on-click': "event.stopPropagation(); record('stopper')"}, outer);
fire('click', stopper);
check('stopPropagation stops the ancestors', calls.map((c) => c[0]).join(',') === 'stopper');

calls.length = 0;
fire('keypress', new El({'data-on-keypress': "if(event.key==='Enter') record('enter')"}), {key: 'a'});
fire('keypress', new El({'data-on-keypress': "if(event.key==='Enter') record('enter')"}), {key: 'Enter'});
check('if guard on event.key', calls.length === 1 && calls[0][0] === 'enter');

calls.length = 0;
ev = fire('keydown', new El({'data-on-keydown': "if(event.key==='Enter'||event.key===' '){event.preventDefault();record('toggle');}"}), {key: ' '});
check('if with || and a block', calls.length === 1 && ev.defaultPrevented);

calls.length = 0;
fire('change', new El({'data-on-change': "record(this.value === 'light' ? 'L' : this.value === 'medium' ? 'M' : 'H')", value: 'medium'}));
check('nested ternary', calls[0] && calls[0][0] === 'M');

calls.length = 0;
fire('click', new El({'data-on-click': 'missingFunction?.(1); record(ns?.nothing?.deeper)'}));
check('optional call on a missing function is a no-op', calls.length === 1 && calls[0][0] === undefined);

calls.length = 0;
fire('click', new El({'data-on-click': "record({\"id\": 3, name: 'n', list: [1, 'two']}, [])"}));
check('JSON-shaped object and array literals',
    calls[0] && calls[0][0].id === 3 && calls[0][0].name === 'n' && calls[0][0].list[1] === 'two' && Array.isArray(calls[0][1]));

const hover = new El({'data-on-mouseover': "this.style.borderColor='red'; this.style.boxShadow='none'"});
fire('mouseover', hover);
check('style assignment', hover.style.borderColor === 'red' && hover.style.boxShadow === 'none');

const img = new El({'data-on-error': "this.style.display='none'; this.nextElementSibling.style.display='';"});
img.nextElementSibling = new El();
img.nextElementSibling.style.display = 'none';
fire('error', img);
check('image fallback via data-on-error', img.style.display === 'none' && img.nextElementSibling.style.display === '');

calls.length = 0;
const blurParent = new El({'data-on-blur': "record('parent')"});
fire('blur', new El({}, blurParent));
check('non-bubbling events do not reach ancestors', calls.length === 0);

calls.length = 0;
fire('click', new El({'data-on-click': "record('a\\'b', \"c\\\"d\", '\\u0041')"}));
check('string escapes', calls[0] && calls[0][0] === "a'b" && calls[0][1] === 'c"d' && calls[0][2] === 'A');

// --- what must never run --------------------------------------------------------
let ran = false;
globalThis.evil = () => { ran = true; };
globalThis.setTimeout = () => { ran = true; };
globalThis.eval = () => { ran = true; };
const refused = [
    "eval('evil()')",
    "setTimeout('evil()')",
    "window['setTime' + 'out']('evil()')",
    "record.constructor('evil()')()",
    "record['constr' + 'uctor']('evil()')()",
    'record.__proto__',
    "this.innerHTML = '<img>'",
    "this.href = 'javascript:evil()'",
    "document.cookie = 'x'",
    "Function('evil()')()",
    'new evil()',
    'evil(); var x = 1',
    '(() => evil())()',
    'evil`x`',
    "globalThis.evil()",
    "Reflect.apply(evil, null, [])",
];
consoleErrors.length = 0;
for (const expression of refused) {
    ran = false;
    const target = new El({'data-on-click': expression});
    fire('click', target);
    check(`refused: ${expression}`, ran === false && target.innerHTML === undefined && target.href === undefined);
}
check('each refusal is reported on the console', consoleErrors.length === refused.length,
    `${consoleErrors.length} of ${refused.length}`);

// An alias of a refused function is refused by identity, not only by name.
globalThis.aliasOfTimer = globalThis.setTimeout;
ran = false;
fire('click', new El({'data-on-click': "aliasOfTimer('evil()')"}));
check('a refused function is refused by identity', ran === false);

// A failing handler does not stop the next element's handler.
calls.length = 0;
const survivor = new El({'data-on-click': "record('survivor')"});
fire('click', new El({'data-on-click': 'notDefinedAnywhere()'}, survivor));
check('a throwing handler does not break its ancestors', calls.length === 1 && calls[0][0] === 'survivor');

// --- every shipped expression parses ---------------------------------------------
function walk(dir, out = []) {
    for (const name of readdirSync(dir)) {
        const path = join(dir, name);
        if (statSync(path).isDirectory()) {
            if (name !== 'dist' && name !== 'vendor') walk(path, out);
        } else if (/\.(html|js)$/.test(name) && !name.includes('.min.')) {
            out.push(path);
        }
    }
    return out;
}

const attribute = /\bdata-on-([a-z]+)\s*=\s*(\\?["'])/g;
let shipped = 0;
for (const file of [...walk('templates'), ...walk('static/js')]) {
    const source = readFileSync(file, 'utf8');
    let match;
    attribute.lastIndex = 0;
    while ((match = attribute.exec(source)) !== null) {
        const quote = match[2];
        const start = match.index + match[0].length;
        const end = source.indexOf(quote, start);
        if (end < 0) continue;
        let value = source.slice(start, end);
        // Selectors such as [data-on-click*="save"] are not handlers.
        if (source[match.index - 1] === '[' || /^\*|^\]/.test(source.slice(match.index + 8 + match[1].length).trimStart())) continue;
        value = value.replace(/\{\{[\s\S]*?\}\}/g, '0').replace(/\{%[\s\S]*?%\}/g, '')
            .replace(/\$\{[^}]*\}/g, '0')
            .replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, '&');
        if (quote.startsWith('\\')) value = value.replace(/\\(["'])/g, '$1');
        if (!runtime.EVENTS.includes(match[1])) {
            failures.push(`${file}: data-on-${match[1]} is not an event the runtime listens for`);
            continue;
        }
        shipped += 1;
        try {
            runtime.parse(value);
        } catch (error) {
            failures.push(`${file}: data-on-${match[1]}="${value}" does not parse: ${error.message}`);
        }
    }
}
check('the shipped markup has declarative handlers to check', shipped > 500, `${shipped}`);

if (failures.length) {
    console.log(`FAIL ${failures.length} of ${checks} checks (${shipped} shipped expressions)`);
    failures.forEach((failure) => console.log(`  - ${failure}`));
    process.exit(1);
}
console.log(`OK ${checks} checks, ${shipped} shipped expressions parse`);
