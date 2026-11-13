/**
 * Schedules page (step 20) — scheduled evaluations and retention.
 *
 * The list is the unified table component (`record_table` +
 * static/js/modules/ui/unified-table.js) and nothing else: the same frame,
 * the same header sort, the same states as every other list in the
 * application. The page's own business is only what a schedule *is*:
 * fetching, the create/edit dialog, the actions (run now, pause, resume,
 * delete) and the per-schedule job history.
 *
 * CSP-safe: no eval, no inline script, no Function constructor.
 */

import { escapeHtml, getCSRFTokenAsync } from '../modules/core/utils.js';

const DATA = JSON.parse(document.getElementById('schedules-page-data').textContent);
const L = JSON.parse(document.getElementById('schedules-page-labels').textContent);
const TABLE_ID = 'schedulesTable';
const JOBS_TABLE_ID = 'schedulesJobsTable';
const BODY = document.getElementById('schedulesTableBody');
const COLUMN_COUNT = 8;

let rows = [];
let allUsers = false;
let editingId = null;   // null => the dialog creates
let jobsForId = null;

class ApiError extends Error {
    constructor(status, body) {
        super((body && body.error && body.error.message)
            || `HTTP ${status}`);
        this.status = status;
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
    const box = document.getElementById('schedulesError');
    const message = box.querySelector('.state-message') || box;
    message.textContent = (error && error.message) || L.load_failed;
    box.classList.remove('d-none');
}

function clearError() {
    document.getElementById('schedulesError').classList.add('d-none');
}

function guarded(fn) {
    return Promise.resolve().then(fn).catch((error) => {
        if (error instanceof ApiError && error.code === 'FORBIDDEN'
            && /no longer run/.test(error.message)) {
            showError(new Error(L.you_cannot_resume));
            return refresh();
        }
        showError(error);
    });
}

// ---------------------------------------------------------------------------
// Rendering (through the unified table)
// ---------------------------------------------------------------------------

function statusCell(row) {
    if (row.enabled) {
        return `<span class="badge text-bg-success">${escapeCell(L.status_enabled)}</span>`;
    }
    const paused = String(row.disabled_reason || '').includes('paused');
    const badge = paused
        ? `<span class="badge text-bg-secondary">${escapeCell(L.status_paused)}</span>`
        : `<span class="badge text-bg-danger">${escapeCell(L.status_disabled)}</span>`;
    const reason = row.disabled_reason
        ? `<div class="small text-muted">${escapeCell(row.disabled_reason)}</div>`
        : '';
    return badge + reason;
}

function escapeCell(value) {
    // One canonical implementation (core/utils.js). The local copy that used
    // to live here had lost its entity table - every mapping returned the
    // character it was meant to replace, so cells were never escaped and the
    // file did not even parse.
    return escapeHtml(String(value == null ? '' : value));
}

function fmtWhen(iso) {
    if (!iso) return L.never;
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? escapeCell(iso) : d.toLocaleString();
}

function whatRuns(row) {
    if (row.schedule_type === 'retention') return L.what_retention;
    return L.what_scenarios;
}

function actionsCell(row) {
    if (!DATA.can_write) return '';
    const run = `<button class="btn btn-sm btn-outline-primary" title="${L.confirm_run_now}"
        data-on-click="runNow(${row.id})"><i class="bi bi-play-fill"></i></button>`;
    const pauseOrResume = row.enabled
        ? `<button class="btn btn-sm btn-outline-secondary" title="${L.status_paused}"
             data-on-click="pause(${row.id})"><i class="bi bi-pause-fill"></i></button>`
        : `<button class="btn btn-sm btn-outline-secondary" title="${L.status_enabled}"
             data-on-click="resume(${row.id})"><i class="bi bi-play-circle"></i></button>`;
    const jobs = `<button class="btn btn-sm btn-outline-secondary" title="${L.jobs_title}"
        data-on-click="showJobs(${row.id}, this)"><i class="bi bi-list-task"></i></button>`;
    const edit = `<button class="btn btn-sm btn-outline-secondary"
        data-on-click="openEdit(${row.id})"><i class="bi bi-pencil"></i></button>`;
    const del = `<button class="btn btn-sm btn-outline-danger"
        data-on-click="remove(${row.id})"><i class="bi bi-trash"></i></button>`;
    return run + pauseOrResume + jobs + edit + del;
}

function ownerCell(row) {
    if (!allUsers) return '';
    const suffix = row.owner_user_id === DATA.user_id ? ` (${L.mine})` : '';
    return escapeCell(row.owner_username || row.owner_user_id) + suffix;
}

function td(value, html) {
    // `data-ut-value` is what the unified table sorts on when it differs
    // from what the cell shows (dates, ordered statuses, numbers).
    return `<td data-ut-value="${escapeCell(value == null ? '' : value)}">${html}</td>`;
}

function rowCells(row) {
    // One <td> per column, in the template's column order.
    const paused = !row.enabled
        && String(row.disabled_reason || '').includes('paused');
    const statusOrder = row.enabled ? 2 : (paused ? 1 : 0);
    return td(row.name, `<strong>${escapeCell(row.name)}</strong>`)
        + td(whatRuns(row), whatRuns(row))
        + td(row.interval_minutes, escapeCell(String(row.interval_minutes)))
        + td(statusOrder, statusCell(row))
        + td(row.next_run_at || '', row.enabled ? fmtWhen(row.next_run_at)
                                                : '&mdash;')
        + td(row.last_status || '', row.last_status
            ? `${escapeCell(row.last_status)}${row.consecutive_failures
                ? ` <span class="badge text-bg-warning" title="${escapeCell(L.failure_count)}">${row.consecutive_failures}</span>`
                : ''}`
            : `<span class="text-muted">${escapeCell(L.never)}</span>`)
        + td(row.owner_username || '', ownerCell(row))
        + td('', actionsCell(row));
}

function render() {
    const list = window.UnifiedTable;
    if (!list || !BODY) return;
    if (!rows.length) {
        list.renderRows(BODY, [list.states.empty(COLUMN_COUNT, {
            message: 'No schedules yet.', filtered: false })]);
        return;
    }
    list.renderRows(BODY, rows.map((row) => '<tr>' + rowCells(row) + '</tr>'));
}

function renderJobs(jobs) {
    const list = window.UnifiedTable;
    const body = document.getElementById('schedulesJobsTableBody');
    if (!list || !body) return;
    if (!jobs.length) {
        list.renderRows(body, [list.states.empty(5, {
            message: 'This schedule has not created any jobs yet.' })]);
        return;
    }
    list.renderRows(body, jobs.map((job) => '<tr><td><code>'
        + escapeCell(job.job_id) + '</code></td><td>'
        + escapeCell(job.status) + '</td><td>'
        + escapeCell(String(job.progress)) + '</td><td>'
        + escapeCell(job.source || '') + '</td><td>'
        + fmtWhen(job.created_at) + '</td></tr>'));
}

// ---------------------------------------------------------------------------
// Data
// ---------------------------------------------------------------------------

async function refresh() {
    clearError();
    const list = window.UnifiedTable;
    if (list) {
        list.renderRows(BODY, list.states.loading(COLUMN_COUNT, '...'));
    }
    try {
        const body = await api('GET', `/api/schedules${allUsers ? '?all=1' : ''}`);
        rows = body.items || [];
        render();
    } catch (error) {
        rows = [];
        render();
        showError(error);
    }
}

// ---------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------

function openCreate() {
    editingId = null;
    document.getElementById('scheduleDialogTitle').textContent =
        document.querySelector('label[for="scheduleType"]').textContent;
    document.getElementById('scheduleName').value = '';
    document.getElementById('scheduleInterval').value = 1440;
    document.getElementById('scheduleParameters').value = '';
    showDialog();
}

function openEdit(id) {
    const row = rows.find((r) => r.id === id);
    if (!row) return;
    editingId = id;
    document.getElementById('scheduleName').value = row.name;
    document.getElementById('scheduleInterval').value = row.interval_minutes;
    document.getElementById('scheduleType').value = row.schedule_type;
    document.getElementById('scheduleParameters').value =
        JSON.stringify(row.payload || {}, null, 2);
    showDialog();
}

function showDialog() {
    const el = document.getElementById('scheduleDialog');
    const dialog = bootstrap.Modal.getOrCreateInstance(el);
    document.getElementById('scheduleDialogError').classList.add('d-none');
    dialog.show();
}

function dialogError(message) {
    const box = document.getElementById('scheduleDialogError');
    box.textContent = `${L.dialog_error} ${message}`;
    box.classList.remove('d-none');
}

async function save() {
    const type = document.getElementById('scheduleType').value;
    const name = document.getElementById('scheduleName').value.trim();
    const minutes = Number(document.getElementById('scheduleInterval').value);
    const body = { schedule_type: type, name, interval_minutes: minutes };
    let parameters;
    try {
        parameters = JSON.parse(document.getElementById('scheduleParameters')
            .value || '{}');
    } catch (error) {
        dialogError(L.params_invalid);
        return;
    }
    // The textarea is the payload itself. Wrapping it as {parameters: ...}
    // was rejected by the store as an unknown payload field.
    body.payload = parameters;
    try {
        if (editingId == null) {
            await api('POST', '/api/schedules', body);
        } else {
            await api('PUT', `/api/schedules/${editingId}`, body);
        }
        bootstrap.Modal.getOrCreateInstance(
            document.getElementById('scheduleDialog')).hide();
        await refresh();
    } catch (error) {
        dialogError(error.message);
    }
}

async function pause(id) {
    await guarded(async () => {
        await api('POST', `/api/schedules/${id}/pause`, {});
        await refresh();
    });
}

async function resume(id) {
    await guarded(async () => {
        await api('POST', `/api/schedules/${id}/resume`, {});
        await refresh();
    });
}

async function runNow(id) {
    if (!window.confirm(L.confirm_run_now)) return;
    await guarded(async () => {
        await api('POST', `/api/schedules/${id}/run_now`, {});
        await refresh();
    });
}

async function remove(id) {
    if (!window.confirm(L.confirm_delete)) return;
    await guarded(async () => {
        await api('DELETE', `/api/schedules/${id}`);
        await refresh();
    });
}

async function showJobs(id) {
    jobsForId = id;
    const row = rows.find((r) => r.id === id);
    document.getElementById('schedulesJobsTitle').textContent =
        `${L.jobs_title} ${row ? row.name : id}`;
    document.getElementById('schedulesJobsPanel').classList.remove('d-none');
    try {
        const body = await api('GET', `/api/schedules/${id}/jobs?limit=20`);
        renderJobs(body.items || []);
    } catch (error) {
        renderJobs([]);
        showError(error);
    }
}

function closeJobs() {
    jobsForId = null;
    document.getElementById('schedulesJobsPanel').classList.add('d-none');
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------

function wireSchedulesPage() {
    const toggle = document.getElementById('schedulesAllUsers');
    if (toggle && toggle.dataset.bound !== 'true') {
        toggle.dataset.bound = 'true';
        toggle.addEventListener('change', () => {
            allUsers = toggle.checked;
            refresh();
        });
    }
    return refresh();
}

function initSchedulesPage() {
    if (!document.getElementById('schedulesTableBody')) return Promise.resolve();
    return Promise.resolve(wireSchedulesPage());
}

export default function init() {
    if (document.readyState === 'loading') {
        return new Promise((resolve) => {
            document.addEventListener('DOMContentLoaded', () => {
                Promise.resolve(initSchedulesPage()).then(resolve);
            }, { once: true });
        });
    }
    return initSchedulesPage();
}

// The data-on-click contract: the handlers are reachable from `window`
// (this file is an ES module, so its top-level names are module-scoped).
const page = {
    refresh, openCreate, openEdit, save, pause, resume, runNow, remove,
    showJobs, closeJobs, init,
};
Object.assign(window, {
    refresh: page.refresh, openCreate: page.openCreate, openEdit: page.openEdit,
    save: page.save, pause: page.pause, resume: page.resume,
    runNow: page.runNow, remove: page.remove, showJobs: page.showJobs,
    closeJobs: page.closeJobs,
});

init();