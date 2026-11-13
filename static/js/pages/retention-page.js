/**
 * Retention page (step 21) — the pruning policies and what they cost now.
 *
 * The list is the unified table component (record_table + unified-table.js)
 * like every other page; the page's own business is fetching the overview,
 * editing one policy's days and running policies now.
 *
 * CSP-safe: no eval, no inline script, no Function constructor.
 */

import { getCSRFTokenAsync } from '../modules/core/utils.js';

const DATA = JSON.parse(document.getElementById('retention-page-data').textContent);
const L = JSON.parse(document.getElementById('retention-page-labels').textContent);
const BODY = document.getElementById('retentionTableBody');
const COLUMN_COUNT = 7;

let rows = [];
let editingArea = null;

class ApiError extends Error {
    constructor(status, body) {
        super((body && body.error && body.error.message) || `HTTP ${status}`);
        this.code = body && body.error && body.error.code;
    }
}

async function api(method, url, payload) {
    const headers = { Accept: 'application/json' };
    const init = { method, credentials: 'same-origin', headers };
    if (method !== 'GET') {
        headers['Content-Type'] = 'application/json';
        headers['X-CSRFToken'] = await getCSRFTokenAsync();
        init.body = JSON.stringify(payload ?? {});
    }
    const resp = await fetch(url, init);
    const body = await resp.json().catch(() => null);
    if (!resp.ok || (body && body.success === false)) throw new ApiError(resp.status, body);
    return body;
}

function showError(error) {
    const box = document.getElementById('retentionError');
    const message = box.querySelector('.state-message') || box;
    message.textContent = (error && error.message) || L.load_failed;
    box.classList.remove('d-none');
}

function clearError() {
    document.getElementById('retentionError').classList.add('d-none');
}

function showStatus(message) {
    const box = document.getElementById('retentionStatus');
    box.textContent = message;
    box.classList.remove('d-none');
}

function escapeCell(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, (ch) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[ch]));
}

function td(value, html) {
    return `<td data-ut-value="${escapeCell(value == null ? '' : value)}">${html}</td>`;
}

function fmtOldest(iso) {
    if (!iso) return escapeCell(L.never);
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? escapeCell(iso) : d.toLocaleString();
}

function daysCell(row) {
    if (row.days === 0) {
        return `<span class="badge text-bg-secondary">${escapeCell(L.keep_forever)}</span>`;
    }
    return escapeCell(String(row.days));
}

function actionsCell(row) {
    const edit = `<button class="btn btn-sm btn-outline-secondary"
        data-on-click="openDays('${escapeCell(row.area)}')"><i class="bi bi-pencil"></i></button>`;
    const run = row.days > 0
        ? `<button class="btn btn-sm btn-outline-danger"
             data-on-click="runArea('${escapeCell(row.area)}')"><i class="bi bi-hourglass"></i></button>`
        : '';
    return edit + run;
}

function rowHtml(row) {
    return td(row.area, `<strong>${escapeCell(row.area)}</strong>`)
        + td(row.description, escapeCell(row.description))
        + td(row.days, daysCell(row))
        + td(row.eligible, escapeCell(String(row.eligible)))
        + td(row.total, escapeCell(String(row.total)))
        + td(row.oldest || '', fmtOldest(row.oldest))
        + td('', actionsCell(row));
}

function render() {
    const list = window.UnifiedTable;
    if (!list || !BODY) return;
    if (!rows.length) {
        list.renderRows(BODY, [list.states.empty(COLUMN_COUNT, {
            message: 'No retention policies.' })]);
        return;
    }
    list.renderRows(BODY, rows.map((row) => '<tr>' + rowHtml(row) + '</tr>'));
}

async function refresh(clearStatus) {
    clearError();
    if (clearStatus) document.getElementById('retentionStatus').classList.add('d-none');
    const list = window.UnifiedTable;
    if (list) {
        list.renderRows(BODY, [list.states.loading(COLUMN_COUNT, '...')]);
    }
    try {
        const body = await api('GET', '/api/retention');
        rows = body.items || [];
        render();
    } catch (error) {
        rows = [];
        render();
        showError(error);
    }
}

function openDays(area) {
    const row = rows.find((r) => r.area === area);
    if (!row) return;
    editingArea = area;
    document.getElementById('retentionDialogTitle').textContent =
        `${L.dialog_title} ${row.area}`;
    document.getElementById('retentionDialogDeletes').textContent = row.deletes;
    document.getElementById('retentionDays').value = row.days;
    document.getElementById('retentionDialogError').classList.add('d-none');
    bootstrap.Modal.getOrCreateInstance(
        document.getElementById('retentionDialog')).show();
}

async function saveDays() {
    if (editingArea == null) return;
    const days = Number(document.getElementById('retentionDays').value);
    const box = document.getElementById('retentionDialogError');
    try {
        await api('PUT', `/api/retention/${editingArea}`, { days });
        bootstrap.Modal.getOrCreateInstance(
            document.getElementById('retentionDialog')).hide();
        await refresh();
    } catch (error) {
        box.textContent = `${L.dialog_error} ${error.message}`;
        box.classList.remove('d-none');
    }
}

async function runArea(area) {
    if (!window.confirm(L.confirm_run_area)) return;
    await guarded(async () => {
        await api('POST', '/api/retention/run', { area });
        showStatus(`${area}: ${L.fired}`);
        await refresh();
    });
}

async function runAll() {
    if (!window.confirm(L.confirm_run_all)) return;
    await guarded(async () => {
        await api('POST', '/api/retention/run', {});
        showStatus(L.fired);
        await refresh();
    });
}

function guarded(fn) {
    return Promise.resolve().then(fn).catch(showError);
}

function initRetentionPage() {
    if (!document.getElementById('retentionTableBody')) return Promise.resolve();
    return Promise.resolve(refresh());
}

export default function init() {
    if (document.readyState === 'loading') {
        return new Promise((resolve) => {
            document.addEventListener('DOMContentLoaded', () => {
                Promise.resolve(initRetentionPage()).then(resolve);
            }, { once: true });
        });
    }
    return initRetentionPage();
}

const page = { refresh, openDays, saveDays, runArea, runAll, init };
Object.assign(window, {
    refresh: page.refresh, openDays: page.openDays, saveDays: page.saveDays,
    runArea: page.runArea, runAll: page.runAll,
});

init();
