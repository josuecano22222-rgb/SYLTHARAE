/**
 * Detection page (administrators): coverage per detector, run history and
 * re-detection, over the server contract only:
 *   GET  /api/signals/detection/status   exact counts from one snapshot
 *   GET  /api/signals/detection/runs     run rows, newest first, paged
 *   POST /api/signals/redetect           queues a signal_redetection job
 *   GET  /api/jobs/<id>                  the job's status and result summary
 *
 * The browser counts nothing and decides nothing about staleness: every
 * number is the server's, "never analysed" is shown as its own column, and
 * an unavailable detector version is shown as unavailable, not as a
 * version. The id list is split into numbers here; its limits and every
 * other rule are the server's, whose error is shown verbatim. Values are
 * inserted with textContent; errors and file names get dir="auto".
 */

import { getCSRFTokenAsync } from '../modules/core/utils.js';

const DATA = JSON.parse(document.getElementById('detection-page-data').textContent);
const L = JSON.parse(document.getElementById('detection-page-labels').textContent);
const PAGE = DATA.page_size;
const TERMINAL = new Set(['COMPLETED', 'COMPLETED_WITH_WARNINGS', 'FAILED', 'CANCELLED']);

const state = { filters: {}, offset: 0, polling: null };

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

const none = () => el('span', { class: 'text-muted', text: L.none });
const num = (n) => (n === null || n === undefined ? none() : el('span', { text: Number(n).toLocaleString() }));

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
    const box = document.getElementById('detectionError');
    const message = box.querySelector('.state-message, p') || box;
    message.textContent = error.code ? `${error.message} (${error.code})` : error.message;
    box.classList.remove('d-none');
}

function hideError() {
    document.getElementById('detectionError').classList.add('d-none');
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
// Coverage
// ---------------------------------------------------------------------------

async function loadCoverage() {
    const body = await api('GET', '/api/signals/detection/status');
    document.getElementById('coverageNote').textContent = fmt(L.coverage_note, {
        analysable: Number(body.analysable_contents).toLocaleString(),
        stale: Number(body.stale_any_detector).toLocaleString(),
        at: when(body.measured_at),
    });
    const rows = document.getElementById('coverageRows');
    rows.replaceChildren();
    for (const d of body.detectors) {
        const older = Object.values(d.at_older_versions).reduce((a, b) => a + b, 0);
        const version = d.current_version_available
            ? el('code', { text: d.current_version })
            : el('span', { class: 'text-warning', title: L.version_unavailable_help, text: L.version_unavailable });
        const action = d.stale > 0
            ? el('button', { type: 'button', class: 'btn btn-outline-primary btn-sm',
                             'data-detector': d.detector, text: L.redetect_stale })
            : el('span', { class: 'small text-muted', text: L.nothing_stale });
        if (d.stale > 0) {
            action.addEventListener('click', () => guarded(() => submitRedetect({
                scope: 'stale', detectors: [d.detector] })));
        }
        rows.append(el('tr', { 'data-detector': d.detector },
            el('td', {}, el('code', { text: d.detector })),
            el('td', {}, version),
            el('td', {}, num(d.at_current_version.complete)),
            el('td', {}, num(d.at_current_version.truncated)),
            el('td', {}, num(d.at_current_version.no_text)),
            el('td', {}, num(d.at_current_version.failed)),
            el('td', {}, num(older)),
            el('td', {}, num(d.never_analysed)),
            el('td', {}, el('strong', { text: Number(d.stale).toLocaleString() })),
            el('td', { class: 'text-end' }, action)));
    }
}

// ---------------------------------------------------------------------------
// Re-detection
// ---------------------------------------------------------------------------

function syncScope() {
    const scope = document.getElementById('redetectScope').value;
    document.getElementById('redetectIdsBox').classList.toggle('d-none', scope !== 'hash_ids');
    document.getElementById('redetectAllBox').classList.toggle('d-none', scope !== 'all');
}

function readForm() {
    const scope = document.getElementById('redetectScope').value;
    const detectors = DATA.detectors.filter((name) => document.getElementById(`redetect_${name}`).checked);
    if (!detectors.length) throw new Error(L.choose_detector);
    const payload = { scope, detectors };
    if (scope === 'hash_ids') {
        const tokens = document.getElementById('redetectIds').value.split(/[\s,;]+/).filter(Boolean);
        if (!tokens.length) throw new Error(L.ids_required);
        const bad = tokens.filter((t) => !/^[0-9]+$/.test(t));
        if (bad.length) throw new Error(fmt(L.ids_invalid, { bad: bad.slice(0, 5).join(', ') }));
        payload.hash_ids = tokens.map(Number);
    }
    if (scope === 'all' && !document.getElementById('redetectAllConfirm').checked) {
        throw new Error(L.confirm_all);
    }
    return payload;
}

function jobLine(job) {
    const s = job.result_summary || {};
    const box = document.getElementById('redetectJob');
    // No summary yet (queued/running): say only what is known - never zeros.
    const known = ['processed', 'signals', 'failed'].every((k) => Number.isInteger(s[k]));
    box.replaceChildren(
        el('span', { text: known
            ? fmt(L.job_state, { id: job.job_id, status: job.status,
                                 processed: s.processed, signals: s.signals, failed: s.failed })
            : fmt(L.job_status_only, { id: job.job_id, status: job.status }) }),
        ' ',
        el('a', { href: `/operations/jobs/${encodeURIComponent(job.job_id)}`, text: L.open_job }));
    if (job.status === 'COMPLETED_WITH_WARNINGS') box.append(' ', el('span', { text: L.job_done_warnings }));
}

async function follow(jobId) {
    for (let i = 0; i < 1200; i += 1) {
        const { job } = await api('GET', `/api/jobs/${encodeURIComponent(jobId)}`);
        jobLine(job);
        if (TERMINAL.has(job.status)) {
            await loadCoverage();
            await loadRuns(0);
            return job;
        }
        await new Promise((r) => setTimeout(r, 1000));
    }
    return null;
}

async function submitRedetect(payload) {
    const body = await api('POST', '/api/signals/redetect', payload);
    document.getElementById('redetectJob').textContent = fmt(L.job_queued, { id: body.job.job_id });
    return follow(body.job.job_id);
}

// ---------------------------------------------------------------------------
// Runs
// ---------------------------------------------------------------------------

function readRunFilters() {
    const f = {};
    for (const [key, id] of [['detector', 'runDetector'], ['status', 'runStatus'],
                             ['version', 'runVersion'], ['trigger', 'runTrigger']]) {
        const v = document.getElementById(id).value;
        if (v) f[key] = v;
    }
    const hash = document.getElementById('runHash').value.trim();
    if (hash) f.hash_id = hash;
    return f;
}

function fileCell(run) {
    if (run.path_id === null || run.path_id === undefined) return el('span', { class: 'text-muted', text: L.no_file });
    const link = el('a', { href: `/file/${encodeURIComponent(run.path_id)}`, title: L.open_file },
                    el('bdi', { dir: 'auto', text: run.file_name }));
    const extra = run.path_count > 1 ? el('span', { class: 'small text-muted', text: ` ${fmt(L.more_files, { count: run.path_count - 1 })}` }) : null;
    return el('span', {}, link, extra);
}

async function loadRuns(offset) {
    const params = new URLSearchParams({ ...state.filters, limit: String(PAGE), offset: String(offset) });
    const body = await api('GET', `/api/signals/detection/runs?${params.toString()}`);
    state.offset = offset;
    const rows = document.getElementById('runRows');
    rows.replaceChildren();
    if (!body.items.length) {
        rows.append(el('tr', {}, el('td', { colspan: '11', class: 'text-center text-muted py-3',
            text: Object.keys(state.filters).length || offset ? L.runs_empty : L.runs_empty_all })));
    }
    for (const r of body.items) {
        const version = el('span', {}, el('code', { text: r.detector_ver }),
            r.is_current_version ? el('span', { class: 'badge text-bg-light ms-1', text: L.is_current }) : null);
        const chars = r.chars_scanned === null && r.chars_total === null ? none()
            : el('span', { text: fmt(L.chars_of, {
                scanned: r.chars_scanned === null ? L.none : Number(r.chars_scanned).toLocaleString(),
                total: r.chars_total === null ? L.none : Number(r.chars_total).toLocaleString() }) });
        const job = r.job_id ? el('a', { href: `/operations/jobs/${encodeURIComponent(r.job_id)}`, text: L.open_job }) : none();
        rows.append(el('tr', { 'data-hash': String(r.hash_id), 'data-detector': r.detector },
            el('td', { class: 'text-nowrap', text: when(r.ran_at) }),
            el('td', {}, fileCell(r)),
            el('td', {}, el('code', { text: String(r.hash_id) })),
            el('td', {}, el('code', { text: r.detector })),
            el('td', {}, version),
            el('td', { text: L[`status_${r.status}`] || r.status }),
            el('td', {}, num(r.signal_count)),
            el('td', {}, chars),
            el('td', { text: r.trigger }),
            el('td', {}, job),
            el('td', {}, r.error === null ? none() : el('bdi', { dir: 'auto', class: 'text-danger', text: r.error }))));
    }
    const nav = document.getElementById('runPager');
    nav.replaceChildren();
    const newer = el('button', { type: 'button', class: 'btn btn-outline-secondary btn-sm', id: 'runNewer', text: L.newer });
    newer.disabled = offset === 0;
    newer.addEventListener('click', () => guarded(() => loadRuns(Math.max(0, offset - PAGE))));
    const older = el('button', { type: 'button', class: 'btn btn-outline-secondary btn-sm', id: 'runOlder', text: L.older });
    older.disabled = !body.has_more;
    older.addEventListener('click', () => guarded(() => loadRuns(offset + PAGE)));
    nav.append(newer, older);
    document.getElementById('runNote').textContent = body.items.length
        ? `${fmt(L.runs_note, { from: offset + 1, to: offset + body.items.length })} ${body.has_more ? L.runs_more : L.runs_end}`
        : '';
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------

function fillSelect(id, first, values, label = (v) => v) {
    const select = document.getElementById(id);
    select.replaceChildren(el('option', { value: '', text: first }));
    for (const v of values) select.append(el('option', { value: v, text: label(v) }));
}

fillSelect('runDetector', L.any_detector, DATA.detectors);
fillSelect('runStatus', L.any_outcome, DATA.statuses, (s) => L[`status_${s}`] || s);
const boxes = document.getElementById('redetectDetectors');
for (const name of DATA.detectors) {
    boxes.append(el('div', { class: 'form-check' },
        el('input', { class: 'form-check-input', type: 'checkbox', id: `redetect_${name}`, checked: true }),
        el('label', { class: 'form-check-label small', for: `redetect_${name}`, text: name })));
    document.getElementById(`redetect_${name}`).checked = true;
}

document.getElementById('redetectScope').addEventListener('change', syncScope);
document.getElementById('redetectForm').addEventListener('submit', (event) => {
    event.preventDefault();
    guarded(() => submitRedetect(readForm()));
});
document.getElementById('runFilters').addEventListener('submit', (event) => {
    event.preventDefault();
    state.filters = readRunFilters();
    guarded(() => loadRuns(0));
});
document.getElementById('coverageRefresh').addEventListener('click', () => guarded(loadCoverage));

syncScope();
guarded(async () => {
    await loadCoverage();
    await loadRuns(0);
});
