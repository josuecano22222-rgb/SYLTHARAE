/**
 * Analyst categorization CSV export with column picker (honors view filters).
 */

import { ensureExportExtension, saveExportBlob, chooseExportDestination } from '../core/export-download.js';

const STORAGE_KEY = 'analystCsvExportColumns';

const DEFAULT_CHECKED = new Set([
    'file_id', 'file_name', 'file_path', 'file_type',
    'source', 'side', 'analyst_category', 'assigned_by', 'assigned_at',
    'originating_search_query',
]);

function analystPageData() {
    const el = document.getElementById('analyst-categorization-page-data');
    if (!el) return {};
    try {
        return JSON.parse(el.textContent);
    } catch (_e) {
        return {};
    }
}

function analystT(key, fallback) {
    const data = analystPageData();
    return (data.translations && data.translations[key]) || fallback;
}

function exportColumnsFromPage() {
    const data = analystPageData();
    if (Array.isArray(data.exportColumns) && data.exportColumns.length) {
        return data.exportColumns.map((col) => ({
            key: col.key || col[0],
            label: col.label || col[1] || col.key,
        }));
    }
    return [
        { key: 'assignment_id', label: 'Assignment ID' },
        { key: 'file_id', label: 'File ID' },
        { key: 'file_name', label: 'File name' },
        { key: 'file_path', label: 'File path' },
        { key: 'file_type', label: 'File type' },
        { key: 'source', label: 'Source' },
        { key: 'side', label: 'Side' },
        { key: 'analyst_category', label: 'Analyst category' },
        { key: 'analyst_category_id', label: 'Analyst category ID' },
        { key: 'category_color', label: 'Category color' },
        { key: 'assigned_by', label: 'Assigned by' },
        { key: 'assigned_by_id', label: 'Assigned by (user ID)' },
        { key: 'assigned_at', label: 'Assigned at' },
        { key: 'originating_search_query', label: 'Originating search query' },
    ];
}

function loadSavedColumns() {
    try {
        const raw = sessionStorage.getItem(STORAGE_KEY);
        if (!raw) return null;
        const parsed = JSON.parse(raw);
        return Array.isArray(parsed) ? parsed : null;
    } catch (_e) {
        return null;
    }
}

function saveColumnSelection(keys) {
    try {
        sessionStorage.setItem(STORAGE_KEY, JSON.stringify(keys));
    } catch (_e) { /* ignore */ }
}

function buildFilterQueryString() {
    if (typeof window.analystQuery === 'function') {
        return window.analystQuery();
    }
    const params = new URLSearchParams();
    const category = document.getElementById('filterCategory')?.value;
    const analyst = document.getElementById('filterAnalyst')?.value;
    const dateFrom = document.getElementById('filterDateFrom')?.value;
    const dateTo = document.getElementById('filterDateTo')?.value;
    const query = document.getElementById('filterQuery')?.value?.trim();
    const fileQuery = document.getElementById('filterFile')?.value?.trim();
    if (category) params.set('category_id', category);
    if (analyst) params.set('analyst_id', analyst);
    if (dateFrom) params.set('date_from', dateFrom);
    if (dateTo) params.set('date_to', dateTo);
    if (query) params.set('q', query);
    if (fileQuery) params.set('file_query', fileQuery);
    return params.toString();
}

function ensureDialog() {
    let dialog = document.getElementById('analystCsvExportDialog');
    if (dialog) return dialog;

    dialog = document.createElement('div');
    dialog.id = 'analystCsvExportDialog';
    dialog.className = 'analyst-export-dialog fmas-ops-dialog';
    dialog.hidden = true;
    dialog.setAttribute('role', 'dialog');
    dialog.setAttribute('aria-modal', 'true');
    dialog.innerHTML = `
        <div class="fmas-ops-dialog-backdrop" data-analyst-export-dismiss></div>
        <div class="fmas-ops-dialog-panel">
            <header class="fmas-ops-dialog-header">
                <h2>${analystT('exportCsvTitle', 'Export analyst categorizations')}</h2>
                <button type="button" class="fmas-ops-dialog-close" data-analyst-export-dismiss aria-label="${analystT('close', 'Close')}">&times;</button>
            </header>
            <form id="analystCsvExportForm" class="fmas-ops-dialog-form">
                <p class="small text-muted mb-2">${analystT('exportCsvHint', 'Exports respect the filters on this page. Choose which columns to include in the CSV.')}</p>
                <fieldset class="fmas-ops-columns">
                    <legend>${analystT('chooseColumns', 'Columns to include')}</legend>
                    <div class="fmas-ops-column-actions">
                        <button type="button" class="action-btn" data-analyst-export-columns="all">${analystT('selectAll', 'Select All')}</button>
                        <button type="button" class="action-btn" data-analyst-export-columns="none">${analystT('deselectAll', 'Select None')}</button>
                        <button type="button" class="action-btn" data-analyst-export-columns="default">${analystT('exportDefaultColumns', 'Default set')}</button>
                    </div>
                    <div class="fmas-ops-column-list" id="analystCsvExportColumns"></div>
                </fieldset>
                <div class="fmas-ops-field">
                    <label for="analystCsvFilename">${analystT('fileName', 'File name')}</label>
                    <input type="text" id="analystCsvFilename" class="form-control form-control-sm"
                           placeholder="analyst_categorizations" maxlength="120" />
                </div>
                <div class="fmas-ops-dialog-actions">
                    <button type="button" class="action-btn" data-analyst-export-dismiss>${analystT('cancel', 'Cancel')}</button>
                    <button type="submit" class="action-btn export-btn">${analystT('export', 'Export')}</button>
                </div>
            </form>
        </div>
    `;

    const list = dialog.querySelector('#analystCsvExportColumns');
    const saved = loadSavedColumns();
    exportColumnsFromPage().forEach((col) => {
        const id = `analystCsvCol_${col.key}`;
        const checked = saved
            ? saved.includes(col.key)
            : DEFAULT_CHECKED.has(col.key);
        const label = document.createElement('label');
        label.className = 'fmas-ops-column-item';
        label.innerHTML = `<input type="checkbox" class="analyst-csv-column-check" id="${id}" value="${col.key}" ${checked ? 'checked' : ''}> <span>${col.label}</span>`;
        list.appendChild(label);
    });

    dialog.addEventListener('click', (event) => {
        if (event.target.closest('[data-analyst-export-dismiss]')) {
            dialog.hidden = true;
        }
        const modeBtn = event.target.closest('[data-analyst-export-columns]');
        if (modeBtn) {
            const mode = modeBtn.getAttribute('data-analyst-export-columns');
            dialog.querySelectorAll('.analyst-csv-column-check').forEach((box) => {
                if (mode === 'all') box.checked = true;
                else if (mode === 'none') box.checked = false;
                else box.checked = DEFAULT_CHECKED.has(box.value);
            });
        }
    });

    dialog.querySelector('#analystCsvExportForm').addEventListener('submit', async (event) => {
        event.preventDefault();
        const columns = Array.from(dialog.querySelectorAll('.analyst-csv-column-check:checked'))
            .map((box) => box.value);
        if (!columns.length) {
            window.showWarning?.(analystT('chooseColumnsWarning', 'Choose at least one column'));
            return;
        }
        saveColumnSelection(columns);

        const qs = buildFilterQueryString();
        const params = new URLSearchParams(qs);
        params.set('columns', columns.join(','));
        const rawName = dialog.querySelector('#analystCsvFilename')?.value?.trim()
            || `analyst_categorizations_${new Date().toISOString().slice(0, 10)}`;
        const filename = ensureExportExtension(rawName, 'csv', 'analyst_categorizations');

        let destination = null;
        try {
            destination = await chooseExportDestination(filename);
            if (destination === false) return;
        } catch (_e) { /* fallback download */ }

        try {
            const response = await fetch(`/api/analyst/export?${params.toString()}`, { credentials: 'same-origin' });
            if (!response.ok) {
                const problem = await response.json().catch(() => ({}));
                throw new Error(problem.error || `HTTP ${response.status}`);
            }
            await saveExportBlob(await response.blob(), filename, destination);
            window.showSuccess?.(analystT('exportCsvReady', 'CSV export ready.'));
            dialog.hidden = true;
        } catch (error) {
            window.showError?.(error.message || analystT('exportFailed', 'Export failed'));
        }
    });

    document.body.appendChild(dialog);
    return dialog;
}

/** Open column picker then download CSV with current analyst view filters. */
export function openAnalystCsvExportDialog() {
    const dialog = ensureDialog();
    const nameInput = dialog.querySelector('#analystCsvFilename');
    if (nameInput && !nameInput.value) {
        nameInput.value = `analyst_categorizations_${new Date().toISOString().slice(0, 10)}`;
    }
    dialog.hidden = false;
}

export default openAnalystCsvExportDialog;
