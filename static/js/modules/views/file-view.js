/**
 * File View Renderer
 * Extracted from the legacy file-management-system.js
 */

import { escapeAttribute, escapeHtml, formatFileSize } from '../core/utils.js';
import { translations as defaultTranslations } from '../core/config.js';

/** Server-rendered strings from #page-data (refresh on each render). */
function pageTranslations() {
    const fromApp = window.appData?.translations;
    const fromWindow = typeof window.translations === 'object' ? window.translations : null;
    return { ...defaultTranslations, ...(fromWindow || {}), ...(fromApp || {}) };
}
import { renderFilePaginationControls, updateNavItemCount, initializeFilePaginationControls } from '../rendering/pagination.js';
import { navigationState, fileNavigationState } from '../core/state.js';
import { getFileViewMode } from '../ui/view-mode.js';
import { selectedTypeValues, setSelectedTypes } from '../file-operations/selectable-files-ops.js';
import { enhanceMultiSelect, refreshMultiSelect, iconForFileType } from '../ui/multi-select-dropdown.js';
import { syncArchivesChromeLayout } from '../ui/archives-chrome-layout.js';

/** Hover/focus tooltips for icon-only FMAS controls (Bootstrap). */
export function initFmasActionTooltips(scope) {
    const root = scope || document.getElementById('unifiedContentView') || document;
    if (!window.bootstrap?.Tooltip) return;
    root.querySelectorAll('.fmas-files-panel [data-bs-toggle="tooltip"], .fmas-files-section [data-bs-toggle="tooltip"]').forEach((el) => {
        try {
            window.bootstrap.Tooltip.getInstance(el)?.dispose();
            window.bootstrap.Tooltip.getOrCreateInstance(el, {
                trigger: 'hover focus',
                container: 'body',
                boundary: 'viewport',
            });
            el.addEventListener('mouseleave', () => {
                try {
                    window.bootstrap.Tooltip.getInstance(el)?.hide();
                } catch (_e) { /* */ }
            }, { passive: true });
        } catch (_error) { /* tooltip unavailable */ }
    });
}

function tipAttrs(label) {
    const t = escapeAttribute(label);
    return `title="${t}" aria-label="${t}" data-bs-toggle="tooltip" data-bs-placement="top" data-bs-container="body"`;
}

function hiddenLabel(label) {
    return `<span class="visually-hidden">${escapeHtml(label)}</span>`;
}

/**
 * One delegated handler for the file list.
 *
 * Cards used to carry their whole payload inside inline handlers:
 *
 *     onclick="showFileDetails?.(12, '<name>', <files as JSON>, 0)"
 *
 * and a filename is not code. escapeHtml is a text-node escaper - it leaves
 * quotes alone - so a name with an apostrophe ended the JavaScript string
 * early and the browser refused to compile the handler ("missing ) after
 * argument list" repeated in the console); a name with a double quote closed
 * the attribute as well. Every card in the listing was dead: clicking did
 * nothing, and the console filled with errors.
 *
 * The click contract is therefore data, not source text:
 *
 *     data-file-id / data-file-name / data-file-index   who the card is
 *     data-file-action="open" | "export" | "report" | "native" | "locate" | "saveas" | "full-view"
 *
 * Names live in attributes escaped with escapeAttribute and are read back with
 * getAttribute(), which the browser un-escapes - so the modal still shows the
 * real filename, and no filename is ever parsed as code. Links, checkboxes and
 * other native controls keep their own behaviour.
 *
 * @param {Event} event - click event (delegated from the content container)
 * @returns {boolean} true when the click was handled here
 */
export function handleFileCardClick(event) {
    const target = event && event.target;
    if (!target || typeof target.closest !== 'function') return false;

    if (target.closest('.fmas-bulk-actions')) return false;

    if (target.closest('input[type="checkbox"], .file-checkbox, .file-select-checkbox')) {
        return false;
    }
    if (target.closest('a[data-file-action="full-view"]')) {
        return false;
    }

    const card = target.closest('.file-card, .file-grid-item, [data-file-id]');
    if (!card) return false;

    const actionEl = target.closest('[data-file-action]');
    const action = (actionEl && actionEl.getAttribute('data-file-action')) || 'open';
    if (action === 'full-view') return false;

    const idSource = (actionEl && actionEl.getAttribute('data-file-id'))
        || card.getAttribute('data-file-id');
    const fileId = parseInt(idSource, 10);
    if (!Number.isInteger(fileId)) return false;

    const handled = (() => {
    if (action === 'export') {
        if (typeof window.exportFile === 'function') window.exportFile(fileId);
        return true;
    }
    if (action === 'saveas') {
        if (typeof window.exportFileWithRename === 'function') {
            window.exportFileWithRename(fileId, card.getAttribute('data-file-name') || '');
        }
        return true;
    }
    if (action === 'native') {
        if (typeof window.openInNativeApp === 'function') window.openInNativeApp(fileId);
        return true;
    }
    if (action === 'locate') {
        if (typeof window.locateFile === 'function') window.locateFile(fileId);
        return true;
    }
    if (action === 'report') {
        if (typeof window.openAnalyticalReportForFile === 'function') {
            window.openAnalyticalReportForFile(fileId);
        }
        return true;
    }
    if (action !== 'open') return false;

    const name = card.getAttribute('data-file-name') || 'File';
    const indexAttr = card.getAttribute('data-file-index');
    const index = indexAttr === null ? -1 : parseInt(indexAttr, 10);
    // The page on screen is the list this renderer stored, so Previous/Next in
    // the modal keeps walking the page the user is looking at.
    const files = Array.isArray(fileNavigationState.currentFiles)
        ? fileNavigationState.currentFiles : null;
    const details = window.fms && window.fms.fileOperations
        && window.fms.fileOperations.fileDetails;
    const open = (details && details.showFileDetails) || window.showFileDetails;
    if (typeof open !== 'function') return false;
    open(fileId, name, files, Number.isInteger(index) ? index : -1);
    return true;
    })();

    if (handled && event.preventDefault) event.preventDefault();
    return handled;
}

//: Delegation is registered once per document, however often the list re-renders.
let fileCardDelegationBound = false;

/**
 * Attach the delegated click handler to the rendered file list (idempotent).
 */
export function ensureFileCardDelegation() {
    if (fileCardDelegationBound) return;
    const host = (typeof document !== 'undefined'
        && (document.getElementById('unifiedContentView') || document.body))
        || null;
    if (!host || typeof host.addEventListener !== 'function') return;
    host.addEventListener('click', handleFileCardClick);
    fileCardDelegationBound = true;
}

/**
 * Render a single file card (for list view)
 */
function renderFileCard(file, fileNumber, index) {
    const translations = pageTranslations();
    const fileSize = formatFileSize(file.size || 0);
    const fileType = file.type || file.file_type || '';
    const fileDate = file.file_date || file.date || file.created_at || '';
    const fileSource = file.source || file.source_name || '';
    const fileSide = file.side || file.side_name || '';
    const displayName = file.name || `File ${fileNumber}`;
    const safeName = escapeHtml(displayName);
    // Attribute context needs the attribute escaper: a quote in a filename
    // must not be able to close the attribute or open a handler of its own.
    const nameAttr = escapeAttribute(displayName);
    
    return `
        <div class="file-card" data-file-id="${file.id}" data-file-action="open" data-file-name="${nameAttr}" data-file-index="${index}" data-file-type="${escapeAttribute(fileType || '')}" style="border: 1px solid #e2e8f0; border-radius: 0.5rem; padding: 1rem; margin-bottom: 0.75rem; display: flex; gap: 1rem; align-items: flex-start; cursor: pointer; position: relative;">
            <div class="file-card-number" style="position: absolute; top: 0.5rem; left: 0.5rem; background: #3b82f6; color: white; width: 28px; height: 28px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 0.75rem; font-weight: 600; z-index: 10; box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);">${fileNumber}</div>
            <div class="file-card-icon"><i class="bi bi-file-earmark" aria-hidden="true"></i></div>
            <div class="file-card-body" style="flex: 1;">
                <div class="file-card-name" style="font-weight: 600; margin-bottom: 0.25rem;">${safeName}</div>
                <div class="file-card-meta" style="color: #64748b; font-size: 0.875rem; display: flex; gap: 1rem; flex-wrap: wrap;">
                    <span><i class="bi bi-diagram-3"></i> ${escapeHtml(fileSide || '—')}</span>
                    <span><i class="bi bi-building"></i> ${escapeHtml(fileSource || '—')}</span>
                    <span><i class="bi bi-hdd"></i> ${fileSize}</span>
                    <span><i class="bi bi-calendar"></i> ${escapeHtml(fileDate || '—')}</span>
                    <span><i class="bi bi-tag"></i> ${escapeHtml(fileType || '—')}</span>
                </div>
                <div class="file-card-actions" style="margin-top: 0.5rem; display: flex; gap: 0.5rem; flex-wrap: wrap;">
                    <button type="button" class="action-btn action-btn-icon-only" data-file-action="open" ${tipAttrs(translations.viewDetails || 'View Details')}>
                        <i class="bi bi-eye" aria-hidden="true"></i>${hiddenLabel(translations.viewDetails || 'View Details')}
                    </button>
                    <a class="action-btn action-btn-icon-only" href="/file/${file.id}" target="_blank" rel="noopener" data-file-action="full-view" ${tipAttrs(translations.openFullView || 'Open Full View')}>
                        <i class="bi bi-box-arrow-up-right" aria-hidden="true"></i>${hiddenLabel(translations.fullView || 'Full View')}
                    </a>
                    <button type="button" class="action-btn export-btn action-btn-icon-only" data-file-action="export" ${tipAttrs(translations.exportFile || 'Export File')}>
                        <i class="bi bi-download" aria-hidden="true"></i>${hiddenLabel(translations.export || 'Export')}
                    </button>
                    <button type="button" class="action-btn action-btn-icon-only" data-file-action="saveas" ${tipAttrs(translations.saveAsTitle || translations.saveAs || 'Save As (rename on export)')}>
                        <i class="bi bi-save" aria-hidden="true"></i>${hiddenLabel(translations.saveAs || 'Save As')}
                    </button>
                    <button type="button" class="action-btn action-btn-icon-only" data-file-action="native" ${tipAttrs(translations.openInNativeApp || 'Open in native application')}>
                        <i class="bi bi-app-indicator" aria-hidden="true"></i>${hiddenLabel(translations.openNative || 'Open Native')}
                    </button>
                    <button type="button" class="action-btn action-btn-icon-only" data-file-action="locate" ${tipAttrs(translations.locateFolder || 'Locate folder')}>
                        <i class="bi bi-folder2-open" aria-hidden="true"></i>${hiddenLabel(translations.locate || 'Locate')}
                    </button>
                    <button type="button" class="action-btn fmas-report-btn action-btn-icon-only" data-file-action="report" ${tipAttrs(translations.analyticalReport || 'Analytical report')}>
                        <i class="bi bi-file-earmark-richtext" aria-hidden="true"></i>${hiddenLabel(translations.report || 'Report')}
                    </button>
                </div>
            </div>
            <div class="file-card-select" style="display: flex; align-items: center;">
                <input type="checkbox" class="file-checkbox" value="${file.id}" aria-label="${translations.selectFile || 'Select file'}: ${nameAttr}">
            </div>
        </div>
    `;
}

/**
 * Render a file grid item (for grid view)
 */
function renderFileGridItem(file, fileNumber, index) {
    const translations = pageTranslations();
    const fileSize = formatFileSize(file.size || 0);
    const fileType = file.type || file.file_type || '';
    const fileDate = file.file_date || file.date || file.created_at || '';
    const displayName = file.name || `File ${fileNumber}`;
    const safeName = escapeHtml(displayName);
    const nameAttr = escapeAttribute(displayName);
    
    return `
        <div class="file-grid-item" data-file-id="${file.id}" data-file-action="open" data-file-name="${nameAttr}" data-file-index="${index}" data-file-type="${escapeAttribute(fileType || '')}" style="border: 1px solid #e2e8f0; border-radius: 0.5rem; padding: 1rem; cursor: pointer; position: relative; background: white; transition: transform 0.2s, box-shadow 0.2s;" data-on-mouseover="this.style.transform='translateY(-2px)'; this.style.boxShadow='0 4px 8px rgba(0,0,0,0.1)'" data-on-mouseout="this.style.transform=''; this.style.boxShadow=''">
            <div class="file-card-number" style="position: absolute; top: 0.5rem; right: 0.5rem; background: #3b82f6; color: white; width: 28px; height: 28px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 0.75rem; font-weight: 600; z-index: 10; box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);">${fileNumber}</div>
            <div style="text-align: center; margin-bottom: 0.75rem;">
                <div class="file-grid-icon" style="font-size: 3rem; color: #3b82f6; margin-bottom: 0.5rem;">
                    <i class="bi bi-file-earmark" aria-hidden="true"></i>
                </div>
            </div>
            <div class="file-grid-body">
                <div class="file-grid-name" style="font-weight: 600; margin-bottom: 0.5rem; text-align: center; font-size: 0.875rem; line-height: 1.4; min-height: 2.8em; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;" title="${nameAttr}">${safeName}</div>
                <div class="file-grid-meta" style="color: #64748b; font-size: 0.75rem; display: flex; flex-direction: column; gap: 0.25rem;">
                    <div><i class="bi bi-tag"></i> ${escapeHtml(fileType || '—')}</div>
                    <div><i class="bi bi-hdd"></i> ${fileSize}</div>
                    <div><i class="bi bi-calendar"></i> ${escapeHtml(fileDate || '—')}</div>
                </div>
                <div class="file-grid-actions" style="margin-top: 0.75rem; display: flex; gap: 0.25rem; justify-content: center; flex-wrap: wrap;">
                    <button type="button" class="action-btn action-btn-icon-only" data-file-action="open" ${tipAttrs(translations.viewDetails || 'View Details')}>
                        <i class="bi bi-eye" aria-hidden="true"></i>${hiddenLabel(translations.viewDetails || 'View Details')}
                    </button>
                    <a class="action-btn action-btn-icon-only" href="/file/${file.id}" target="_blank" rel="noopener" data-file-action="full-view" ${tipAttrs(translations.openFullView || 'Open Full View')}>
                        <i class="bi bi-box-arrow-up-right" aria-hidden="true"></i>${hiddenLabel(translations.fullView || 'Full View')}
                    </a>
                    <button type="button" class="action-btn export-btn action-btn-icon-only" data-file-action="export" ${tipAttrs(translations.exportFile || 'Export File')}>
                        <i class="bi bi-download" aria-hidden="true"></i>${hiddenLabel(translations.export || 'Export')}
                    </button>
                    <button type="button" class="action-btn action-btn-icon-only" data-file-action="saveas" ${tipAttrs(translations.saveAsTitle || translations.saveAs || 'Save As')}>
                        <i class="bi bi-save" aria-hidden="true"></i>${hiddenLabel(translations.saveAs || 'Save As')}
                    </button>
                    <button type="button" class="action-btn action-btn-icon-only" data-file-action="native" ${tipAttrs(translations.openInNativeApp || 'Open in native application')}>
                        <i class="bi bi-app-indicator" aria-hidden="true"></i>${hiddenLabel(translations.openNative || 'Open Native')}
                    </button>
                    <button type="button" class="action-btn action-btn-icon-only" data-file-action="locate" ${tipAttrs(translations.locateFolder || 'Locate folder')}>
                        <i class="bi bi-folder2-open" aria-hidden="true"></i>${hiddenLabel(translations.locate || 'Locate')}
                    </button>
                    <button type="button" class="action-btn fmas-report-btn action-btn-icon-only" data-file-action="report" ${tipAttrs(translations.analyticalReport || 'Analytical report')}>
                        <i class="bi bi-file-earmark-richtext" aria-hidden="true"></i>${hiddenLabel(translations.report || 'Report')}
                    </button>
                </div>
                <div class="file-grid-select" style="margin-top: 0.5rem; text-align: center;">
                    <input type="checkbox" class="file-checkbox" value="${file.id}" aria-label="${translations.selectFile || 'Select file'}: ${nameAttr}">
                </div>
            </div>
        </div>
    `;
}

export function renderFilesView(files, section, itemName, pagination) {
    const contentView = document.getElementById('unifiedContentView');
    if (!contentView) return;
    const translations = pageTranslations();

    let html = '';
    const sectionLabel = section || 'Section';

    if (pagination) {
        navigationState.filePagination = {
            currentPage: pagination.page,
            perPage: pagination.per_page,
            total: pagination.total,
            totalPages: pagination.total_pages,
            totalSize: pagination.total_size,
            has_prev: pagination.has_prev,
            has_next: pagination.has_next
        };
        const startItem = files.length ? (pagination.page - 1) * pagination.per_page + 1 : 0;
        const endItem = files.length ? Math.min((pagination.page - 1) * pagination.per_page + files.length, pagination.total) : 0;
        updateNavItemCount(startItem, endItem, pagination.total);
    }

    const filters = navigationState.fileListFilters || {};
        const currentPerPage = pagination ? pagination.per_page : (navigationState.filePagination?.perPage || 50);
        const currentFileViewMode = getFileViewMode();
    const typeOptions = collectFileTypeOptions(files, filters.fileType);
    const sortValue = `${filters.sortBy || 'file_date'}_${filters.sortDir || 'desc'}`;

    html += '<div class="section fmas-files-panel fmas-files-section" style="margin-bottom: 1.5rem;">';
    html += '<div class="section-header fmas-files-section-header">';
    html += `<div class="section-label"><i class="bi bi-folder2-open"></i> ${escapeHtml(sectionLabel)}: ${escapeHtml(itemName || 'Item')}</div>`;
    html += '</div>';
    html += buildFilesToolbar({
        filters,
        currentPerPage,
        currentFileViewMode,
        typeOptions,
        sortValue,
        pagination,
        shownCount: files?.length || 0
    });

    html += '<div class="fmas-files-scroll-body">';

        if (window.appData?.canCategorize) {
            html += `<div id="analystBulkBar" data-section-context="${escapeAttribute(`${sectionLabel}: ${itemName || ''}`)}"
                      class="fmas-analyst-bulk-bar"
                          style="display: none; align-items: center; gap: 0.5rem; flex-wrap: wrap; margin-top: 0.75rem; padding: 0.6rem 0.75rem; border: 1px solid #ddd6fe; border-radius: 0.5rem; background: #f5f3ff;">`;
            html += `<span style="font-size: 0.875rem; color: #4c1d95;"><i class="bi bi-person-check me-1" aria-hidden="true"></i><strong id="analystBulkCount">0</strong> ${translations.selected || 'selected'}</span>`;
            html += `<select id="analystBulkCategorySelect" class="form-select form-select-sm" style="max-width: 240px;" title="${translations.analystBulkCategoryTitle || 'Analyst-defined categories only (never smart categories)'}"><option value="">${translations.chooseOrCreateCategory || 'Choose analyst category…'}</option></select>`;
            html += `<input type="text" id="analystBulkNewCategory" class="form-control form-control-sm" style="max-width: 200px;" placeholder="${translations.orNewCategoryName || '…or new category name'}" maxlength="255">`;
            const categorizeLabel = translations.categorizeSelectedTitle || translations.categorize || 'Assign analyst category to selection';
            const removeLabel = translations.removeCategoriesTitle || translations.removeCategories || 'Remove analyst categories from selection';
            html += `<button type="button" class="action-btn action-btn-icon-only fmas-analyst-categorize-btn" data-on-click="assignAnalystCategoriesToSelection?.()" ${tipAttrs(categorizeLabel)}>`;
            html += `<i class="bi bi-tag-fill" aria-hidden="true"></i>${hiddenLabel(translations.categorize || 'Categorize')}</button>`;
            html += `<button type="button" class="action-btn action-btn-icon-only fmas-analyst-remove-btn" data-on-click="removeAnalystCategoriesFromSelectionShared?.()" ${tipAttrs(removeLabel)}>`;
            html += `<i class="bi bi-tag" aria-hidden="true"></i>${hiddenLabel(translations.removeCategories || 'Remove')}</button>`;
            html += '</div>';
        }

    if (files && files.length > 0) {
        const filesList = files.map(file => ({ id: file.id, name: file.name }));
        const startNumber = pagination ? (pagination.page - 1) * pagination.per_page + 1 : 1;
        
        if (currentFileViewMode === 'grid') {
            html += '<div class="files-grid-view" id="fmasFilesContainer" style="display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 1rem; margin-top: 1rem;">';
            files.forEach((file, index) => {
                html += renderFileGridItem(file, startNumber + index, index);
            });
            html += '</div>';
        } else {
            html += '<div class="files-list-view" id="fmasFilesContainer" style="margin-top: 1rem;">';
            files.forEach((file, index) => {
                html += renderFileCard(file, startNumber + index, index);
            });
            html += '</div>';
        }

        fileNavigationState.currentFiles = filesList;
        html += renderFilePaginationControls();
    } else {
        html += `<div class="empty-state" id="fmasFilesEmpty">${translations.noFilesFound || 'No files found'}</div>`;
        html += '<div id="fmasFilesContainer"></div>';
        html += '<div id="file-pagination-container" class="file-pagination-container"></div>';
    }

    html += '</div>'; // fmas-files-scroll-body
    html += '</div>'; // section

    contentView.innerHTML = html;
    ensureFileCardDelegation();
    wireFilesToolbar();
    initFmasActionTooltips(contentView);
    syncArchivesChromeLayout();

    requestAnimationFrame(() => {
        if (files && files.length > 0 && pagination) {
            initializeFilePaginationControls();
        }
        updateShowMoreVisibility();
    });
}

/**
 * Append additional files after a Show More fetch without rebuilding the toolbar.
 */
export function appendFilesToView(files, section, itemName, pagination) {
    const container = document.getElementById('fmasFilesContainer');
    if (!container || !files?.length) {
        if (pagination) syncPaginationState(pagination, (navigationState.loadedFiles || []).length);
        updateShowMoreVisibility();
        return;
    }

    if (pagination) {
        syncPaginationState(pagination, (navigationState.loadedFiles || []).length);
    }

    const existingCount = container.querySelectorAll('.file-card, .file-grid-item').length;
    const currentFileViewMode = getFileViewMode();
    const frag = document.createDocumentFragment();
    const temp = document.createElement('div');

    files.forEach((file, index) => {
        const fileNumber = existingCount + index + 1;
        const globalIndex = existingCount + index;
        temp.innerHTML = currentFileViewMode === 'grid'
            ? renderFileGridItem(file, fileNumber, globalIndex)
            : renderFileCard(file, fileNumber, globalIndex);
        while (temp.firstChild) frag.appendChild(temp.firstChild);
    });
    container.appendChild(frag);

    fileNavigationState.currentFiles = (navigationState.loadedFiles || []).map((f) => ({
        id: f.id,
        name: f.name
    }));

    const empty = document.getElementById('fmasFilesEmpty');
    if (empty) empty.remove();

    ensureFileCardDelegation();
    refreshTypeFilterOptions();
    updateShowMoreVisibility();
    initFmasActionTooltips(container);

    const pagContainer = document.getElementById('file-pagination-container');
    if (pagContainer && navigationState.filePagination?.totalPages > 1) {
        initializeFilePaginationControls();
    }
}

function syncPaginationState(pagination, shownCount) {
    const translations = pageTranslations();
    navigationState.filePagination = {
        currentPage: pagination.page,
        perPage: pagination.per_page,
        total: pagination.total,
        totalPages: pagination.total_pages,
        totalSize: pagination.total_size,
        has_prev: pagination.has_prev,
        has_next: pagination.has_next
    };
    updateNavItemCount(shownCount ? 1 : 0, shownCount, pagination.total);
    const meta = document.getElementById('fmasFilesMeta');
    if (meta) {
        meta.textContent = `${shownCount} / ${pagination.total} ${translations.files || 'files'}`;
    }
}

function collectFileTypeOptions(files, selected) {
    const set = new Set();
    (files || []).forEach((f) => {
        const t = normalizeType(f.type || f.file_type);
        if (t) set.add(t);
    });
    const selectedList = Array.isArray(selected)
        ? selected
        : String(selected || '').split(',').map((v) => v.trim()).filter(Boolean);
    selectedList.forEach((value) => {
        const t = normalizeType(value);
        if (t) set.add(t);
    });
    // Common library types always offered for filtering even if not on this page
    ['.pdf', '.docx', '.doc', '.xlsx', '.xls', '.pptx', '.txt', '.csv', '.jpg', '.jpeg', '.png', '.gif', '.zip', '.eml', '.html', '.json', '.xml'].forEach((t) => set.add(t));
    return Array.from(set).filter(Boolean).sort((a, b) => a.localeCompare(b));
}

function normalizeType(value) {
    if (!value) return '';
    let t = String(value).trim().toLowerCase();
    if (!t || t === 'unknown' || t === '—') return '';
    if (!t.startsWith('.')) t = '.' + t.replace(/^\./, '');
    return t;
}

function buildFilesToolbar({ filters, currentPerPage, currentFileViewMode, typeOptions, sortValue, pagination, shownCount }) {
    const translations = pageTranslations();
    const total = pagination?.total ?? shownCount;
    let html = '<div class="fmas-files-toolbar" role="toolbar" aria-label="File list controls">';

    html += '<div class="fmas-toolbar-row fmas-toolbar-primary">';
    html += '<div class="fmas-toolbar-search">';
    html += `<label for="fileSearchInput" class="sr-only">${translations.searchFiles || 'Search files'}</label>`;
    html += `<input type="search" id="fileSearchInput" class="fmas-control-input" value="${escapeAttribute(filters.search || '')}" placeholder="${translations.searchFiles || 'Search files by name or path…'}" title="${translations.searchFiles || 'Search files'}" aria-label="${translations.searchFiles || 'Search files'}">`;
    html += '<i class="bi bi-search fmas-search-icon" aria-hidden="true"></i>';
    html += '</div>';

    html += '<div class="fmas-toolbar-group fmas-type-filter-group">';
    html += `<label for="fileTypeFilter" class="fmas-control-label">${translations.fileType || 'Type'}</label>`;
    html += `<select id="fileTypeFilter" class="form-select form-select-sm fmas-control-select msdd-enhance" multiple data-compact-multi="type" data-placeholder="${escapeAttribute(translations.allTypes || 'All types')}" title="${translations.filterByTypeMulti || 'Filter by one or more file types'}" aria-label="${translations.filterByTypeMulti || 'Filter by file types'}">`;
    const selectedTypes = new Set(
        (Array.isArray(filters.fileType)
            ? filters.fileType
            : String(filters.fileType || '').split(','))
            .map((v) => normalizeType(v))
            .filter(Boolean)
    );
    typeOptions.forEach((t) => {
        const selected = selectedTypes.has(t) ? ' selected' : '';
        html += `<option value="${escapeAttribute(t)}"${selected}>${escapeHtml(t)}</option>`;
    });
    html += '</select>';
    html += '</div>';

    html += '<div class="fmas-toolbar-group">';
    html += `<label for="fileSortBy" class="fmas-control-label">${translations.sortBy || 'Sort'}</label>`;
    html += `<select id="fileSortBy" class="form-select form-select-sm fmas-control-select" title="${translations.sortBy || 'Sort files'}" aria-label="${translations.sortBy || 'Sort files'}">`;
    const sortChoices = [
        ['file_date_desc', translations.dateNewest || 'Date (newest)'],
        ['file_date_asc', translations.dateOldest || 'Date (oldest)'],
        ['name_asc', translations.nameAZ || 'Name (A–Z)'],
        ['name_desc', translations.nameZA || 'Name (Z–A)'],
        ['size_desc', translations.sizeLargest || 'Size (largest)'],
        ['size_asc', translations.sizeSmallest || 'Size (smallest)'],
        ['type_asc', translations.typeAZ || 'Type (A–Z)'],
        ['type_desc', translations.typeZA || 'Type (Z–A)']
    ];
    sortChoices.forEach(([value, label]) => {
        html += `<option value="${value}"${sortValue === value ? ' selected' : ''}>${escapeHtml(label)}</option>`;
    });
    html += '</select>';
    html += '</div>';

    html += '<div class="fmas-toolbar-group">';
    html += `<label for="perPageFiles" class="fmas-control-label">${translations.perPage || 'Per page'}</label>`;
    html += `<select id="perPageFiles" class="form-select form-select-sm fmas-control-select" data-on-change="handleFilePerPageChange()" title="${translations.itemsPerPage || 'Items per page'}" aria-label="${translations.itemsPerPage || 'Items per page'}">`;
    [10, 25, 50, 100, 200, 500].forEach((n) => {
        html += `<option value="${n}"${Number(currentPerPage) === n ? ' selected' : ''}>${n}</option>`;
    });
    html += '</select>';
    html += '</div>';

    html += `<button type="button" class="action-btn fmas-apply-filters-btn" data-on-click="applyFileListControls?.()" title="${translations.apply || 'Apply'}">`;
    html += `<i class="bi bi-funnel me-1" aria-hidden="true"></i>${translations.apply || 'Apply'}`;
    html += '</button>';
    html += '</div>'; // primary row

    html += '<div class="fmas-toolbar-row fmas-toolbar-secondary">';
    html += `<span class="fmas-files-meta" id="fmasFilesMeta">${shownCount} / ${total} ${translations.files || 'files'}</span>`;

    html += '<div class="file-view-toggle fmas-view-toggle" role="group" aria-label="View mode">';
    html += `<button type="button" class="view-toggle-btn ${currentFileViewMode === 'list' ? 'active' : ''}" data-view="list" data-on-click="setFileViewMode('list')" title="${translations.listView || 'List View'}" aria-label="${translations.switchToListView || 'Switch to List View'}"><i class="bi bi-list-ul" aria-hidden="true"></i></button>`;
    html += `<button type="button" class="view-toggle-btn ${currentFileViewMode === 'grid' ? 'active' : ''}" data-view="grid" data-on-click="setFileViewMode('grid')" title="${translations.gridView || 'Grid View'}" aria-label="${translations.switchToGridView || 'Switch to Grid View'}"><i class="bi bi-grid-3x3" aria-hidden="true"></i></button>`;
    html += '</div>';

    html += '<div class="fmas-bulk-actions">';
    const selectedCount = document.querySelectorAll('.file-checkbox:checked, .file-select-checkbox:checked').length;
    const selectionActive = selectedCount > 0;
    const selectLabel = translations.selectAll || 'Select All';
    const deselectLabel = translations.deselectAll || 'Select None';
    const toggleLabel = selectionActive ? deselectLabel : selectLabel;
    html += `<button type="button" class="action-btn fmas-select-toggle action-btn-icon-only${selectionActive ? ' is-selected' : ''}" id="selectToggleBtn" data-on-click="toggleAllFilesSelectionShared?.()" aria-pressed="${selectionActive ? 'true' : 'false'}" ${tipAttrs(toggleLabel)}>`;
    html += `<i class="bi ${selectionActive ? 'bi-dash-square-fill' : 'bi-check-square-fill'} select-toggle-icon" aria-hidden="true"></i>`;
    html += hiddenLabel(toggleLabel);
    html += `<span class="select-toggle-count" id="selectToggleCount"${selectionActive ? '' : ' hidden'}>${selectedCount}</span>`;
    html += '</button>';
    const bulkIcon = (action, icon, label, extraClass = '') =>
        `<button type="button" class="action-btn action-btn-icon-only ${extraClass}" data-doc-action="${action}" ${tipAttrs(label)}>` +
        `<i class="bi ${icon}" aria-hidden="true"></i>${hiddenLabel(label)}</button>`;
    html += bulkIcon('view-analysis', 'bi-tags', translations.viewAnalysisTitle || translations.viewAnalysis || 'View categories, category terms, and keywords for the selection');
    html += bulkIcon('export-text', 'bi-file-earmark-arrow-down', translations.exportSelectedContentTitle || translations.exportSelectedFiles || 'Export the extracted content of the selection as one archive', 'export-btn');
    html += bulkIcon('export-originals', 'bi-file-earmark-zip', translations.exportOriginalsZipTitle || translations.exportOriginalsTitle || translations.exportOriginals || 'Export the original files of the selection as one ZIP', 'export-btn fmas-export-originals');
    html += bulkIcon('export-text-folder', 'bi-folder-plus', translations.exportTextToFolder || translations.textToFolder || 'Choose a folder (or create a subfolder) for extracted text', 'export-btn');
    html += bulkIcon('export-originals-folder', 'bi-folder-symlink', translations.exportOriginalsToFolder || translations.originalsToFolder || 'Choose a folder (or create a subfolder) for original files', 'export-btn');
    html += bulkIcon('export-structured-folder', 'bi-diagram-3', translations.structuredExportTooltip || translations.structuredExportTitle || translations.structuredExportToolbar || 'Structured Export', 'export-btn');
    html += bulkIcon('export-rename', 'bi-pencil-square', translations.renameOnExport || translations.renameExport || 'Review and rename files before export');
    html += bulkIcon('open-original', 'bi-box-arrow-up-right', translations.openOriginalTitle || translations.openOriginal || 'Open the original file in its native application');
    html += bulkIcon('locate', 'bi-folder2-open', translations.locateContainingFolder || translations.locateFolder || translations.locate || 'Locate the containing folder');
    html += bulkIcon('export-names-csv', 'bi-filetype-csv', translations.exportNamesCsvTitle || translations.exportNamesCsv || translations.namesCsv || 'Export selected filenames to CSV');
    html += bulkIcon('export-names-excel', 'bi-file-earmark-excel', translations.exportNamesExcelTitle || translations.exportNamesExcel || translations.namesExcel || 'Export selected filenames to Excel');
    html += bulkIcon('export-details', 'bi-table', translations.exportDetailsTitle || translations.exportDetails || 'Export names and displayed details with column choice');
    html += bulkIcon('export-loaded-names', 'bi-collection', translations.exportLoadedNames || translations.loadedNames || 'Export names of documents loaded on this page');
    html += `<button type="button" class="action-btn fmas-report-btn action-btn-icon-only" data-on-click="openAnalyticalReportForSelection?.()" ${tipAttrs(translations.analyticalReportTitle || translations.analyticalReport || 'Analytical report for selection')}>`;
    html += `<i class="bi bi-file-earmark-richtext" aria-hidden="true"></i>${hiddenLabel(translations.analyticalReport || 'Analytical Report')}</button>`;
    html += '</div>';
    html += '</div>'; // secondary row

    html += '</div>'; // toolbar
    return html;
}

/** Keep the list's counter and its one Load More control in step with the state. */
function updateShowMoreVisibility() {
    const translations = pageTranslations();
    const pag = navigationState.filePagination || {};
    const shown = (navigationState.loadedFiles || []).length
        || document.querySelectorAll('#fmasFilesContainer .file-card, #fmasFilesContainer .file-grid-item').length;
    const meta = document.getElementById('fmasFilesMeta');
    if (meta && pag.total != null) {
        meta.textContent = `${shown} / ${pag.total} ${translations.files || 'files'}`;
    }
    if (window.UnifiedTable && typeof window.UnifiedTable.syncLoadMore === 'function') {
        window.UnifiedTable.syncLoadMore('fmasFilesContainer', shown, pag.total || 0, pag.perPage);
    }
}

function refreshTypeFilterOptions() {
    const select = document.getElementById('fileTypeFilter');
    if (!select) return;
    const current = selectedTypeValues(select);
    const options = collectFileTypeOptions(navigationState.loadedFiles || [], current);
    select.innerHTML = options.map((t) =>
        `<option value="${escapeAttribute(t)}">${escapeHtml(t)}</option>`).join('');
    setSelectedTypes(select, current);
    enhanceTypeFilter(select);
}

function enhanceTypeFilter(select = document.getElementById('fileTypeFilter')) {
    if (!select) return;
    const translations = pageTranslations();
    enhanceMultiSelect(select, {
        placeholder: translations.allTypes || 'All types',
        compact: true,
        iconForOption: (value) => iconForFileType(value),
    });
    refreshMultiSelect(select);
}

function wireFilesToolbar() {
    const search = document.getElementById('fileSearchInput');
    if (search && !search.dataset.wired) {
        search.dataset.wired = '1';
        search.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                if (typeof window.applyFileListControls === 'function') window.applyFileListControls();
            }
        });
        search.addEventListener('input', () => {
            if (typeof window.filterDisplayedFiles === 'function') {
                window.filterDisplayedFiles(search.value);
            }
        });
    }
    const typeEl = document.getElementById('fileTypeFilter');
    if (typeEl) {
        enhanceTypeFilter(typeEl);
        if (!typeEl.dataset.wired) {
            typeEl.dataset.wired = '1';
            typeEl.addEventListener('change', () => {
                if (typeof window.applyFileListControls === 'function') window.applyFileListControls();
            });
        }
    }
    const sortEl = document.getElementById('fileSortBy');
    if (sortEl && !sortEl.dataset.wired) {
        sortEl.dataset.wired = '1';
        sortEl.addEventListener('change', () => {
            if (typeof window.applyFileListControls === 'function') window.applyFileListControls();
        });
    }
    initFmasActionTooltips();
}


