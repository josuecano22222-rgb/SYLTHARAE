/**
 * Bulk actions on the selected files of the File Management and Analysis
 * grid (FMAS): batch export (extracted text / original files) and analyst
 * (manual) categorization of the selection.
 *
 * Exports post the same form the toolbar always aimed at (/files/export) —
 * an endpoint that existed nowhere until now; both modes produce ONE zip
 * download instead of one download per file.
 *
 * Categorization targets ONLY the analyst (manual) taxonomy through the
 * shared /api/analyst/assign and /api/analyst/remove endpoints — the same
 * namespace the search page and the reader use; smart categories are never
 * touched (FR-1.4).
 */

import { apiPost } from '../api/api-client.js';
import { getCSRFToken } from '../core/utils.js';

const CHECKBOX_SELECTOR = '.file-checkbox:checked, .file-select-checkbox:checked, .file-row-item input[type="checkbox"]:checked';

/** Ids of the currently checked file cards. */
export function collectSelectedIds() {
    return Array.from(document.querySelectorAll(CHECKBOX_SELECTOR))
        .map(cb => parseInt(cb.value, 10))
        .filter(id => !isNaN(id));
}

function csrf() {
    return getCSRFToken() ||
        document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
}

/**
 * Build a form and submit it so the browser handles the download
 * (a fetch+blob round trip would also work but adds nothing here).
 * CSRFProtect guards POST routes, so the token rides along as a field.
 */
function submitBulkExport(fileIds, mode) {
    const form = document.createElement('form');
    form.method = 'POST';
    form.action = '/files/export';
    form.style.display = 'none';

    const modeInput = document.createElement('input');
    modeInput.type = 'hidden';
    modeInput.name = 'mode';
    modeInput.value = mode;
    form.appendChild(modeInput);

    const csrfInput = document.createElement('input');
    csrfInput.type = 'hidden';
    csrfInput.name = 'csrf_token';
    csrfInput.value = csrf();
    form.appendChild(csrfInput);

    fileIds.forEach(id => {
        const input = document.createElement('input');
        input.type = 'hidden';
        input.name = 'file_ids';
        input.value = String(id);
        form.appendChild(input);
    });

    document.body.appendChild(form);
    form.submit();
    setTimeout(() => form.remove(), 60000);
}

/**
 * Export Selected: the extracted text of every selected file as one ZIP.
 * (Previously this fired one download per file — with a 50-file selection
 * that meant 50 downloads.)
 */
export function exportSelectedFiles() {
    const ids = collectSelectedIds();
    if (ids.length === 0) {
        window.showWarning?.(window.translations?.pleaseSelectFilesToExport || 'Please select files to export');
        return;
    }
    submitBulkExport(ids, 'text');
}

/**
 * Export Originals: the source files exactly as they sit on disk, batched
 * into a single ZIP (a manifest lists any original that was unavailable).
 */
export function exportSelectedOriginals() {
    const ids = collectSelectedIds();
    if (ids.length === 0) {
        window.showWarning?.(window.translations?.pleaseSelectFilesToExport || 'Please select files to export');
        return;
    }
    submitBulkExport(ids, 'originals');
}

// ====================================================================
// Analyst (manual) categorization of the selection
// ====================================================================

let categoriesLoaded = false;

function bar() {
    return document.getElementById('analystBulkBar');
}

/** Refresh the bar after any checkbox change (delegated, so grid
 *  re-renders need no re-wiring). */
export function updateAnalystBulkBar() {
    const el = bar();
    if (!el) return;
    const ids = collectSelectedIds();
    const countEl = document.getElementById('analystBulkCount');
    if (countEl) countEl.textContent = String(ids.length);
    el.style.display = ids.length > 0 ? 'flex' : 'none';
    if (ids.length > 0 && !categoriesLoaded) {
        loadAnalystCategoriesForBar();
    }
}

async function loadAnalystCategoriesForBar() {
    categoriesLoaded = true; // once, refreshed after each successful assign
    const select = document.getElementById('analystBulkCategorySelect');
    if (!select) return;
    try {
        const response = await fetch('/api/analyst/categories');
        if (!response.ok) return;
        const categories = await response.json();
        const keep = select.value;
        select.innerHTML = '<option value="">' +
            escapeBar(window.translations?.chooseOrCreateCategory || 'Choose analyst category…') +
            '</option>' +
            (Array.isArray(categories) ? categories : []).map(c =>
                `<option value="${c.id}">${escapeBar(c.name)} (${c.file_count ?? 0})</option>`
            ).join('');
        if (keep) select.value = keep;
    } catch (error) {
        console.error('Could not load analyst categories for the bulk bar:', error);
    }
}

/** Minimal escaper for option text (the module avoids pulling utils). */
function escapeBar(text) {
    return String(text == null ? '' : text)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/** Where the selection came from, for the audit log (FR-1.5): the section
 *  header the grid is showing ("Category: media & communications", …). */
function selectionContext() {
    const label = bar()?.dataset.sectionContext
        || document.querySelector('.section-label')?.textContent?.trim()
        || '';
    return label.slice(0, 255);
}

function selectedIdsOrFail() {
    const ids = collectSelectedIds();
    if (ids.length === 0) {
        window.showWarning?.(window.translations?.selectFilesFirst || 'Select one or more files first');
        return null;
    }
    return ids;
}

/** Assign the chosen analyst category (or a newly typed one) to every
 *  selected file. */
export async function assignAnalystCategoriesToSelection() {
    const ids = selectedIdsOrFail();
    if (!ids) return;

    const select = document.getElementById('analystBulkCategorySelect');
    const newInput = document.getElementById('analystBulkNewCategory');
    const categoryId = select ? parseInt(select.value, 10) : NaN;
    const newName = newInput ? newInput.value.trim() : '';

    if (!newName && (isNaN(categoryId) || !categoryId)) {
        window.showWarning?.(window.translations?.chooseOrCreateCategory || 'Choose an analyst category or type a new one');
        return;
    }

    const payload = { path_ids: ids, source_query: selectionContext() };
    if (newName) {
        payload.category_name = newName;
        payload.create_category = true;
    } else {
        payload.category_id = categoryId;
    }

    try {
        const data = await apiPost('/api/analyst/assign', payload, {
            headers: { 'X-CSRFToken': csrf() },
        });
        if (!data.success) {
            throw new Error(data.error || 'Request failed');
        }
        const categoryName = data.category_name || newName;
        window.showSuccess?.(
            (window.translations?.assignedToast || 'Assigned "{category}" to {count} file(s)')
                .replace('{category}', categoryName)
                .replace('{count}', String(data.assigned ?? ids.length))
        );
        if (newInput) newInput.value = '';
        if (select) select.value = '';
        categoriesLoaded = false; // a new category may exist now
        refreshAfterCategorization();
    } catch (error) {
        console.error('Analyst categorization failed:', error);
        window.showError?.((window.translations?.analystActionError || 'Analyst categorization failed') + ': ' + error.message);
    }
}

/** Remove analyst categories from every selected file (reversible, NFR-3). */
export async function removeAnalystCategoriesFromSelection() {
    const ids = selectedIdsOrFail();
    if (!ids) return;

    if (!confirm((window.translations?.removeBulkConfirm ||
        'Remove all analyst categories from {count} selected file(s)? They return to uncategorized for analyst scope.')
        .replace('{count}', String(ids.length)))) {
        return;
    }

    try {
        const data = await apiPost('/api/analyst/remove', {
            path_ids: ids,
            source_query: selectionContext(),
        }, {
            headers: { 'X-CSRFToken': csrf() },
        });
        if (!data.success) {
            throw new Error(data.error || 'Request failed');
        }
        window.showSuccess?.(window.translations?.analystRemoveSuccess || 'Analyst categories removed');
        refreshAfterCategorization();
    } catch (error) {
        console.error('Analyst category removal failed:', error);
        window.showError?.((window.translations?.analystActionError || 'Analyst categorization failed') + ': ' + error.message);
    }
}

/** Same refresh pattern as the page's bulk delete: reload so sidebar counts
 *  and any category badges reflect the change. */
function refreshAfterCategorization() {
    (window.swapNavigate
                            ? window.swapNavigate(window.location.pathname + '?t=' + Date.now())
                            : (window.location.href = window.location.pathname + '?t=' + Date.now()));
}

// Delegated: any checkbox change anywhere updates the bar (grid re-renders
// replace the DOM, so per-element listeners would be lost).
if (typeof document !== 'undefined') {
    document.addEventListener('change', (event) => {
        if (event.target?.matches?.('.file-checkbox, .file-select-checkbox') ||
            event.target?.closest?.('.file-row-item input[type="checkbox"]')) {
            updateAnalystBulkBar();
        }
    });
}
