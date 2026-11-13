/**
 * Global Search Module
 * Handles full-text search, sorting, filters, history, and saved searches
 * Migrated from enhanced-search.js to use new architecture
 */

import { apiGet, apiPost, apiDelete } from '../api/api-client.js';
import { endpoints } from '../api/endpoints.js';
import { escapeHtml, escapeAttribute, getCSRFToken } from '../core/utils.js';
import notificationSystem from '../ui/notifications.js';
import advancedSearch from './advanced-search.js';
import {
    chooseExportDestination,
    ensureExportExtension,
    saveExportBlob,
} from '../core/export-download.js';

// State management
const searchState = {
    currentQuery: '',
    currentFilters: {},
    currentScope: 'uncategorized',
    currentSort: { by: 'relevance', order: 'desc' },
    currentOptions: {
        use_advanced: true, use_bm25: true, use_expansion: true, use_fuzzy: true,
        case_sensitive: false, whole_word: false
    },
    lastDefinition: null,
    currentPage: 1,
    perPage: 10,
    results: [],
    totalResults: 0,
    totalPages: 0,
    loading: false
};

// OPTIMIZATION: Request cancellation for concurrent searches
let currentSearchController = null;
let previewRenderSequence = 0;

let pageTranslationCache;

function searchText(key, fallback = key) {
    if (pageTranslationCache === undefined) {
        const translationElement = document.getElementById('searchEnhancedTranslations');
        try {
            pageTranslationCache = translationElement ? JSON.parse(translationElement.textContent || '{}') : {};
        } catch {
            pageTranslationCache = {};
        }
    }
    const inlineTranslation = pageTranslationCache?.[key];
    if (typeof inlineTranslation === 'string' && inlineTranslation) return inlineTranslation;
    const translated = window.appTranslations?.[key]
        || (typeof window.t === 'function' ? window.t(key) : '');
    return typeof translated === 'string' && translated !== key ? translated : fallback;
}

/**
 * Initialize search interface
 */
export function initializeSearch() {
    const searchForm = document.getElementById('enhancedSearchForm');
    const searchInput = document.getElementById('searchQuery');
    const sortSelect = document.getElementById('sortBy');
    const sortOrderSelect = document.getElementById('sortOrder');
    const saveSearchBtn = document.getElementById('saveSearchBtn');

    if (searchForm) {
        searchForm.addEventListener('submit', handleSearchSubmit);
    }

    if (searchInput) {
        const params = new URLSearchParams(window.location.search);
        const urlQuery = (params.get('q') || params.get('query') || '').trim();
        const urlScope = (params.get('scope') || params.get('analyst_scope') || '').trim();
        if (urlQuery && !searchInput.value.trim()) {
            searchInput.value = urlQuery;
        }
        if (urlScope) {
            const scopeEl = document.getElementById('searchScope')
                || document.querySelector('select[name="scope"], select[name="analyst_scope"]');
            if (scopeEl) scopeEl.value = urlScope;
            searchState.currentScope = urlScope;
        }
        if ((searchInput.value || '').trim().length >= 2) {
            setTimeout(() => performSearch(), 0);
        }
        // OPTIMIZED: Debounced search with request cancellation
        let searchTimeout;
        searchInput.addEventListener('input', function() {
            searchState.currentPage = 1;
            // Cancel any pending search
            if (currentSearchController) {
                currentSearchController.abort();
                currentSearchController = null;
            }
            
            clearTimeout(searchTimeout);
            
            // Clear results immediately if query is too short
            if (this.value.trim().length < 2) {
                searchState.loading = false;
                clearResults();
                return;
            }
            
            searchTimeout = setTimeout(() => {
                if (this.value.trim().length >= 2) {
                    performSearch();
                }
            }, 300);
        });
    }

    if (sortSelect) {
        sortSelect.addEventListener('change', function() {
            searchState.currentSort.by = this.value;
            searchState.currentPage = 1;
            performSearch();
        });
    }

    if (sortOrderSelect) {
        sortOrderSelect.addEventListener('change', function() {
            searchState.currentSort.order = this.value;
            searchState.currentPage = 1;
            performSearch();
        });
    }

    if (saveSearchBtn) {
        saveSearchBtn.addEventListener('click', showSaveSearchModal);
    }

    const paginationContainer = document.getElementById('searchPagination');
    if (paginationContainer && paginationContainer.dataset.actionsBound !== 'true') {
        paginationContainer.dataset.actionsBound = 'true';
        paginationContainer.addEventListener('click', (event) => {
            const pageButton = event.target.closest('[data-search-page]');
            if (!pageButton || pageButton.disabled || !paginationContainer.contains(pageButton)) return;
            const page = Number(pageButton.dataset.searchPage);
            if (Number.isSafeInteger(page) && page > 0) goToPage(page);
        });
    }

    const resultsContainer = document.getElementById('searchResults');
    if (resultsContainer && resultsContainer.dataset.actionsBound !== 'true') {
        resultsContainer.dataset.actionsBound = 'true';
        resultsContainer.addEventListener('click', (event) => {
            const exportButton = event.target.closest('[data-search-export-format]');
            if (exportButton && resultsContainer.contains(exportButton)) {
                exportResults(exportButton.dataset.searchExportFormat, exportButton);
                return;
            }
            const previewButton = event.target.closest('[data-file-preview-id]');
            if (previewButton && resultsContainer.contains(previewButton)) {
                const fileId = Number(previewButton.dataset.filePreviewId);
                if (Number.isSafeInteger(fileId) && fileId > 0) previewFile(fileId);
            }
        });
    }

    // Filter and analyst-scope changes start a fresh result set.
    setupFilterHandlers();
    document.getElementById('analystScopeSelect')?.addEventListener('change', () => {
        searchState.currentPage = 1;
        performSearch();
    });
}

/**
 * Setup filter change handlers
 */
function setupFilterHandlers() {
    const filterInputs = document.querySelectorAll('.search-filter');
    filterInputs.forEach(input => {
        input.addEventListener('change', function() {
            searchState.currentPage = 1;
            updateFilters();
            performSearch();
        });
    });
}

/**
 * Update filters from form
 */
function updateFilters() {
    const typeEl = document.getElementById('filterFileType');
    const fileTypes = typeEl?.multiple
        ? Array.from(typeEl.selectedOptions).map((o) => o.value).filter(Boolean)
        : (typeEl?.value ? [typeEl.value] : []);
    searchState.currentFilters = {
        file_type: fileTypes.length ? fileTypes.join(',') : '',
        source_id: document.getElementById('filterSourceId')?.value || '',
        side_id: document.getElementById('filterSideId')?.value || '',
        date_from: document.getElementById('filterDateFrom')?.value || '',
        date_to: document.getElementById('filterDateTo')?.value || '',
        category_id: document.getElementById('filterCategoryId')?.value || ''
    };
}

/**
 * Handle search form submission
 */
function handleSearchSubmit(e) {
    e.preventDefault();
    searchState.currentPage = 1;
    performSearch();
}

/**
 * Perform search using enhanced API
 */
export async function performSearch() {
    const searchInput = document.getElementById('searchQuery');
    if (!searchInput) return;

    const query = searchInput.value.trim();
    if (!query || query.length < 2) {
        currentSearchController?.abort();
        currentSearchController = null;
        searchState.loading = false;
        clearResults();
        return;
    }

    searchState.currentQuery = query;
    updateFilters();
    currentSearchController?.abort();
    const requestController = new AbortController();
    currentSearchController = requestController;
    searchState.loading = true;
    showLoading();

    try {
        const advancedOptions = advancedSearch.getAdvancedSearchOptions();
        const params = {
            query,
            page: searchState.currentPage,
            per_page: searchState.perPage,
            sort_by: searchState.currentSort.by,
            sort_order: searchState.currentSort.order,
            use_fulltext: 'true',
            use_advanced: advancedOptions.use_advanced ? 'true' : 'false',
            use_bm25: 'false',
            fast_mode: 'true',
            include_snippets: 'false',
            use_expansion: advancedOptions.use_expansion ? 'true' : 'false',
            use_fuzzy: advancedOptions.use_fuzzy ? 'true' : 'false',
            case_sensitive: advancedOptions.case_sensitive ? 'true' : 'false',
            whole_word: advancedOptions.whole_word ? 'true' : 'false',
            ...Object.fromEntries(
                Object.entries(searchState.currentFilters).filter(([, value]) => value !== '')
            )
        };

        // The server persists this explicit analyst-categorization scope.
        const scopeSelect = document.getElementById('analystScopeSelect');
        if (scopeSelect?.value) searchState.currentScope = scopeSelect.value;
        params.scope = searchState.currentScope;
        searchState.currentOptions = { ...advancedOptions };
        const requestedDefinition = {
            query,
            scope: searchState.currentScope,
            filters: { ...searchState.currentFilters },
            sort: { ...searchState.currentSort },
            options: { ...advancedOptions },
            page: searchState.currentPage,
            per_page: searchState.perPage
        };

        const responseData = await apiGet(endpoints.search(params), {}, { signal: requestController.signal });
        if (requestController.signal.aborted || currentSearchController !== requestController) return;
        const data = responseData && typeof responseData === 'object' ? responseData : {};

        const rawResults = Array.isArray(data.results) ? data.results : [];
        const results = advancedSearch.enhanceResultsDisplay(rawResults);
        const rawPagination = data.pagination && typeof data.pagination === 'object'
            ? data.pagination
            : {};
        const total = Number(rawPagination.total);
        const totalPages = Number(rawPagination.total_pages);
        const page = Number(rawPagination.page);
        const pagination = {
            ...rawPagination,
            total: Number.isSafeInteger(total) && total >= 0 ? total : 0,
            total_pages: Number.isSafeInteger(totalPages) && totalPages >= 0 ? totalPages : 0,
            page: Number.isSafeInteger(page) && page > 0 ? page : searchState.currentPage,
            has_prev: Boolean(rawPagination.has_prev),
            has_next: Boolean(rawPagination.has_next)
        };

        searchState.results = results;
        searchState.totalResults = pagination.total;
        searchState.totalPages = pagination.total_pages;
        searchState.lastDefinition = requestedDefinition;
        displayResults({ ...data, pagination, results });
        updatePagination(pagination);
    } catch (error) {
        if (error.name === 'AbortError') return;
        console.error('Search error:', error);
        const message = searchText('Error performing search', 'Error performing search');
        const resultsContainer = document.getElementById('searchResults');
        if (resultsContainer) {
            resultsContainer.innerHTML = `<div class="alert alert-danger" role="alert">${escapeHtml(message)}</div>`;
        }
        notificationSystem.error(message);
    } finally {
        // An older aborted request must not clear a newer request's loading
        // state or cancellation handle.
        if (currentSearchController === requestController) {
            currentSearchController = null;
            searchState.loading = false;
            hideLoading();
        }
    }
}

/** Detail-page URL carrying the current search so the term is located
 * when a file opens (?q= alias on the file routes). */
function fileDetailHref(fileId) {
    const id = Number(fileId);
    if (!Number.isSafeInteger(id) || id < 1) return '/';
    const queryString = searchState.currentQuery
        ? `?q=${encodeURIComponent(searchState.currentQuery)}`
        : '';
    return `/file/${id}${queryString}`;
}

function safeCount(value) {
    const count = Number(value);
    return Number.isSafeInteger(count) && count >= 0 ? count : 0;
}

/**
 * Display search results
 */
function displayResults(data) {
    const resultsContainer = document.getElementById('searchResults');
    if (!resultsContainer) return;
    resultsContainer.setAttribute('aria-busy', 'false');

    const inputResults = Array.isArray(data?.results) ? data.results : [];
    const resultsByType = Object.create(null);
    inputResults.forEach((result) => {
        if (!result || typeof result !== 'object') return;
        const resultType = String(result.result_type || 'file').trim().toLowerCase().slice(0, 64);
        const entityId = resultType === 'title' ? (result.path_id ?? result.id) : result.id;
        const resultId = Number(entityId);
        if (!Number.isSafeInteger(resultId) || resultId < 1) return;
        if (!resultsByType[resultType]) resultsByType[resultType] = [];
        resultsByType[resultType].push(result);
    });

    const noResults = `
        <div class="text-center text-muted py-5">
            <i class="bi bi-search display-4 d-block mb-3" aria-hidden="true"></i>
            <p>${escapeHtml(searchText('No results found for', 'No results found for'))} "${escapeHtml(searchState.currentQuery)}"</p>
        </div>
    `;
    if (Object.keys(resultsByType).length === 0) {
        resultsContainer.innerHTML = noResults;
        return;
    }

    const typeLabels = {
        file: { icon: 'bi-file-earmark', label: searchText('Files', 'Files'), color: 'primary' },
        category: { icon: 'bi-tags', label: searchText('Categories', 'Categories'), color: 'success' },
        keyword: { icon: 'bi-key', label: searchText('Keywords', 'Keywords'), color: 'warning' },
        source: { icon: 'bi-building', label: searchText('Sources', 'Sources'), color: 'info' },
        side: { icon: 'bi-diagram-3', label: searchText('Sides', 'Sides'), color: 'secondary' },
        word: { icon: 'bi-text-paragraph', label: searchText('Words', 'Words'), color: 'dark' },
        title: { icon: 'bi-heading', label: searchText('Titles', 'Titles'), color: 'primary' }
    };
    const filesLabel = searchText('files', 'files');
    const relevanceLabel = searchText('Relevance', 'Relevance');
    const previewLabel = searchText('Preview', 'Preview');
    const notAvailable = searchText('N/A', 'N/A');
    const unknownLabel = searchText('Unknown', 'Unknown');

    const workspaceParams = new URLSearchParams();
    if (searchState.currentQuery) workspaceParams.set('q', searchState.currentQuery);
    if (searchState.currentScope && searchState.currentScope !== 'uncategorized') {
        workspaceParams.set('scope', searchState.currentScope);
    }
    const workspaceHref = `/search/advanced${workspaceParams.toString() ? `?${workspaceParams.toString()}` : ''}`;

    let html = `
        <div class="d-flex justify-content-between align-items-center mb-3 flex-wrap gap-2">
            <div><strong>${safeCount(data?.pagination?.total)}</strong> ${escapeHtml(searchText('results found', 'results found'))}</div>
            <div class="d-flex flex-wrap gap-2 align-items-center">
                <a class="btn btn-sm btn-outline-secondary" href="${escapeAttribute(workspaceHref)}"
                   target="_blank" rel="noopener" data-open-search-workspace="true">
                    <i class="bi bi-layout-split me-1" aria-hidden="true"></i>
                    ${escapeHtml(searchText('Open Search & Review workspace', 'Open Search & Review workspace'))}
                </a>
                <div class="btn-group" role="group" aria-label="${escapeAttribute(searchText('Export', 'Export'))}">
                <button type="button" class="btn btn-sm btn-outline-primary" data-search-export-format="csv" aria-label="CSV">
                    <i class="bi bi-file-earmark-spreadsheet me-1" aria-hidden="true"></i>CSV
                </button>
                <button type="button" class="btn btn-sm btn-outline-primary" data-search-export-format="excel" aria-label="Excel">
                    <i class="bi bi-file-earmark-excel me-1" aria-hidden="true"></i>Excel
                </button>
                <button type="button" class="btn btn-sm btn-outline-primary" data-search-export-format="json" aria-label="JSON">
                    <i class="bi bi-file-earmark-code me-1" aria-hidden="true"></i>JSON
                </button>
                </div>
            </div>
        </div>
    `;

    Object.keys(resultsByType).forEach((resultType) => {
        const typeInfo = Object.prototype.hasOwnProperty.call(typeLabels, resultType)
            ? typeLabels[resultType]
            : { icon: 'bi-circle', label: resultType, color: 'secondary' };
        const typeResults = resultsByType[resultType];
        html += `
            <section class="mb-4" aria-label="${escapeAttribute(typeInfo.label)}">
                <h5 class="mb-3">
                    <i class="bi ${typeInfo.icon} me-2" aria-hidden="true"></i>
                    ${escapeHtml(typeInfo.label)} (${typeResults.length})
                </h5>
        `;

        typeResults.forEach((result) => {
            const entityId = resultType === 'title' ? (result.path_id ?? result.id) : result.id;
            const resultId = Number(entityId);
            const resultName = String(result.name || result.file_name || unknownLabel);
            const relevanceScore = Number(result.relevance_score);
            const relevance = Number(result.relevance);
            let resultLink = null;
            let resultDetails = '';

            switch (resultType) {
                case 'file': {
                    resultLink = fileDetailHref(resultId);
                    const formatted = advancedSearch.formatSearchResult(result, searchState.currentQuery);
                    if (formatted) {
                        html += formatted;
                        return;
                    }
                    resultDetails = `
                        <small class="text-muted d-block mb-1">
                            <span class="me-3"><i class="bi bi-building me-1" aria-hidden="true"></i>${escapeHtml(result.source_name || unknownLabel)}</span>
                            <span class="me-3"><i class="bi bi-diagram-3 me-1" aria-hidden="true"></i>${escapeHtml(result.side_name || unknownLabel)}</span>
                            <span class="me-3"><i class="bi bi-calendar me-1" aria-hidden="true"></i>${escapeHtml(result.date || result.file_date || notAvailable)}</span>
                            ${result.relevance_score != null && Number.isFinite(relevanceScore) ? `<span class="badge bg-info ms-2" title="${escapeAttribute(relevanceLabel)}">${relevanceScore.toFixed(2)}</span>` : ''}
                        </small>
                        <div class="d-flex gap-2 mt-2 align-items-center">
                            <button type="button" class="btn btn-sm btn-outline-secondary"
                                    data-file-preview-id="${resultId}"
                                    aria-label="${escapeAttribute(`${previewLabel}: ${resultName}`)}">
                                <i class="bi bi-eye me-1" aria-hidden="true"></i>${escapeHtml(previewLabel)}
                            </button>
                            <span class="badge bg-secondary">${escapeHtml(result.type || result.file_type || unknownLabel)}</span>
                        </div>
                    `;
                    break;
                }
                case 'category':
                    resultLink = `/category/${resultId}`;
                    resultDetails = `<small class="text-muted d-block mb-1"><i class="bi bi-file-earmark me-1" aria-hidden="true"></i>${safeCount(result.file_count)} ${escapeHtml(filesLabel)}</small>`;
                    break;
                case 'keyword':
                    resultLink = `/keywords/${resultId}`;
                    resultDetails = `<small class="text-muted d-block mb-1"><i class="bi bi-file-earmark me-1" aria-hidden="true"></i>${safeCount(result.usage_count)} ${escapeHtml(filesLabel)}</small>`;
                    break;
                case 'source':
                    resultLink = `/source/${resultId}`;
                    resultDetails = `
                        <small class="text-muted d-block mb-1">
                            <span class="me-3"><i class="bi bi-briefcase me-1" aria-hidden="true"></i>${escapeHtml(result.job || notAvailable)}</span>
                            <span class="me-3"><i class="bi bi-geo-alt me-1" aria-hidden="true"></i>${escapeHtml(result.country || notAvailable)}</span>
                        </small>
                    `;
                    break;
                case 'side':
                    resultLink = `/side/${resultId}`;
                    resultDetails = `
                        <small class="text-muted d-block mb-1">
                            <span class="me-3"><i class="bi bi-calendar me-1" aria-hidden="true"></i>${escapeHtml(result.date_creation || notAvailable)}</span>
                        </small>
                    `;
                    break;
                case 'word':
                    resultLink = `/word/${resultId}`;
                    resultDetails = `<small class="text-muted d-block mb-1"><i class="bi bi-file-earmark me-1" aria-hidden="true"></i>${safeCount(result.file_count)} ${escapeHtml(filesLabel)}</small>`;
                    break;
                case 'title':
                    resultLink = fileDetailHref(resultId);
                    resultDetails = `
                        <small class="text-muted d-block mb-1">
                            ${result.file_name ? `<span class="me-3"><i class="bi bi-file-earmark me-1" aria-hidden="true"></i>${escapeHtml(result.file_name)}</span>` : ''}
                            <span class="badge bg-secondary">${escapeHtml(result.status || searchText('Main', 'Main'))}</span>
                        </small>
                    `;
                    break;
                default:
                    break;
            }

            const safeName = escapeHtml(resultName);
            const titleMarkup = resultLink
                ? `<a href="${escapeAttribute(resultLink)}" class="text-decoration-none">${safeName}</a>`
                : `<span>${safeName}</span>`;
            html += `
                <div class="list-group-item list-group-item-action">
                    <div class="d-flex justify-content-between align-items-start">
                        <div class="flex-grow-1">
                            <h6 class="mb-1">
                                <i class="bi ${typeInfo.icon} me-2" aria-hidden="true"></i>
                                ${titleMarkup}
                                <span class="badge bg-${typeInfo.color} ms-2">${escapeHtml(typeInfo.label)}</span>
                            </h6>
                            ${resultDetails}
                            ${result.relevance != null && Number.isFinite(relevance) ? `<span class="badge bg-info mt-2">${escapeHtml(relevanceLabel)}: ${relevance.toFixed(2)}</span>` : ''}
                        </div>
                    </div>
                </div>
            `;
        });

        html += '</section>';
    });

    resultsContainer.innerHTML = html;
}

/**
 * Update pagination UI
 */
function updatePagination(pagination) {
    const paginationContainer = document.getElementById('searchPagination');
    if (!paginationContainer || !pagination) return;

    const totalPages = Number(pagination.total_pages);
    const requestedPage = Number(pagination.page);
    if (!Number.isSafeInteger(totalPages) || totalPages <= 1) {
        paginationContainer.replaceChildren();
        return;
    }
    const currentPage = Math.min(Math.max(Number.isSafeInteger(requestedPage) ? requestedPage : 1, 1), totalPages);

    import('../../modules/rendering/unified-pagination.js').then(module => {
        module.renderUnifiedPagination({
            currentPage,
            totalPages,
            containerId: 'searchPagination',
            onPageChange: goToPage,
            urlParams: {},
            showInfo: true,
            showJump: totalPages > 5,
            baseUrl: window.location.pathname
        });
    }).catch(error => {
        console.error('Error loading unified pagination:', error);
        const pageNumbers = new Set([1, totalPages]);
        for (let page = Math.max(1, currentPage - 2); page <= Math.min(totalPages, currentPage + 2); page += 1) {
            pageNumbers.add(page);
        }
        const pages = [...pageNumbers].sort((a, b) => a - b);
        const previousLabel = searchText('Previous', 'Previous');
        const nextLabel = searchText('Next', 'Next');
        let html = `<nav aria-label="${escapeAttribute(searchText('Pagination', 'Pagination'))}"><ul class="pagination justify-content-center">`;
        html += `<li class="page-item"><button type="button" class="page-link" data-search-page="${currentPage - 1}" ${currentPage <= 1 ? 'disabled aria-disabled="true"' : ''}>${escapeHtml(previousLabel)}</button></li>`;
        let lastPage = 0;
        pages.forEach((page) => {
            if (lastPage && page - lastPage > 1) {
                html += '<li class="page-item disabled" aria-hidden="true"><span class="page-link">…</span></li>';
            }
            const current = page === currentPage;
            html += `<li class="page-item${current ? ' active' : ''}"><button type="button" class="page-link" data-search-page="${page}" ${current ? 'aria-current="page"' : ''}>${page}</button></li>`;
            lastPage = page;
        });
        html += `<li class="page-item"><button type="button" class="page-link" data-search-page="${currentPage + 1}" ${currentPage >= totalPages ? 'disabled aria-disabled="true"' : ''}>${escapeHtml(nextLabel)}</button></li>`;
        html += '</ul></nav>';
        paginationContainer.innerHTML = html;
    });
}

/**
 * Go to specific page
 */
export function goToPage(page) {
    const targetPage = Number(page);
    if (!Number.isSafeInteger(targetPage) || targetPage < 1) return;
    if (searchState.totalPages > 0 && targetPage > searchState.totalPages) return;
    searchState.currentPage = targetPage;
    performSearch();
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

/**
 * Export search results
 */
export async function exportResults(format, triggerButton = null) {
    if (!['csv', 'excel', 'json'].includes(format)) return;
    if (searchState.loading) return;
    const definition = searchState.lastDefinition;
    if (!definition || !searchState.results || searchState.results.length === 0) {
        notificationSystem.warning(searchText('No results to export', 'No results to export'));
        return;
    }

    const filters = definition.filters || {};
    const options = definition.options || {};
    const sort = definition.sort || { by: 'relevance', order: 'desc' };
    const extension = format === 'excel' ? 'xlsx' : format;
    const requestedFilename = await window.prompt(
        searchText('filenameExportPrompt', 'Name this export (leave blank for an automatic name):'),
        `search_results_${new Date().toISOString().slice(0, 10)}`);
    if (requestedFilename === null) return;
    const suggestedName = ensureExportExtension(
        requestedFilename.trim() || `search_results_${new Date().toISOString().slice(0, 10)}`,
        extension,
        'search_results');
    let destination = null;
    try {
        destination = await chooseExportDestination(suggestedName);
        if (destination === false) return;
    } catch (error) {
        console.warn('Save location picker unavailable:', error);
    }
    const asList = value => value == null || value === '' ? [] : (Array.isArray(value) ? value : [value]);
    const payload = {
        query: definition.query,
        export_scope: 'filtered',
        analyst_scope: definition.scope || 'uncategorized',
        format,
        filename: requestedFilename.trim() || `search_results_${new Date().toISOString().split('T')[0]}`,
        page: definition.page || 1,
        per_page: definition.per_page || searchState.perPage,
        file_type: asList(filters.file_type),
        source_ids: asList(filters.source_id),
        side_ids: asList(filters.side_id),
        category_ids: asList(filters.category_id),
        sort_by: sort.by || 'relevance',
        sort_order: sort.order || 'desc',
        use_advanced: options.use_advanced !== false,
        use_fulltext: true,
        use_bm25: options.use_bm25 !== false,
        use_expansion: options.use_expansion !== false,
        use_fuzzy: options.use_fuzzy !== false,
        case_sensitive: options.case_sensitive === true,
        whole_word: options.whole_word === true
    };
    const exportButtons = Array.from(document.querySelectorAll('[data-search-export-format]'));
    exportButtons.forEach(button => {
        button.disabled = true;
        button.setAttribute('aria-busy', 'true');
    });
    if (triggerButton && !exportButtons.includes(triggerButton)) {
        triggerButton.disabled = true;
        triggerButton.setAttribute('aria-busy', 'true');
    }

    try {
        let csrfToken = getCSRFToken();
        if (!csrfToken) {
            const { getCSRFTokenAsync } = await import('../core/utils.js');
            csrfToken = await getCSRFTokenAsync();
        }
        const response = await fetch(endpoints.searchExport(), {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken
            },
            body: JSON.stringify(payload)
        });

        if (!response.ok) {
            const problem = await response.json().catch(() => ({}));
            throw new Error(problem.error || searchText('Error exporting results', 'Error exporting results'));
        }

        const blob = await response.blob();
        const extension = format === 'excel' ? 'xlsx' : format;
        let filename = `search_results.${extension}`;
        const disposition = response.headers.get('Content-Disposition') || '';
        const match = disposition.match(/filename\*=UTF-8''([^;]+)|filename="?([^";]+)"?/i);
        if (match) {
            const rawFilename = match[1] || match[2];
            try { filename = decodeURIComponent(rawFilename); } catch (_) { filename = rawFilename; }
        }

        await saveExportBlob(blob, filename, destination);
        notificationSystem.success(searchText('Export ready', 'Export ready'));
    } catch (error) {
        console.error('Export error:', error);
        notificationSystem.error(error.message || searchText('Error exporting results', 'Error exporting results'));
    } finally {
        exportButtons.forEach(button => {
            if (!button.isConnected) return;
            button.disabled = false;
            button.removeAttribute('aria-busy');
        });
        if (triggerButton?.isConnected) {
            triggerButton.disabled = false;
            triggerButton.removeAttribute('aria-busy');
        }
    }
}

/**
 * Preview file
 */
export async function previewFile(fileId) {
    const id = Number(fileId);
    if (!Number.isSafeInteger(id) || id < 1) return;

    try {
        const data = await apiGet(endpoints.filePreview(id, 1200, 800));
        if (!data || typeof data !== 'object') throw new Error('Invalid preview response');
        if (data.preview_type === 'error') {
            notificationSystem.error(searchText('Error loading preview', 'Error loading preview'));
            return;
        }
        data.file_id = id;
        showPreviewModal(data);
    } catch (error) {
        console.error('Preview error:', error);
        notificationSystem.error(searchText('Error loading preview', 'Error loading preview'));
    }
}

/** Show the preview modal and render one response generation at a time. */
function showPreviewModal(previewData) {
    let modal = document.getElementById('filePreviewModal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'filePreviewModal';
        modal.className = 'modal fade';
        modal.tabIndex = -1;
        modal.setAttribute('aria-labelledby', 'filePreviewModalLabel');
        modal.setAttribute('aria-hidden', 'true');

        const dialog = document.createElement('div');
        dialog.className = 'modal-dialog modal-lg';
        const content = document.createElement('div');
        content.className = 'modal-content';
        const header = document.createElement('div');
        header.className = 'modal-header';
        const title = document.createElement('h5');
        title.className = 'modal-title';
        title.id = 'filePreviewModalLabel';
        title.textContent = searchText('File Preview', 'File Preview');
        const closeButton = document.createElement('button');
        closeButton.type = 'button';
        closeButton.className = 'btn-close';
        closeButton.setAttribute('data-bs-dismiss', 'modal');
        closeButton.setAttribute('aria-label', searchText('Close', 'Close'));
        header.append(title, closeButton);
        const body = document.createElement('div');
        body.className = 'modal-body';
        body.id = 'previewModalBody';
        content.append(header, body);
        dialog.append(content);
        modal.append(dialog);
        document.body.append(modal);
    }

    const body = modal.querySelector('#previewModalBody');
    if (!body) return;
    const renderSequence = ++previewRenderSequence;
    renderPreviewBody(body, previewData, renderSequence);

    if (typeof bootstrap !== 'undefined' && bootstrap.Modal) {
        const bsModal = typeof bootstrap.Modal.getOrCreateInstance === 'function'
            ? bootstrap.Modal.getOrCreateInstance(modal)
            : new bootstrap.Modal(modal);
        bsModal.show();
    } else {
        modal.style.display = 'block';
        modal.classList.add('show');
        document.body.classList.add('modal-open');
    }
}

/**
 * Render preview content using text-only DOM APIs or the shared formatter.
 * API-provided image data is restricted to raster base64 URLs; text and error
 * messages are always assigned through textContent/escaped formatter output.
 */
async function renderPreviewBody(body, previewData, renderSequence) {
    if (!body || !previewData || typeof previewData !== 'object') return;

    if (previewData.preview_type === 'image') {
        const fileId = Number(previewData.file_id);
        if (!Number.isSafeInteger(fileId) || fileId < 1) {
            const message = document.createElement('p');
            message.className = 'text-muted';
            message.textContent = searchText('Error loading preview', 'Error loading preview');
            body.replaceChildren(message);
            return;
        }
        const image = document.createElement('img');
        image.src = `/api/preview/${fileId}/image`;
        image.className = 'img-fluid';
        image.alt = searchText('File Preview', 'File Preview');
        body.replaceChildren(image);
        return;
    }

    if (['pdf', 'document', 'text'].includes(previewData.preview_type)) {
        let text;
        if (previewData.preview_type === 'document' && Array.isArray(previewData.data)) {
            text = previewData.data
                .map(row => (Array.isArray(row) ? row : []).map(cell => String(cell == null ? '' : cell)).join('\t'))
                .join('\n');
        } else {
            text = typeof previewData.data === 'string'
                ? previewData.data
                : JSON.stringify(previewData.data, null, 2);
        }

        let formatted = '';
        try {
            const formatter = await import('../content-formatter.js');
            const name = String(previewData.file_name || previewData.file_type || '');
            const extension = name.includes('.') ? name.split('.').pop() : (previewData.file_type || 'txt');
            formatted = formatter.formatContentByType(
                text, extension, '', previewData.file_id || null);
        } catch (error) {
            console.warn('Formatted preview unavailable, using plain text:', error);
        }
        if (renderSequence !== previewRenderSequence || !body.isConnected) return;

        if (typeof formatted === 'string' && formatted) {
            ensureFormattedContentStyles();
            body.innerHTML = `<div class="formatted-content-wrapper" style="max-height: 500px; overflow-y: auto;">${formatted}</div>`;
        } else {
            const pre = document.createElement('pre');
            pre.className = 'bg-light p-3';
            pre.style.maxHeight = '500px';
            pre.style.overflowY = 'auto';
            pre.textContent = text;
            body.replaceChildren(pre);
        }
        return;
    }

    const message = document.createElement('p');
    message.className = 'text-muted';
    message.textContent = String(previewData.message || searchText('Error loading preview', 'Error loading preview'));
    body.replaceChildren(message);
}

/** Load the shared formatted-content stylesheet on demand. */
function ensureFormattedContentStyles() {
    if (document.getElementById('formatted-content-preview-css')) return;
    const link = document.createElement('link');
    link.id = 'formatted-content-preview-css';
    link.rel = 'stylesheet';
    link.href = '/static/css/formatted-content.css';
    document.head.appendChild(link);
}

/**
 * Load search history
 */
export async function loadSearchHistory() {
    try {
        const data = await apiGet(endpoints.searchHistory(10));
        const historyContainer = document.getElementById('searchHistory');
        if (!historyContainer) return;

        if (historyContainer.dataset.actionsBound !== 'true') {
            historyContainer.dataset.actionsBound = 'true';
            historyContainer.addEventListener('click', (event) => {
                const link = event.target.closest('[data-search-history-query]');
                if (!link || !historyContainer.contains(link)) return;
                event.preventDefault();
                loadHistorySearch(link.dataset.searchHistoryQuery || '');
            });
        }

        const history = Array.isArray(data?.history) ? data.history : [];
        if (history.length === 0) {
            const message = document.createElement('p');
            message.className = 'text-muted small';
            message.textContent = historyContainer.dataset.emptyMessage || searchText('No search history', 'No search history');
            historyContainer.replaceChildren(message);
            return;
        }

        const resultsLabel = searchText('results', 'results');
        let html = '<ul class="list-group list-group-flush">';
        history.forEach((item) => {
            if (!item || typeof item !== 'object') return;
            const query = String(item.query == null ? '' : item.query);
            const resultCount = safeCount(item.result_count);
            const parsedTimestamp = item.timestamp ? new Date(item.timestamp) : null;
            const dateLabel = parsedTimestamp && !Number.isNaN(parsedTimestamp.getTime())
                ? parsedTimestamp.toLocaleString()
                : '';
            html += `
                <li class="list-group-item">
                    <div class="d-flex justify-content-between align-items-center gap-2">
                        <div class="min-w-0">
                            <a href="#" data-search-history-query="${escapeAttribute(query)}" class="text-decoration-none text-break">
                                ${escapeHtml(query)}
                            </a>
                            <small class="text-muted d-block">${escapeHtml(dateLabel)}</small>
                        </div>
                        <span class="badge bg-secondary flex-shrink-0">${resultCount} ${escapeHtml(resultsLabel)}</span>
                    </div>
                </li>
            `;
        });
        html += '</ul>';
        historyContainer.innerHTML = html;
    } catch (error) {
        console.error('Error loading search history:', error);
    }
}

/**
 * Load saved searches
 */
export async function loadSavedSearches() {
    try {
        const data = await apiGet(endpoints.searchSaved());
        const savedContainer = document.getElementById('savedSearches');
        if (!savedContainer) return;

        if (savedContainer.dataset.actionsBound !== 'true') {
            savedContainer.dataset.actionsBound = 'true';
            savedContainer.addEventListener('click', (event) => {
                const link = event.target.closest('[data-saved-search-id]');
                if (!link || !savedContainer.contains(link)) return;
                event.preventDefault();
                const id = Number(link.dataset.savedSearchId);
                if (Number.isSafeInteger(id) && id > 0) loadSavedSearch(id);
            });
        }

        const searches = Array.isArray(data?.searches) ? data.searches : [];
        const renderableSearches = searches.filter((search) => {
            const id = Number(search?.id);
            return search && typeof search === 'object' && Number.isSafeInteger(id) && id > 0;
        });
        if (renderableSearches.length === 0) {
            const message = document.createElement('p');
            message.className = 'text-muted small';
            message.textContent = savedContainer.dataset.emptyMessage || searchText('No saved searches', 'No saved searches');
            savedContainer.replaceChildren(message);
            return;
        }

        let html = '<ul class="list-group list-group-flush">';
        renderableSearches.forEach((search) => {
            const id = Number(search.id);
            const name = String(search.name == null ? '' : search.name);
            const query = String(search.query == null ? '' : search.query);
            html += `
                <li class="list-group-item">
                    <div class="d-flex justify-content-between align-items-center">
                        <div class="min-w-0">
                            <a href="#" data-saved-search-id="${id}" class="text-decoration-none fw-bold text-break">
                                ${escapeHtml(name)}
                            </a>
                            <small class="text-muted d-block text-break">${escapeHtml(query)}</small>
                        </div>
                    </div>
                </li>
            `;
        });
        html += '</ul>';
        savedContainer.innerHTML = html;
    } catch (error) {
        console.error('Error loading saved searches:', error);
    }
}

/**
 * Load history search
 */
export function loadHistorySearch(query) {
    const input = document.getElementById('searchQuery');
    if (!input) return;
    input.value = String(query == null ? '' : query);
    searchState.currentPage = 1;
    performSearch();
}

/**
 * Load saved search
 */
export async function loadSavedSearch(searchId) {
    const id = Number(searchId);
    if (!Number.isSafeInteger(id) || id < 1) return;

    try {
        const data = await apiGet(endpoints.searchSavedById(id));
        if (!data?.search || typeof data.search !== 'object') return;
        const searchInput = document.getElementById('searchQuery');
        if (!searchInput) return;
        searchInput.value = String(data.search.query == null ? '' : data.search.query);

        const filterIds = {
            file_type: 'filterFileType',
            source_id: 'filterSourceId',
            side_id: 'filterSideId',
            date_from: 'filterDateFrom',
            date_to: 'filterDateTo',
            category_id: 'filterCategoryId'
        };
        if (data.search.filters && typeof data.search.filters === 'object') {
            Object.entries(filterIds).forEach(([key, elementId]) => {
                const input = document.getElementById(elementId);
                if (!input || !Object.prototype.hasOwnProperty.call(data.search.filters, key)) return;
                const raw = data.search.filters[key];
                if (input.multiple) {
                    const wanted = new Set(
                        (Array.isArray(raw) ? raw : String(raw ?? '').split(','))
                            .map((v) => String(v).trim())
                            .filter(Boolean)
                    );
                    Array.from(input.options).forEach((opt) => {
                        opt.selected = wanted.has(opt.value);
                    });
                } else {
                    input.value = String(raw ?? '');
                }
            });
        }
        searchState.currentPage = 1;
        performSearch();
    } catch (error) {
        console.error('Error loading saved search:', error);
        notificationSystem.error(searchText('Error loading saved search', 'Error loading saved search'));
    }
}

/**
 * Delete saved search
 */
export async function deleteSavedSearch(searchId) {
    const id = Number(searchId);
    if (!Number.isSafeInteger(id) || id < 1) return;
    if (!confirm(searchText('Delete this saved search?', 'Delete this saved search?'))) return;

    try {
        await apiDelete(endpoints.searchSavedById(id));
        loadSavedSearches();
        notificationSystem.success(searchText('Saved search deleted', 'Saved search deleted'));
    } catch (error) {
        console.error('Error deleting saved search:', error);
        notificationSystem.error(searchText('Error deleting saved search', 'Error deleting saved search'));
    }
}

/**
 * Show save search modal
 */
export async function showSaveSearchModal() {
    const query = document.getElementById('searchQuery')?.value.trim();
    if (!query) {
        notificationSystem.warning(searchText('Please enter a search query first', 'Please enter a search query first'));
        return;
    }

    const name = await window.prompt(searchText('Enter a name for this search:', 'Enter a name for this search:'), query);
    if (!name || !name.trim()) return;
    await saveSearch(name.trim(), query);
}

/**
 * Save search
 */
export async function saveSearch(name, query) {
    const safeName = String(name == null ? '' : name).trim();
    const safeQuery = String(query == null ? '' : query).trim();
    if (!safeName || !safeQuery) return;

    try {
        await apiPost(endpoints.searchSaved(), {
            name: safeName,
            query: safeQuery,
            filters: searchState.currentFilters
        });
        loadSavedSearches();
        notificationSystem.success(searchText('Search saved successfully', 'Search saved successfully'));
    } catch (error) {
        console.error('Error saving search:', error);
        notificationSystem.error(searchText('Error saving search', 'Error saving search'));
    }
}

/**
 * Clear results
 */
function clearResults() {
    searchState.results = [];
    searchState.totalResults = 0;
    searchState.totalPages = 0;
    const resultsContainer = document.getElementById('searchResults');
    if (resultsContainer) {
        resultsContainer.replaceChildren();
        resultsContainer.setAttribute('aria-busy', 'false');
    }
    const paginationContainer = document.getElementById('searchPagination');
    if (paginationContainer) {
        paginationContainer.innerHTML = '';
    }
}

/**
 * Show loading indicator
 */
function showLoading() {
    const resultsContainer = document.getElementById('searchResults');
    if (!resultsContainer) return;
    resultsContainer.setAttribute('aria-busy', 'true');
    const loadingText = searchText('Loading...', 'Loading...');
    resultsContainer.innerHTML = `
        <div class="text-center py-5">
            <div class="spinner-border" role="status">
                <span class="visually-hidden">${escapeHtml(loadingText)}</span>
            </div>
        </div>
    `;
}

/** Hide the loading state from assistive technology after a request. */
function hideLoading() {
    document.getElementById('searchResults')?.setAttribute('aria-busy', 'false');
}

// Export API
export default {
    initializeSearch,
    performSearch,
    goToPage,
    exportResults,
    previewFile,
    loadHistorySearch,
    loadSavedSearch,
    deleteSavedSearch,
    saveSearch,
    showSaveSearchModal,
    loadSearchHistory,
    loadSavedSearches
};
