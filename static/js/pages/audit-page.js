/**
 * Audit Log page (administrators): read the audit log through the server's
 * contract only (Api/routes/audit.py: GET /api/audit, /api/audit/actions,
 * /api/audit/<id>).
 *
 * The page never counts, filters or sorts entries itself: it sends the
 * filters, renders the page the server returned (newest first) and pages
 * with the server's keyset cursor (next_before_id). "More entries exist" is
 * the server's has_more, and the total is shown as not counted because the
 * server does not count it. Every value is inserted with textContent; names
 * and resources get dir="auto"; the detail JSON is shown as text, never as
 * markup. NULL is shown as "none".
 */

const DATA = JSON.parse(document.getElementById('audit-page-data').textContent);
const L = JSON.parse(document.getElementById('audit-page-labels').textContent);
const PAGE = Math.min(DATA.page_size, DATA.max_page_size);

const state = {
    filters: {},     // the filters that produced the shown page
    cursors: [],     // before_id of each earlier page (for Newer); [] = first page
    before: null,    // before_id of the shown page
    next: null,      // server's next_before_id, or null
};

function fmt(template, values) {
    return template.replace(/%\((\w+)\)s/g, (_, k) => (values[k] ?? ''));
}

function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
        if (v === null || v === undefined || v === false) continue;
        if (k === 'class') node.className = v;
        else if (k === 'text') node.textContent = v;
        else node.setAttribute(k, v === true ? '' : v);
    }
    for (const child of children) {
        if (child === null || child === undefined) continue;
        node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
}

function userText(value) {
    return el('bdi', { dir: 'auto', text: value ?? '' });
}

function orNone(value) {
    return value === null || value === undefined ? el('span', { class: 'text-muted', text: L.none })
        : userText(String(value));
}

function when(iso) {
    if (!iso) return L.none;
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

class ApiError extends Error {
    constructor(status, body) {
        const err = body && body.error;
        const message = (err && (err.message || err.code)) || (typeof err === 'string' ? err : '')
            || `${L.request_failed} (HTTP ${status})`;
        super(message);
        this.status = status;
        this.code = err && err.code;
    }
}

async function api(url) {
    const resp = await fetch(url, { method: 'GET', credentials: 'same-origin',
                                    headers: { Accept: 'application/json' } });
    const body = await resp.json().catch(() => null);
    if (!resp.ok || (body && body.success === false)) throw new ApiError(resp.status, body);
    return body;
}

// The error box is optional on some renders (and absent after a soft
// navigation swap). Without it there is nowhere to show the message, so log
// it instead of throwing a TypeError out of the error handler.
function showError(error) {
    const box = document.getElementById('auditError');
    if (!box) {
        console.error('Audit page error:', error);
        return;
    }
    const message = box.querySelector('.state-message, p') || box;
    message.textContent = error.code ? `${error.message} (${error.code})` : error.message;
    box.classList.remove('d-none');
}

function hideError() {
    const box = document.getElementById('auditError');
    if (box) box.classList.add('d-none');
}

async function guarded(fn) {
    hideError();
    try {
        return await fn();
    } catch (error) {
        showError(error);
        return undefined;
    }
}

// ---------------------------------------------------------------------------
// Filters
// ---------------------------------------------------------------------------

function localToIso(value) {
    if (!value) return '';
    const d = new Date(value);          // datetime-local is the reader's local time
    return Number.isNaN(d.getTime()) ? value : d.toISOString();
}

function readFilters() {
    const f = {};
    const action = document.getElementById('auditAction').value;
    const username = document.getElementById('auditUsername').value.trim();
    const resource = document.getElementById('auditResource').value.trim();
    const since = localToIso(document.getElementById('auditSince').value);
    const until = localToIso(document.getElementById('auditUntil').value);
    if (action) f.action = action;
    if (username) f.username = username;
    if (resource) f.resource = resource;
    if (since) f.since = since;
    if (until) f.until = until;
    return f;
}

function query(filters, before) {
    const params = new URLSearchParams({ ...filters, limit: String(PAGE) });
    if (before) params.set('before_id', String(before));
    return `/api/audit?${params.toString()}`;
}

async function loadActions() {
    const body = await api('/api/audit/actions');
    const select = document.getElementById('auditAction');
    const chosen = select.value;
    select.replaceChildren(el('option', { value: '', text: L.any_action }));
    for (const name of body.items) select.append(el('option', { value: name, text: name }));
    select.value = body.items.includes(chosen) ? chosen : '';
    if (body.capped) {
        document.getElementById('auditNote').textContent = fmt(L.actions_capped, { cap: body.cap });
    }
}

// ---------------------------------------------------------------------------
// List
// ---------------------------------------------------------------------------

function filterButton(text, title, apply) {
    const button = el('button', { type: 'button', class: 'btn btn-link btn-sm p-0 text-start',
                                  title }, userText(text));
    button.addEventListener('click', () => guarded(async () => { apply(); await search(); }));
    return button;
}

function renderRows(items) {
    const body = document.getElementById('auditRows');
    body.replaceChildren();
    const filtered = Object.keys(state.filters).length > 0 || state.before;
    if (!items.length) {
        body.append(el('tr', {}, el('td', { colspan: '6', class: 'text-center text-muted py-3',
                                            text: filtered ? L.empty : L.empty_log })));
        return;
    }
    for (const entry of items) {
        const user = entry.username === null
            ? el('span', { class: 'text-muted', text: L.no_user })
            : filterButton(entry.username, L.filter_user, () => {
                document.getElementById('auditUsername').value = entry.username;
            });
        const resource = entry.resource === null ? orNone(null)
            : filterButton(entry.resource, L.filter_resource, () => {
                document.getElementById('auditResource').value = entry.resource;
            });
        const details = el('button', { type: 'button', class: 'btn btn-outline-secondary btn-sm',
                                       'data-entry': String(entry.id), text: L.details });
        details.addEventListener('click', () => guarded(() => showEntry(entry.id)));
        body.append(el('tr', {},
            el('td', { class: 'text-nowrap', text: when(entry.created_at) }),
            el('td', {}, user),
            el('td', {}, el('code', { text: entry.action })),
            el('td', {}, resource),
            el('td', {}, orNone(entry.ip_address)),
            el('td', { class: 'text-end' }, details)));
    }
}

function renderPager() {
    const nav = document.getElementById('auditPager');
    nav.replaceChildren();
    const newer = el('button', { type: 'button', class: 'btn btn-outline-secondary btn-sm',
                                 id: 'auditNewer', text: L.newer });
    newer.disabled = state.cursors.length === 0;
    newer.addEventListener('click', () => guarded(() => {
        const before = state.cursors.pop();
        return load(before ?? null);
    }));
    const older = el('button', { type: 'button', class: 'btn btn-outline-secondary btn-sm',
                                 id: 'auditOlder', text: L.older });
    older.disabled = state.next === null;
    older.addEventListener('click', () => guarded(() => {
        state.cursors.push(state.before);
        return load(state.next);
    }));
    nav.append(newer, older);
}

async function load(before) {
    const body = await api(query(state.filters, before));
    state.before = before;
    state.next = body.has_more ? body.next_before_id : null;
    renderRows(body.items);
    renderPager();
    const note = [fmt(L.shown, { count: body.items.length }),
                  body.has_more ? L.more : L.no_more];
    if (body.total === null) note.push(L.not_counted);
    document.getElementById('auditNote').textContent = note.join(' ');
}

async function search() {
    state.filters = readFilters();
    state.cursors = [];
    document.getElementById('auditDetail').classList.add('d-none');
    await load(null);
}

// ---------------------------------------------------------------------------
// Detail
// ---------------------------------------------------------------------------

async function showEntry(id) {
    const { entry } = await api(`/api/audit/${encodeURIComponent(id)}`);
    document.getElementById('auditDetailTitle').textContent = fmt(L.entry_title, { id: entry.id });
    const dl = document.getElementById('auditDetailFields');
    dl.replaceChildren();
    const fields = [
        [L.f_id, String(entry.id)], [L.f_when, when(entry.created_at)],
        [L.f_user, entry.username], [L.f_user_id, entry.user_id],
        [L.f_action, entry.action], [L.f_resource, entry.resource], [L.f_ip, entry.ip_address],
    ];
    for (const [label, value] of fields) {
        dl.append(el('dt', { class: 'col-sm-3', text: label }), el('dd', { class: 'col-sm-9' }, orNone(value)));
    }
    document.getElementById('auditDetailJson').textContent =
        entry.detail === null ? L.none : JSON.stringify(entry.detail, null, 2);
    document.getElementById('auditDetail').classList.remove('d-none');
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------

document.getElementById('auditForm').addEventListener('submit', (event) => {
    event.preventDefault();
    guarded(search);
});
document.getElementById('auditReset').addEventListener('click', () => {
    for (const id of ['auditAction', 'auditUsername', 'auditResource', 'auditSince', 'auditUntil']) {
        document.getElementById(id).value = '';
    }
    guarded(search);
});

guarded(async () => {
    await loadActions();
    await search();
});
