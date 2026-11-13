/**
 * Analyst Categorization View (FR-4)
 *
 * Client logic for the dedicated analyst-categorization interface:
 * - Filtered, paginated browsing of every manually categorized file
 *   (FR-4.1 / FR-4.2: category, analyst, date range, originating query)
 * - Reversible removal of assignments (NFR-3)
 * - Analyst-category management (create/delete) — a namespace fully
 *   separate from smart categories (FR-1.4)
 * - Audit log browsing (FR-1.5)
 *
 * This page only ever calls /api/analyst/* endpoints, which read and
 * write exclusively the analyst tables. The smart taxonomy is never
 * touched from here.
 */

const analystState = {
    page: 1,
    perPage: 25,
    total: 0,
    totalPages: 1,
    canCategorize: false
};

function analystT(key, fallback) {
    const el = document.getElementById('analyst-categorization-page-data');
    if (el) {
        try {
            const data = JSON.parse(el.textContent);
            if (data.translations && data.translations[key]) {
                return data.translations[key];
            }
        } catch (e) { /* fall through */ }
    }
    return fallback;
}

function analystCsrfToken() {
    return document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
}

function analystInitState() {
    const el = document.getElementById('analyst-categorization-page-data');
    if (!el) return;
    try {
        const data = JSON.parse(el.textContent);
        analystState.canCategorize = !!data.canCategorize;
        if (data.translations) {
            window.translations = window.translations || {};
            Object.assign(window.translations, data.translations);
        }
    } catch (e) { /* ignore */ }
}

function analystQuery(extra = {}) {
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
    for (const [k, v] of Object.entries(extra)) params.set(k, v);
    return params.toString();
}

function escapeHtmlText(text) {
    const div = document.createElement('div');
    div.textContent = text == null ? '' : String(text);
    return div.innerHTML;
}

// Attribute-context escaper: unlike escapeHtmlText (innerHTML text
// serialization, which leaves quotes intact), this escapes quotes too so a
// crafted value can never break out of title="/data-*=" attributes.
function escapeAttr(text) {
    return String(text == null ? '' : text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

// ---- Assignments table -------------------------------------------

async function loadAssignments(page = 1) {
    analystState.page = page;
    const body = document.getElementById('assignmentsTableBody');
    if (!body) return;
    body.innerHTML = `<tr><td colspan="7" class="text-center text-muted py-4">${escapeHtmlText(analystT('loading', 'Loading…'))}</td></tr>`;

    try {
        const qs = analystQuery({
            page: page,
            per_page: analystState.perPage
        });
        const response = await fetch(`/api/analyst/assignments?${qs}`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();

        analystState.total = data.total || 0;
        analystState.totalPages = data.total_pages || 1;
        if (!body.isConnected) return;
        const totalEl = document.getElementById('assignmentsTotal');
        if (totalEl) totalEl.textContent = analystState.total;

        const pageInfo = document.getElementById('assignmentsPageInfo');
        if (pageInfo) {
            pageInfo.textContent = analystT('pageOf', 'Page {page} of {pages} ({total} assignments)')
                .replace('{page}', data.page || page)
                .replace('{pages}', analystState.totalPages)
                .replace('{total}', analystState.total);
        }

        if (!data.assignments || data.assignments.length === 0) {
            body.innerHTML = `<tr><td colspan="7" class="text-center text-muted py-4">${
                escapeHtmlText(analystT('noAssignments', 'No analyst-categorized files match the current filters.'))
            }</td></tr>`;
            renderPagination();
            return;
        }

        body.innerHTML = data.assignments.map(row => `
            <tr>
                <td>
                    <a class="file-link" href="/file/${row.path_id}">${escapeHtmlText(row.file_name)}</a>
                    ${row.file_path ? `<span class="file-path" title="${escapeAttr(row.file_path)}">${escapeHtmlText(row.file_path)}</span>` : ''}
                </td>
                <td class="assignment-origin-cell">
                    ${row.source_name
                        ? `<span class="assignment-origin" title="${escapeAttr(analystT('sourceLabel', 'Source'))}"><i class="bi bi-building me-1"></i>${escapeHtmlText(row.source_name)}</span>`
                        : ''}
                    ${row.side_name
                        ? `<span class="assignment-origin" title="${escapeAttr(analystT('sideLabel', 'Side'))}"><i class="bi bi-diagram-3 me-1"></i>${escapeHtmlText(row.side_name)}</span>`
                        : ''}
                    ${!row.source_name && !row.side_name
                        ? `<span class="text-muted small">${escapeHtmlText(analystT('noOrigin', '—'))}</span>`
                        : ''}
                </td>
                <td><span class="badge analyst-category-badge"><i class="bi bi-person-fill me-1"></i>${escapeHtmlText(row.category_name)}</span></td>
                <td>${escapeHtmlText(row.assigned_by_username || analystT('unknownAnalyst', 'unknown'))}</td>
                <td class="text-nowrap">${row.assigned_at ? new Date(row.assigned_at).toLocaleString() : ''}</td>
                <td>${row.source_query
                    ? `<span class="originating-query" title="${escapeAttr(row.source_query)}">${escapeHtmlText(row.source_query)}</span>`
                    : `<span class="text-muted small">${escapeHtmlText(analystT('never', '(no query)'))}</span>`}</td>
                <td class="text-end">
                    ${analystState.canCategorize ? `
                    <button type="button" class="btn btn-sm btn-outline-danger"
                            data-on-click="removeAssignment(${row.path_id}, ${row.category_id})"
                            title="Remove this analyst category (returns the file to uncategorized scope)">
                        <i class="bi bi-x-lg"></i>
                    </button>` : ''}
                </td>
            </tr>
        `).join('');

        renderPagination();
    } catch (error) {
        console.error('Failed to load analyst assignments:', error);
        body.innerHTML = `<tr><td colspan="7" class="text-center text-danger py-4">${escapeHtmlText(analystT('failedToLoadAssignments', 'Failed to load assignments'))}</td></tr>`;
    }
}

function renderPagination() {
    // The assignments list pages through the same bar as every other list:
    // this page used to build its own buttons, which is how two paginations
    // come to exist in one application.
    const container = document.getElementById('assignmentsPagination');
    if (!container) return;
    container.innerHTML = '';
    if (analystState.totalPages <= 1) return;

    import('../modules/rendering/unified-pagination.js').then(module => {
        module.renderUnifiedPagination({
            currentPage: analystState.page,
            totalPages: analystState.totalPages,
            containerId: 'assignmentsPagination',
            onPageChange: (page) => loadAssignments(page),
            urlParams: {},
            showInfo: true,
            showJump: analystState.totalPages > 5,
            baseUrl: window.location.pathname
        });
    });
}

// Reversibility (NFR-3): remove one category from one file. Only the
// analyst layer is affected; smart categories are never touched.
async function removeAssignment(pathId, categoryId) {
    if (!confirm(analystT('confirmRemove', 'Remove analyst category from this file? It returns to uncategorized status for search scope.'))) {
        return;
    }
    try {
        const response = await fetch('/api/analyst/remove', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': analystCsrfToken()
            },
            body: JSON.stringify({ path_ids: [pathId], category_ids: [categoryId] })
        });
        const data = await response.json();
        if (!response.ok || !data.success) throw new Error(data.error || 'failed');
        await loadAssignments(analystState.page);
        await loadAuditLog();
        await refreshStatsAndFilters();
    } catch (error) {
        console.error(error);
        alert(analystT('removeFailed', 'Remove failed') + ': ' + error.message);
    }
}

// ---- Category management ------------------------------------------

async function createAnalystCategory() {
    const input = document.getElementById('newCategoryName');
    const name = input ? input.value.trim() : '';
    if (!name) return;
    try {
        const response = await fetch('/api/analyst/categories', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': analystCsrfToken()
            },
            body: JSON.stringify({ name })
        });
        const data = await response.json();
        if (!response.ok || !data.success) throw new Error(data.error || 'failed');
        input.value = '';
        await refreshStatsAndFilters();
    } catch (error) {
        console.error(error);
        alert(analystT('createFailed', 'Could not create category') + ': ' + error.message);
    }
}

// Delete an analyst category. Accepts the clicked button element (preferred
// - the category name is resolved from the enclosing list item's
// data-category-name attribute, never from an inline JS string, which would
// be an XSS vector for crafted category names) or a bare category id.
async function deleteAnalystCategory(buttonOrId) {
    let categoryId;
    let name = '';
    if (buttonOrId instanceof Element) {
        const item = buttonOrId.closest('.analyst-category-item');
        categoryId = item ? parseInt(item.dataset.categoryId, 10) : NaN;
        name = item ? (item.dataset.categoryName || '') : '';
    } else {
        categoryId = parseInt(buttonOrId, 10);
    }
    if (isNaN(categoryId)) return;
    if (!name) {
        const known = (analystState.categories || []).find(c => c.id === categoryId);
        name = known ? known.name : `#${categoryId}`;
    }

    const message = analystT('confirmDeleteCategory', 'Delete analyst category "{name}"? Its assignments are removed and affected files return to uncategorized status.')
        .replace('{name}', name);
    if (!confirm(message)) return;
    try {
        const response = await fetch(`/api/analyst/categories/${categoryId}`, {
            method: 'DELETE',
            headers: { 'X-CSRFToken': analystCsrfToken() }
        });
        const data = await response.json();
        if (!response.ok || !data.success) throw new Error(data.error || 'failed');
        await refreshStatsAndFilters();
        await loadAssignments(1);
        await loadAuditLog();
    } catch (error) {
        console.error(error);
        alert(analystT('deleteFailed', 'Could not delete category') + ': ' + error.message);
    }
}

function filterByCategory(categoryId) {
    const select = document.getElementById('filterCategory');
    if (select) select.value = String(categoryId);
    loadAssignments(1);
}

function resetFilters() {
    ['filterCategory', 'filterAnalyst', 'filterDateFrom', 'filterDateTo', 'filterQuery', 'filterFile']
        .forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
    loadAssignments(1);
}

// Refresh category list, analyst list and stats after mutations
async function refreshStatsAndFilters() {
    try {
        const [statsRes, categoriesRes, analystsRes] = await Promise.all([
            fetch('/api/analyst/stats'),
            fetch('/api/analyst/categories'),
            fetch('/api/analyst/analysts')
        ]);
        const stats = await statsRes.json();
        const categories = await categoriesRes.json();
        const analysts = await analystsRes.json();

        const setText = (id, value) => {
            const node = document.getElementById(id);
            if (node) node.textContent = value;
        };
        setText('statCategoryCount', stats.category_count ?? 0);
        setText('statCategorizedFiles', stats.categorized_files ?? 0);
        setText('statAssignments', stats.assignment_count ?? 0);
        setText('statAnalysts', stats.analyst_count ?? 0);
        setText('statUncategorized',
            Math.max((stats.total_files ?? 0) - (stats.categorized_files ?? 0), 0));

        // Category filter + management list
        analystState.categories = categories;
        const filterSelect = document.getElementById('filterCategory');
        const currentFilter = filterSelect ? filterSelect.value : '';
        if (filterSelect) {
            filterSelect.innerHTML = '<option value="">' +
                escapeHtmlText(analystT('allCategories', 'All Categories')) + '</option>' +
                categories.map(c => `<option value="${c.id}">${escapeHtmlText(c.name)} (${c.file_count})</option>`).join('');
            filterSelect.value = currentFilter;
        }

        const canManage = analystState.canCategorize;
        const list = document.getElementById('analystCategoryList');
        if (list) {
            list.innerHTML = categories.length === 0
                ? `<li class="text-muted small py-2">${escapeHtmlText(analystT('noCategoriesYet', 'No analyst categories yet — create one from a search or above.'))}</li>`
                : categories.map(cat => `
                    <li class="analyst-category-item" data-category-id="${cat.id}" data-category-name="${escapeAttr(cat.name)}">
                        <div class="d-flex justify-content-between align-items-center w-100">
                            <div class="analyst-category-name">
                                <i class="bi bi-person-check" aria-hidden="true"></i>
                                <span class="analyst-category-label">${escapeHtmlText(cat.name)}</span>
                                <span class="analyst-badge-soft">${cat.file_count}</span>
                            </div>
                            <div class="analyst-category-actions btn-group btn-group-sm">
                                <button type="button" class="btn btn-sm btn-outline-secondary"
                                        data-on-click="filterByCategory(${cat.id})"
                                        title="${escapeAttr(analystT('showFilesInCategory', 'Show files in this category'))}">
                                    <i class="bi bi-funnel"></i>
                                </button>
                                ${canManage ? `
                                <button type="button" class="btn btn-sm btn-outline-danger"
                                        data-on-click="deleteAnalystCategory(this)"
                                        title="${escapeAttr(analystT('deleteCategoryTitle', 'Delete category (files return to uncategorized)'))}">
                                    <i class="bi bi-trash"></i>
                                </button>` : ''}
                            </div>
                        </div>
                    </li>`).join('');
        }

        const analystSelect = document.getElementById('filterAnalyst');
        const currentAnalyst = analystSelect ? analystSelect.value : '';
        if (analystSelect) {
            analystSelect.innerHTML = '<option value="">' + escapeHtmlText(analystT('allAnalysts', 'All Analysts')) + '</option>' +
                analysts.map(a => `<option value="${a.id}">${escapeHtmlText(a.username)} (${a.assignment_count})</option>`).join('');
            analystSelect.value = currentAnalyst;
        }
    } catch (error) {
        console.error('Failed to refresh analyst stats/filters:', error);
    }
}

// ---- Audit log ------------------------------------------------------

async function loadAuditLog() {
    const list = document.getElementById('analystAuditList');
    if (!list) return;
    try {
        const response = await fetch('/api/analyst/log?per_page=30');
        const data = await response.json();
        const entries = data.entries || [];
        if (entries.length === 0) {
            list.innerHTML = `<li class="text-muted small py-2">${escapeHtmlText(analystT('noActionsYet', 'No categorization actions recorded yet.'))}</li>`;
            return;
        }
        list.innerHTML = entries.map(entry => {
            const files = entry.path_count > 0
                ? ' — ' + analystT('fileCount', '{count} file(s)').replace('{count}', String(entry.path_count)) : '';
            const query = entry.source_query
                ? `<span class="originating-query" title="${escapeAttr(entry.source_query)}">${escapeHtmlText(entry.source_query)}</span>` : '';
            return `
                <li class="analyst-audit-entry">
                    <span class="audit-action-${escapeHtmlText(entry.action)}">${escapeHtmlText(entry.action.replace('_', ' '))}</span>
                    <strong>${escapeHtmlText(entry.category_name || '')}</strong>${files}<br>
                    <span class="analyst-audit-meta">
                        ${escapeHtmlText(entry.analyst_username || analystT('unknownAnalyst', 'unknown'))}
                        · ${entry.created_at ? new Date(entry.created_at).toLocaleString() : ''}
                        ${query ? ' · ' + query : ''}
                    </span>
                </li>`;
        }).join('');
    } catch (error) {
        console.error('Failed to load audit log:', error);
        list.innerHTML = `<li class="text-muted small py-2">${escapeHtmlText(analystT('failedAuditLog', 'Failed to load audit log.'))}</li>`;
    }
}

// ---- Export ----------------------------------------------------------

async function exportAnalystCsv() {
    const { openAnalystCsvExportDialog } = await import('../modules/analyst/analyst-csv-export.js');
    return openAnalystCsvExportDialog();
}

function analystFiltersForExport() {
    const params = {};
    const category = document.getElementById('filterCategory')?.value;
    const analyst = document.getElementById('filterAnalyst')?.value;
    const dateFrom = document.getElementById('filterDateFrom')?.value;
    const dateTo = document.getElementById('filterDateTo')?.value;
    const query = document.getElementById('filterQuery')?.value?.trim();
    const fileQuery = document.getElementById('filterFile')?.value?.trim();
    if (category) {
        const cid = parseInt(category, 10);
        if (Number.isFinite(cid)) params.category_id = cid;
    }
    if (analyst) {
        const aid = parseInt(analyst, 10);
        if (Number.isFinite(aid)) params.analyst_id = aid;
    }
    if (dateFrom) params.date_from = dateFrom;
    if (dateTo) params.date_to = dateTo;
    if (query) params.q = query;
    if (fileQuery) params.file_query = fileQuery;
    return params;
}

async function openStructuredExport() {
    const { openStructuredFolderExport } = await import('../modules/file-operations/structured-folder-export.js');
    return openStructuredFolderExport({
        context: 'analyst',
        analystFilters: analystFiltersForExport,
    });
}

// ---- Init -------------------------------------------------------------

function initAnalystCategorizationPage() {
    // Guard per DOM: the page can be initialised by its own top-level self-run and
    // again by the soft-nav default export (or a cache-busted copy) on the same
    // document. The filter inputs are recreated on every visit, so a flag stored on
    // the node resets naturally and never blocks a fresh visit.
    const guard = document.getElementById('filterQuery');
    if (!guard || guard.dataset.analystBooted === '1') return;
    guard.dataset.analystBooted = '1';

    analystInitState();

    // Enter key in filter inputs applies the filters
    ['filterQuery', 'filterFile'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('keydown', e => { if (e.key === 'Enter') loadAssignments(1); });
    });
    ['filterCategory', 'filterAnalyst', 'filterDateFrom', 'filterDateTo'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('change', () => loadAssignments(1));
    });

    loadAssignments(1);
    loadAuditLog();
}

// Expose handlers for inline onclick attributes
if (typeof window !== 'undefined') {
    window.loadAssignments = loadAssignments;
    window.resetFilters = resetFilters;
    window.filterByCategory = filterByCategory;
    window.removeAssignment = removeAssignment;
    window.createAnalystCategory = createAnalystCategory;
    window.deleteAnalystCategory = deleteAnalystCategory;
    window.exportAnalystCsv = exportAnalystCsv;
    window.openStructuredExport = openStructuredExport;
    window.analystQuery = analystQuery;
}

// Module entry point (universal-initializer compatible)
export default async function init() {
    initAnalystCategorizationPage();
}

// Direct-access fallback
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAnalystCategorizationPage);
} else {
    initAnalystCategorizationPage();
}
