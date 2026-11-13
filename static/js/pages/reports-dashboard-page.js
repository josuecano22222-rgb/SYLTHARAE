/**
 * Reports Dashboard page.
 *
 * The counts arrive server-rendered: the page's first paint already holds the
 * numbers from the report statement. This module adds the one thing that needs
 * a round trip - refreshing them - and fills the per-file-type table through
 * the shared unified table engine, so sorting and filtering behave exactly as
 * they do on every other list.
 *
 * CSP-safe: no inline script, no eval, no Function constructor.
 */

function readIsland(id) {
    const node = document.getElementById(id);
    if (!node) return {};
    try { return JSON.parse(node.textContent) || {}; } catch (_) { return {}; }
}

let LABELS = readIsland('reports-dashboard-labels');
let FILTERS = readIsland('reports-dashboard-filters');

function escapeCell(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, (ch) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[ch]));
}

function setText(id, value) {
    const node = document.getElementById(id);
    if (node) node.textContent = value;
}

function showError(message) {
    const box = document.getElementById('reportsDashboardError');
    if (!box) return;
    box.textContent = message || '';
    box.classList.toggle('d-none', !message);
}

function summaryUrl() {
    const params = new URLSearchParams();
    Object.entries(FILTERS || {}).forEach(([key, value]) => {
        if (value !== null && value !== undefined && value !== '') params.append(key, value);
    });
    const query = params.toString();
    return `/api/reports/dashboard${query ? `?${query}` : ''}`;
}

async function fetchSummary() {
    const resp = await fetch(summaryUrl(), {
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
    });
    const body = await resp.json().catch(() => null);
    if (!resp.ok || !body || body.success === false) {
        const message = (body && body.error && body.error.message) || LABELS.refresh_failed;
        throw new Error(message);
    }
    return body;
}

function renderSummary(body) {
    const totals = body.totals || {};
    setText('rdTotal', totals.total ?? 0);
    setText('rdProcessed', totals.success ?? 0);
    setText('rdFailed', totals.failed ?? 0);
    setText('rdSuccessRate', `${totals.success_pct ?? 0}%`);
}

function renderRows(rows) {
    const body = document.getElementById('reportsByTypeTableBody');
    if (!body) return;
    if (!Array.isArray(rows) || !rows.length) {
        body.innerHTML = `<tr><td colspan="5" class="text-muted">${escapeCell(LABELS.no_results)}</td></tr>`;
        return;
    }
    body.innerHTML = rows.map((row) => `<tr>
        <td>${escapeCell(row.file_type)}</td>
        <td class="text-end">${escapeCell(row.total)}</td>
        <td class="text-end">${escapeCell(row.success)}</td>
        <td class="text-end">${escapeCell(row.failed)}</td>
        <td class="text-end">${escapeCell(row.success_pct)}%</td>
    </tr>`).join('');
    // The engine enhances the wrapper it finds around the table; init() is
    // idempotent (an already-enhanced table is skipped).
    const engine = window.UnifiedTable;
    if (engine && typeof engine.init === 'function') {
        try { engine.init(document.getElementById('reportsDashboardPage')); }
        catch (_) { /* the rows are already readable */ }
    }
}

async function refresh() {
    showError('');
    try {
        const body = await fetchSummary();
        renderSummary(body);
        renderRows(body.by_file_type);
    } catch (error) {
        showError(error.message || LABELS.load_failed);
    }
}

// The declarative event layer calls these by name (no inline handlers).
window.refresh = refresh;

document.addEventListener('DOMContentLoaded', () => {
    // First paint: the counts are already rendered by the server; this fills
    // the table from the same statement and makes the page live afterwards.
    if (!document.getElementById('reportsDashboardPage')) return;
    LABELS = readIsland('reports-dashboard-labels');
    FILTERS = readIsland('reports-dashboard-filters');
    refresh();
});
