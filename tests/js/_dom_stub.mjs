import { readFileSync } from 'node:fs';

/**
 * A small DOM, enough for the components whose runtime lives in the browser.
 *
 * Shared by the JavaScript harnesses so a stub defect is fixed once: two copies
 * of a fake document drift, and then one harness passes against a DOM the other
 * one cannot build. It models exactly what these components use - attributes,
 * classes, text (as text, never as markup), events, and the few selectors the
 * runtimes ask for - and nothing else on purpose.
 */

/**
 * Listeners registered on the document rather than on an element.
 *
 * A delegated runtime - the Inspector, the toolbar - listens on the document in
 * the capture phase, so the stub has to model two things it did not need before:
 * a document that can be listened to at all, and the phase a listener belongs
 * to. Without the phase, an interception that must run *before* the control it
 * is inspecting looks exactly like one that runs after it, and a harness would
 * pass a runtime that deletes a record while you inspect the Delete button.
 */
const documentListeners = {capture: [], bubble: []};

function fire(listeners, event) {
    listeners.slice().forEach(({handler}) => {
        if (event._stopped) return;
        handler(event);
    });
}

/** Registrations are read once per phase, in the order they were made. */

export class FakeClassList {
    constructor(node) { this.node = node; this.set = new Set(); }
    add(...names) { names.forEach((n) => this.set.add(n)); this.sync(); }
    remove(...names) { names.forEach((n) => this.set.delete(n)); this.sync(); }
    contains(name) { return this.set.has(name); }
    toggle(name, on) { if (on === undefined) on = !this.contains(name);
        if (on) this.add(name); else this.remove(name); return on; }
    sync() { this.node._class = [...this.set].join(' '); }
}

export class FakeElement {
    constructor(tag, attrs = {}) {
        this.tagName = tag.toUpperCase();
        //: Set by the document when the element is attached, so focus knows
        //: where to record itself.
        this.ownerDocument = null;
        //: Element nodes are the only ones these runtimes walk, and one of them
        //: asks, so the stub answers instead of leaving `undefined`.
        this.nodeType = 1;
        this.children = [];
        this.parentNode = null;
        this.attributes = {};
        this.listeners = {};
        this.style = {};
        //: Form controls have a value in a real DOM; the dialog's typed
        //: confirmation reads it, so the stub has one too.
        this._value = '';
        this._class = attrs.class || '';
        this._text = '';
        this.classList = new FakeClassList(this);
        Object.entries(attrs).forEach(([key, value]) => {
            if (key === 'class') this.classList.set = new Set(String(value).split(/\s+/).filter(Boolean));
            else this.attributes[key] = String(value);
        });
        this.classList.sync();
    }
    get value() { return this._value; }
    set value(next) { this._value = next === null || next === undefined ? '' : String(next); }
    get id() { return this.attributes.id !== undefined ? this.attributes.id : ''; }
    set id(next) {
        if (next === null || next === undefined || next === '') delete this.attributes.id;
        else this.attributes.id = String(next);
    }
    /** `data-*` attributes as a dataset; camelCase maps both ways. */
    get dataset() {
        const element = this;
        const toAttr = (prop) => 'data-' + String(prop).replace(/[A-Z]/g, (m) => '-' + m.toLowerCase());
        const fromAttr = (prop) => String(prop).replace(/^data-/, '').replace(/-(\w)/g, (_, c) => c.toUpperCase());
        return new Proxy({}, {
            get(_target, prop) {
                if (typeof prop !== 'string') return undefined;
                const value = element.getAttribute(toAttr(prop));
                return value === null ? undefined : value;
            },
            set(_target, prop, value) {
                if (typeof prop !== 'string') return false;
                if (value === null || value === undefined) element.removeAttribute(toAttr(prop));
                else element.setAttribute(toAttr(prop), String(value));
                return true;
            },
            deleteProperty(_target, prop) {
                if (typeof prop === 'string') element.removeAttribute(toAttr(prop));
                return true;
            },
            has(_target, prop) { return typeof prop === 'string' && element.hasAttribute(toAttr(prop)); },
            ownKeys() {
                return Object.keys(element.attributes)
                    .filter((name) => name.startsWith('data-'))
                    .map(fromAttr);
            },
            getOwnPropertyDescriptor() { return { enumerable: true, configurable: true }; },
        });
    }
    get className() { return this._class; }
    set className(value) {
        this.classList.set = new Set(String(value).split(/\s+/).filter(Boolean));
        this.classList.sync();
    }
    get textContent() {
        return this._text + this.children.map((child) => child.textContent).join('');
    }
    set textContent(value) {
        this.children = [];
        this._text = value === null || value === undefined ? '' : String(value);
    }
    get innerHTML() { throw new Error('innerHTML is not available here'); }
    set innerHTML(_value) { throw new Error('innerHTML is not available here'); }
    get firstChild() { return this.children[0] || null; }
    // Table sugar, as far as the table runtime reaches: `tBodies`, `tHead`
    // and the direct `rows` of a table/section - nothing deeper.
    get tBodies() { return this.children.filter((c) => c.tagName === 'TBODY'); }
    get tHead() { return this.children.find((c) => c.tagName === 'THEAD') || null; }
    get rows() {
        if (!['TABLE', 'TBODY', 'THEAD', 'TFOOT'].includes(this.tagName)) return undefined;
        return this.children.filter((c) => c.tagName === 'TR');
    }
    get cells() {
        if (this.tagName !== 'TR') return undefined;
        return this.children.filter((c) => c.tagName === 'TD' || c.tagName === 'TH');
    }
    /** `beforeend` is the only position the runtimes use: parse the fragment
     *  with the same tree builder the harnesses use, splice its children in. */
    insertAdjacentHTML(position, html) {
        if (position !== 'beforeend') {
            throw new Error(`insertAdjacentHTML(${position}) is not modelled here`);
        }
        const parsed = treeFromHtml(String(html));
        parsed.children.slice().forEach((child) => this.appendChild(child));
    }
    appendChild(child) {
        // A fragment is a wrapper, never a node: its children move in.
        if (isFragment(child)) {
            child.children.slice().forEach((grandchild) => this.appendChild(grandchild));
            child.children = [];
            return child;
        }
        child.parentNode = this;
        child.ownerDocument = this.ownerDocument || null;
        this.children.push(child);
        return child;
    }
    insertBefore(child, before) {
        if (isFragment(child)) {
            child.children.slice().forEach((grandchild) => this.insertBefore(grandchild, before));
            child.children = [];
            return child;
        }
        child.parentNode = this;
        child.ownerDocument = this.ownerDocument || null;
        const index = before ? this.children.indexOf(before) : -1;
        if (index < 0) this.children.push(child); else this.children.splice(index, 0, child);
        return child;
    }
    removeChild(child) {
        const index = this.children.indexOf(child);
        if (index >= 0) this.children.splice(index, 1);
        child.parentNode = null;
        return child;
    }
    remove() { if (this.parentNode) this.parentNode.removeChild(this); }
    contains(node) {
        if (node === this) return true;
        return this.children.some((child) => child.contains(node));
    }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    getAttribute(name) { return name in this.attributes ? this.attributes[name] : null; }
    matches(selector) { return matches(this, selector); }
    /** A copy of the element (attributes, classes, text); children only when deep. */
    cloneNode(deep = false) {
        const copy = new this.constructor(this.tagName.toLowerCase());
        Object.assign(copy.attributes, this.attributes);
        copy.classList.set = new Set(this.classList.set);
        copy.classList.sync();
        copy._text = this._text;
        if (deep) this.children.forEach((child) => copy.appendChild(child.cloneNode(true)));
        return copy;
    }
    focus() {
        const owner = this.ownerDocument;
        if (owner) owner.activeElement = this;
        this.focused = true;
    }
    blur() { this.focused = false; }
    replaceChildren() {
        this.children.forEach((child) => { child.parentNode = null; });
        this.children = [];
        this._text = '';
    }
    hasAttribute(name) { return name in this.attributes; }
    removeAttribute(name) { delete this.attributes[name]; }
    addEventListener(type, handler, options) {
        const capture = options === true || (options && options.capture === true);
        (this.listeners[type] ||= []).push({handler, capture});
    }
    removeEventListener(type, handler) {
        this.listeners[type] = (this.listeners[type] || [])
            .filter((entry) => entry.handler !== handler);
    }
    /** DOM activation: the element's click() dispatches a click event. */
    click() { return this.dispatch('click'); }

    dispatch(type, payload = {}) {
        // Events travel the whole path - capture down from the document, then
        // the target, then bubble back up - because a component delegating its
        // interception from an ancestor is the whole point of a shared surface:
        // a stub that stopped at the node it was fired on would pass a harness
        // against a runtime that never runs.
        const event = {
            type, target: this, currentTarget: this,
            preventDefault() {}, stopPropagation() { event._stopped = true; },
            _stopped: false, ...payload,
        };
        const path = [];
        for (let node = this; node; node = node.parentNode) path.push(node);
        // Capture runs outermost first: an interception that must happen before
        // the control it is inspecting has to see the event first.
        documentListeners.capture
            .filter((entry) => entry.type === type)
            .forEach((entry) => { if (!event._stopped) entry.handler(event); });
        path.slice().reverse().forEach((node) => {
            (node.listeners[type] || []).filter((entry) => entry.capture).forEach((entry) => {
                if (!event._stopped) entry.handler(event);
            });
        });
        (this.listeners[type] || []).forEach((entry) => {
            if (!event._stopped) entry.handler(event);
        });
        if (payload.bubbles !== false) {
            path.slice(1).forEach((node) => {
                (node.listeners[type] || []).filter((entry) => !entry.capture).forEach((entry) => {
                    if (!event._stopped) entry.handler(event);
                });
            });
            documentListeners.bubble
                .filter((entry) => entry.type === type)
                .forEach((entry) => { if (!event._stopped) entry.handler(event); });
        }
        return event;
    }
    querySelectorAll(selector) {
        const found = [];
        const walk = (node) => {
            node.children.forEach((child) => {
                if (matchesPath(child, selector)) found.push(child);
                walk(child);
            });
        };
        walk(this);
        return found;
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    /** DOM-style dispatch: an event object carries type/detail/bubbles. */
    dispatchEvent(event) {
        const detail = event && event.detail !== undefined ? event.detail : undefined;
        const payload = { target: this, detail };
        if (event && event.bubbles === false) payload.bubbles = false;
        this.dispatch(event.type, payload);
        return true;
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

export function matches(node, selector) {
    if (!node || !node.tagName) return false;
    // Selector lists matter here: `closest('[a],[b]')` is how a runtime finds
    // the nearest ancestor that described itself, and a stub that only knew
    // single selectors would say "nothing described this" to everything.
    return String(selector).split(',').map((part) => part.trim()).filter(Boolean)
        .some((part) => matchesOne(node, part));
}

/**
 * Matching for queries, which may be a descendant chain - `table#id tbody`,
 * `[data-unified-table] table`. The last part must match the node itself and
 * the earlier parts some ancestor, outermost-first in order. (No sibling
 * combinators: nothing these runtimes do needs them.)
 */
function matchesPath(node, selector) {
    const parts = String(selector).split(',').map((part) => part.trim())
        .filter(Boolean);
    return parts.some((part) => {
        const chain = part.split(/\s+/).filter(Boolean);
        if (!matchesOne(node, chain[chain.length - 1])) return false;
        let rest = chain.slice(0, -1);
        let ancestor = node.parentNode;
        while (ancestor && rest.length) {
            if (matchesOne(ancestor, rest[rest.length - 1])) rest = rest.slice(0, -1);
            ancestor = ancestor.parentNode;
        }
        return rest.length === 0;
    });
}

/** Is this node a document fragment the way appendChild has to treat one:
 *  a container whose children splice into the target, itself discarded? */
function isFragment(node) {
    return node && node.tagName === '#DOCUMENT-FRAGMENT';
}

function matchesOne(node, selector) {
    const negated = selector.match(/^(.*?):not\((.*)\)$/);
    if (negated) {
        return matchesOne(node, negated[1]) && !matchesOne(node, negated[2]);
    }
    // A compound of simple parts - `th.ut-sortable`, `.ut-check[checked]`,
    // `td[data-x="y"]` - is the AND of its parts: an optional tag followed
    // by classes, ids and attribute tests. A lone attribute selector is not
    // a compound: it falls through to the attribute branch below.
    const compound = selector.match(/^([a-zA-Z][\w-]*)?((?:[.#][\w-]+|:checked|\[[^\]]+\])+)$/);
    if (compound && (compound[1]
            || (compound[2].match(/[.#][\w-]+|:checked|\[[^\]]+\]/g) || []).length > 1)) {
        if (compound[1] && node.tagName !== compound[1].toUpperCase()) return false;
        const parts = compound[2].match(/[.#][\w-]+|:checked|\[[^\]]+\]/g) || [];
        return parts.every((part) => matchesOne(node, part));
    }
    // Compound with the checkedness pseudo-class, as in ".file-checkbox:checked":
    // the box matches when it matches the rest and is actually checked.
    const checked = selector.match(/^(.*):checked$/);
    if (checked) {
        return (!checked[1] || matchesOne(node, checked[1])) && node.checked === true;
    }
    const attribute = selector.match(/^\[([\w-]+)(?:="([^"]*)")?\]$/);
    if (attribute) {
        const value = node.getAttribute(attribute[1]);
        if (attribute[2] === undefined) return value !== null;
        return value === attribute[2];
    }
    if (selector.startsWith('.')) return node.classList.contains(selector.slice(1));
    if (selector.startsWith('#')) return node.getAttribute('id') === selector.slice(1);
    return node.tagName === selector.toUpperCase();
}

/** The entities a server-rendered attribute may carry. */
function decodeEntities(value) {
    return String(value)
        .replace(/&quot;/g, '"')
        .replace(/&#39;/g, "'")
        .replace(/&lt;/g, '<')
        .replace(/&gt;/g, '>')
        .replace(/&amp;/g, '&');
}

/** Parse the attributes of a start tag into a FakeElement. Both quote
 *  styles are honoured - templates legitimately use either - and entities
 *  inside attribute values are decoded, as a browser would. */
export function elementFromTag(tag) {
    const name = tag.match(/^<\s*([a-zA-Z0-9]+)/)[1];
    const attrs = {};
    const body = tag.replace(/^<\s*[a-zA-Z0-9]+/, '').replace(/\/?>$/, '');
    const pattern = /([\w:-]+)(?:="([^"]*)"|='([^']*)')?/g;
    let match;
    while ((match = pattern.exec(body))) {
        if (['/', 'aria', ''].includes(match[1])) continue;
        const raw = match[2] !== undefined ? match[2] : match[3];
        attrs[match[1]] = raw === undefined ? '' : decodeEntities(raw);
    }
    return new FakeElement(name, attrs);
}

/** Enough of an HTML tree for the dialog markup: elements, classes, text. */
export function treeFromHtml(html) {
    const root = new FakeElement('div', { id: 'document-root' });
    const stack = [root];
    const tokens = html.split(/(<[^>]+>)/).filter((token) => token !== '');
    for (const token of tokens) {
        if (token.startsWith('<!--')) continue;
        if (token.startsWith('</')) {
            if (stack.length > 1) stack.pop();
            continue;
        }
        if (token.startsWith('<')) {
            if (/^<\s*(br|input|img|hr)\b/i.test(token) && !token.endsWith('/>')) {
                stack[stack.length - 1].appendChild(elementFromTag(token.replace(/>$/, '/>')));
                continue;
            }
            const node = elementFromTag(token);
            stack[stack.length - 1].appendChild(node);
            if (!/\/>$/.test(token)) stack.push(node);
            continue;
        }
        // Text is appended, never assigned: the setter clears children, and
        // clearing them here is how a parsed tree silently becomes one node.
        stack[stack.length - 1]._text += token.replace(/\s+/g, ' ');
    }
    return root;
}

/**
 * Install the stub as the document the runtimes will use.
 *
 * Returns the root, the fake document, and the list of elements Bootstrap was
 * asked to show - a harness asserts what the reader met, not what was called.
 */
export function installDom() {
    const globalThisRef = globalThis;
    const documentRoot = new FakeElement('div', {});
    documentListeners.capture.length = 0;
    documentListeners.bubble.length = 0;
    const document = {
        createElement: (tag) => new FakeElement(tag),
        querySelector: (selector) => documentRoot.querySelector(selector),
        querySelectorAll: (selector) => documentRoot.querySelectorAll(selector),
        getElementById: (id) => documentRoot.querySelector(`[id="${id}"]`),
        // The table runtime adopts fetched rows through a fragment; the stub
        // has no per-document node identity to preserve, so the node itself.
        importNode: (node) => node,
        createDocumentFragment: () => new FakeElement('#document-fragment'),
        //: Text nodes are named by number rather than an imported class list,
        //: and nothing here walks them - `nodeType === 1` is what is asked.
        addEventListener(type, handler, options) {
            const capture = options === true || (options && options.capture === true);
            const store = capture ? documentListeners.capture : documentListeners.bubble;
            store.push({type, handler, capture});
        },
        removeEventListener(type, handler) {
            for (const store of [documentListeners.capture, documentListeners.bubble]) {
                for (let index = store.length - 1; index >= 0; index -= 1) {
                    if (store[index].type === type && store[index].handler === handler) {
                        store.splice(index, 1);
                    }
                }
            }
        },
        dispatch(type, payload = {}) {
            const event = {
                type, target: documentRoot, currentTarget: document,
                preventDefault() {}, stopPropagation() { event._stopped = true; },
                _stopped: false, ...payload,
            };
            fire(documentListeners.capture.filter((entry) => entry.type === type), event);
            if (payload.bubbles !== false) {
                fire(documentListeners.bubble.filter((entry) => entry.type === type), event);
            }
            return event;
        },
        get body() { return documentRoot.querySelector('body'); },
        activeElement: null,
        get documentElement() { return documentRoot; },
    };
    globalThisRef.addEventListener = (type, handler, options) => document.addEventListener(type, handler, options);
    const shown = [];
    //: Focus has to land somewhere the runtime can ask about: a browser puts
    //: the focused element on the document, and so does the stub.
    documentRoot.ownerDocument = document;
    globalThisRef.document = document;
    globalThisRef.window = globalThisRef;
    // The unified table adopts fetched rows with `table#id + CSS.escape`;
    // a stub CSS that passes identifiers through is enough for that.
    globalThisRef.CSS = {
        escape(value) { return String(value).replace(/([^a-zA-Z0-9_-])/g, '\\$1'); },
    };
    globalThisRef.bootstrap = {
        Modal: class {
            constructor(node) { this.node = node; }
            static getOrCreateInstance(node) { return new this(node); }
            show() { shown.push(this.node); }
            hide() { this.node.dispatch('hidden.bs.modal'); }
        },
        Toast: class {
            constructor(node) { this.node = node; }
            static getOrCreateInstance(node) { return new this(node); }
            show() { shown.push(this.node); }
        },
    };
    return {documentRoot, document, shown, globalThisRef};
}

/** Load a shipped runtime into the current global scope, as the shell does. */
export function loadRuntime(relative) {
    const source = readFileSync(relative, 'utf8');
    // eslint-disable-next-line no-eval
    (0, eval)(source);
}
