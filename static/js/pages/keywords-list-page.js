/**
 * Keywords List Page JavaScript
 * Extracted from Keyword/keywords_list.html
 */

import { getCSRFToken } from '../modules/core/utils.js';
import { settingsReady } from '../modules/core/system-settings.js';

// Load translations from JSON script tag
let translations = {};

function loadKeywordsListPageData() {
    const pageDataEl = document.getElementById('keywords-list-page-data');
    if (pageDataEl) {
        try {
            const data = JSON.parse(pageDataEl.textContent);
            translations = data.translations || {};
            window.translations = window.translations || {};
            Object.assign(window.translations, translations);
        } catch (e) {
            console.error('Error parsing keywords list page data:', e);
        }
    }
}

   (function() {
        function loadSelect2() {
            if (typeof jQuery !== 'undefined' && typeof jQuery.fn.select2 === 'undefined') {
                const script = document.createElement('script');
                // Get Select2 URL from page data or use default
                const pageDataEl = document.getElementById('keywords-list-page-data');
                let select2Url = '/static/dist/js/select2.min.js';
                if (pageDataEl) {
                    try {
                        const data = JSON.parse(pageDataEl.textContent);
                        select2Url = data.select2_url || '/static/dist/js/select2.min.js';
                    } catch (e) {
                        console.warn('Error parsing page data, using default Select2 URL');
                    }
                }
                script.src = select2Url;
                script.onerror = function() {
                    console.error('Failed to load Select2 library');
                };
                document.head.appendChild(script);
            } else if (typeof jQuery === 'undefined') {
                // jQuery not ready yet, try again
                setTimeout(loadSelect2, 50);
            }
        }
        
        // Start checking when DOM is ready
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', loadSelect2);
        } else {
            loadSelect2();
        }
    })();



// Ensure window.translations exists (file-management-system.js may have created it)
if (typeof window.translations === 'undefined') {
    window.translations = {};
}

(function(){
    // Use translations from window - never reference the global 'translations' variable
    const translations = window.translations;
    const tableBody = document.getElementById('keywordsTableBody');
    const searchInput = document.getElementById('searchKeywords');
    const statusFilter = document.getElementById('statusFilter');
    const categoryFilter = document.getElementById('categoryFilter');
    const perPageSelect = document.getElementById('perPage');
    const selectAllCheckbox = document.getElementById('selectAllCheckbox');
    const TABLE_ID = 'keywordsTable';
    const COLUMN_COUNT = 7;
    
    let currentPage = 1;
    let currentPerPage = 10;
    // Seed from the URL so a server-rendered, sorted view (the sort/order
    // query params the headers wrote) is the state the page starts in.
    const SORTABLE_COLUMNS = ['usage_count', 'text', 'id', 'status'];
    const initialParams = new URLSearchParams(window.location.search);
    const sortFromUrl = initialParams.get('sort');
    const orderFromUrl = initialParams.get('order');
    let currentSort = SORTABLE_COLUMNS.includes(sortFromUrl) ? sortFromUrl : 'usage_count';
    let currentOrder = orderFromUrl === 'asc' ? 'asc' : 'desc';
    let currentFilters = {};
    let selectedKeywords = new Set();
    let categories = [];

    // Initialize
    function init() {
        loadKeywordsListPageData();
        if (window.DocumentsPanel) {
            window.DocumentsPanel.wire({ panelId: 'documentsPanel', tableId: 'keywordFilesTable' });
        }
        // Read page number from URL
        const urlParams = new URLSearchParams(window.location.search);
        const pageFromUrl = parseInt(urlParams.get('page')) || 1;
        currentPage = pageFromUrl;
        
        // ✅ FIXED: Update sort icons to reflect current sort state
        updateSortIcons();

        // Column sort lives in the table headers: the unified table asks the
        // page to re-fetch in the chosen order.
        if (window.UnifiedTable) {
            window.UnifiedTable.onSort(TABLE_ID, function (key, direction) {
                // A third header click clears the sort: the request goes out
                // without sort parameters, the route's default order.
                currentSort = direction == null ? null : key;
                currentOrder = direction == null ? null : direction;
                currentPage = 1;
                fetchPage(1);
            });
        }

        // ✅ FIXED: Create pagination container on init to ensure it's always visible
        createPaginationContainer();
        
        if (perPageSelect) {
            currentPerPage = parseInt(perPageSelect.value) || 10;
        }
        loadCategories();
        setupEventListeners();
        wireLoadMoreInfo();

        // The table is already painted by the server. Waiting on settings
        // and then fetchPage() was wiping those rows with "Loading…" and
        // paying for the same query a second time. Keep the server rows;
        // refetch only when the saved page size disagrees, or the tbody
        // arrived empty.
        const ssrRows = tableBody ? tableBody.querySelectorAll('tr[data-keyword-id]').length : 0;
        if (!ssrRows) {
            fetchPage(pageFromUrl);
        }
        settingsReady.then(() => {
            // Settings rewrite the per-page select to the saved size. Put
            // it back so it matches the rows already on the page; a refetch
            // here was the remaining flash after first paint.
            if (perPageSelect && ssrRows) {
                perPageSelect.value = String(currentPerPage);
            }
        });
    }

    // Show More appends rows through the shared table engine, which reports the
    // visible and total counts. The info line must follow those counts, not
    // stay on the last fetched range.
    let loadMoreInfoWired = false;
    function wireLoadMoreInfo() {
        if (loadMoreInfoWired) return;
        loadMoreInfoWired = true;
        document.addEventListener('ut:loadmore', (event) => {
            const detail = event.detail || {};
            if (detail.table !== TABLE_ID) return;
            const infoEl = document.getElementById('searchResultsInfo');
            if (!infoEl) return;
            const shown = Math.max(0, detail.shown || 0);
            const total = Math.max(0, detail.total || 0);
            const first = Math.max(1, detail.first || 1);
            const last = first + shown - 1;
            const query = searchInput.value.trim();
            if (shown === 0) {
                infoEl.textContent = query
                    ? `Found ${total} result${total !== 1 ? 's' : ''} for "${query}"`
                    : `${translations.showing} 0 ${translations.of} ${total} ${translations.keywords}`;
                return;
            }
            infoEl.textContent = query
                ? `Found ${total} result${total !== 1 ? 's' : ''} for "${query}" (${first}–${last})`
                : `${translations.showing} ${first}–${last} ${translations.of} ${total} ${translations.keywords}`;
        });
    }
    
    // Update sort icons to reflect current sort state
    function updateSortIcons() {
        // The header is the one sort control; the unified table owns how the
        // decision is drawn (indicator, aria-sort).
        if (window.UnifiedTable) {
            window.UnifiedTable.setSort(TABLE_ID, currentSort, currentOrder);
            return;
        }
        // Fallback without the module: reset every indicator, mark the active one.
        document.querySelectorAll('[id^="sortIcon-"]').forEach(icon => {
            icon.className = 'bi bi-arrow-down-up';
        });
        
        const activeIcon = document.getElementById(`sortIcon-${currentSort}`);
        if (activeIcon) {
            if (currentOrder === 'asc') {
                activeIcon.className = 'bi bi-sort-up';
            } else {
                activeIcon.className = 'bi bi-sort-down';
            }
        }
    }

    function setupEventListeners() {
        // Search with debounce
        let searchTimeout;
        searchInput.addEventListener('input', () => {
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(() => {
                currentPage = 1;
                fetchPage(1);
            }, 300);
        });

        // Page size change
        perPageSelect.addEventListener('change', () => {
            currentPerPage = parseInt(perPageSelect.value);
            currentPage = 1;
            fetchPage(1);
        });

        tableBody?.addEventListener('change', (event) => {
            if (event.target.matches('.keyword-checkbox')) updateBulkButtons();
        });
        tableBody?.addEventListener('click', (event) => {
            const button = event.target.closest('.keyword-row-action');
            if (!button || !tableBody.contains(button)) return;
            const action = button.dataset.action;
            const keywordId = Number(button.dataset.keywordId);
            if (action !== 'merge' && (!Number.isSafeInteger(keywordId) || keywordId < 1)) return;
            if (action === 'edit') editKeywordInModal(keywordId);
            else if (action === 'view') window.viewKeyword(keywordId);
            else if (action === 'delete') window.deleteKeyword(keywordId);
            else if (action === 'merge') window.mergeDuplicates(button.dataset.keywordText || '', button);
        });
    }

    function loadCategories() {
        fetch('/api/categories')
            .then(response => {
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}`);
                }
                return response.json();
            })
            .then(data => {
                categories = data || [];
                categoryFilter.innerHTML = `<option value="">${translations.allCategories || 'All Categories'}</option>`;
                if (Array.isArray(data)) {
                    data.forEach(cat => {
                        const option = document.createElement('option');
                        option.value = cat.id;
                        option.textContent = cat.name;
                        categoryFilter.appendChild(option);
                    });
                }
            })
            .catch(error => {
                console.error('Error loading categories:', error);
                categories = [];
                // ✅ FIXED: Show user-friendly error message
                categoryFilter.innerHTML = `
                    <option value="">${translations.allCategories || 'All Categories'}</option>
                    <option value="" disabled>Error loading categories</option>
                `;
                // Show toast notification if available
                if (typeof showToast === 'function') {
                    showToast('Failed to load categories. Please refresh the page.', 'error');
                }
            });
    }

    function renderRows(items, page) {
        console.log(`renderRows called with ${items?.length || 0} items, page ${page}`);

        // ✅ FIXED: Check if tableBody exists
        if (!tableBody) {
            console.error('tableBody element not found!');
            return;
        }

        if (!window.UnifiedTable) {
            console.error('UnifiedTable module not loaded');
            return;
        }

        if (!items || items.length === 0) {
            console.log('No items to render, showing empty state');
            const query = searchInput?.value?.trim() || '';
            const message = query ?
                `${translations.noKeywordsFoundMatching} "${query}". ${translations.tryDifferentSearchTerm}` :
                translations.noKeywordsYet;

            UnifiedTable.renderRows(tableBody, [
                UnifiedTable.states.empty(COLUMN_COUNT, { message, filtered: Boolean(query) })
            ]);
            return;
        }

        console.log(`Rendering ${items.length} items`);

        // Detect duplicates
        const keywordCounts = {};
        const duplicates = new Set();
        items.forEach(kw => {
            if (!kw || typeof kw !== 'object') {
                console.warn('Invalid keyword item:', kw);
                return;
            }
            const text = (kw.text || '').toLowerCase().trim();
            if (keywordCounts[text]) {
                duplicates.add(text);
            } else {
                keywordCounts[text] = 1;
            }
        });

        // Update duplicate count
        const duplicateCountEl = document.getElementById('duplicateCount');
        if (duplicateCountEl) {
            duplicateCountEl.textContent = duplicates.size;
        }

        // The same row the server renders, drawn with the same pieces: one
        // join, one insertion, a page of rows for one reflow.
        const rows = [];
        items.forEach((kw, idx) => {
            // Validate the server ID before using it as a row/action identifier.
            const keywordId = Number(kw?.id);
            if (!kw || typeof kw !== 'object' || !Number.isSafeInteger(keywordId) || keywordId < 1) {
                console.warn(`Skipping invalid keyword at index ${idx}:`, kw);
                return;
            }
            const globalIndex = ((page - 1) * currentPerPage) + (idx + 1);
            const keywordText = String(kw.text || '').toLowerCase().trim();
            const isDuplicate = kw.is_duplicate || duplicates.has(keywordText);
            const usage = kw.usage_count || 0;
            const active = usage > 0;

            rows.push(`
                <tr data-keyword-id="${keywordId}" data-keyword="${escapeHtml(keywordText)}"
                    data-usage-count="${usage}" data-status="${active ? 'active' : 'unused'}">
                    <td class="ut-col-select">
                        <input type="checkbox" class="keyword-checkbox ut-row-check"
                               value="${keywordId}" data-on-change="updateBulkButtons()"
                               aria-label="${escapeHtml(translations.selectKeyword || 'Select keyword')}">
                    </td>
                    <td class="ut-index-cell"><span class="ut-badge ut-badge-neutral">${globalIndex}</span></td>
                    <td data-ut-value="${escapeHtml(keywordText)}">
                        <span class="ut-name">${escapeHtml(kw.text || '')}</span>
                        ${isDuplicate ? `<span class="badge bg-warning text-dark ms-2"><i class="bi bi-exclamation-triangle me-1" aria-hidden="true"></i>${escapeHtml(translations.duplicate || 'Duplicate')}</span>` : ''}
                    </td>
                    <td class="text-end" data-ut-value="${usage}">${usage}</td>
                    <td data-ut-value="${active ? 'active' : 'unused'}">${active
                        ? `<span class="badge bg-success"><i class="bi bi-check-circle me-1" aria-hidden="true"></i>${escapeHtml(translations.active || 'Active')}</span>`
                        : `<span class="badge bg-secondary"><i class="bi bi-dash-circle me-1" aria-hidden="true"></i>${escapeHtml(translations.unused || 'Unused')}</span>`}</td>
                    <td data-ut-value="${escapeHtml((kw.category_name || translations.uncategorized || 'Uncategorized').toLowerCase())}">
                        <span class="ut-muted">${escapeHtml(kw.category_name || translations.uncategorized || 'Uncategorized')}</span>
                    </td>
                    <td>
                        <div class="btn-group btn-group-sm ut-actions">
                            <button type="button" class="btn btn-outline-primary keyword-row-action" data-action="view" data-keyword-id="${keywordId}"
                                    title="${escapeHtml(translations.viewDetails || 'View Details')}" aria-label="${escapeHtml(translations.viewDetails || 'View Details')}">
                                <i class="bi bi-eye" aria-hidden="true"></i>
                            </button>
                            <button type="button" class="btn btn-outline-secondary" data-panel-open
                                    data-panel-url="/keywords/${keywordId}/documents"
                                    data-panel-label="${escapeHtml(kw.text || '')}"
                                    title="${escapeHtml(translations.documentsWithKeyword || 'Documents with this keyword')}">
                                <i class="bi bi-files" aria-hidden="true"></i>
                            </button>
                            <button type="button" class="btn btn-outline-warning keyword-row-action" data-action="edit" data-keyword-id="${keywordId}"
                                    title="${escapeHtml(translations.editKeyword || 'Edit Keyword')}" aria-label="${escapeHtml(translations.editKeyword || 'Edit Keyword')}">
                                <i class="bi bi-pencil" aria-hidden="true"></i>
                            </button>
                            <button type="button" class="btn btn-outline-danger keyword-row-action" data-action="delete" data-keyword-id="${keywordId}"
                                    title="${escapeHtml(translations.deleteKeyword || 'Delete')}" aria-label="${escapeHtml(translations.deleteKeyword || 'Delete')}">
                                <i class="bi bi-trash" aria-hidden="true"></i>
                            </button>
                            ${isDuplicate ? `<button type="button" class="btn btn-outline-warning keyword-row-action" data-action="merge" data-keyword-text="${escapeHtml(keywordText)}"
                                    title="${escapeHtml(translations.mergeDuplicates || 'Merge duplicates')}" aria-label="${escapeHtml(translations.mergeDuplicates || 'Merge duplicates')}"><i class="bi bi-arrow-down-up" aria-hidden="true"></i></button>` : ''}
                        </div>
                    </td>
                </tr>`);
        });
        UnifiedTable.renderRows(tableBody, rows);
    }

    function renderPaginator(page, total_pages, loadMore = {}) {
        console.log(`renderPaginator called: page=${page}, total_pages=${total_pages}`);

        // ✅ FIXED: Validate pagination inputs
        if (total_pages < 1) {
            total_pages = 1;
        }
        if (page < 1) {
            page = 1;
        }
        if (page > total_pages) {
            page = total_pages;
        }
        
        // Don't render if only one page
        if (total_pages <= 1) {
            const paginationContainer = document.querySelector('.unified-pagination-container') || 
                                       document.querySelector('.pagination-container');
            if (paginationContainer) {
                paginationContainer.innerHTML = '';
            }
            return;
        }
        
        // First, check for existing unified-pagination-container from template
        let paginationContainer = document.querySelector('.unified-pagination-container');
        console.log('Found existing unified-pagination-container:', !!paginationContainer);
        
        // If no unified container exists, check for old pagination-container
        if (!paginationContainer) {
            paginationContainer = document.querySelector('.pagination-container');
            console.log('Found existing pagination-container:', !!paginationContainer);
        }
        
        // If still no container, create one
        if (!paginationContainer) {
            console.log('Creating new pagination container');
            paginationContainer = createPaginationContainer();
        }
        
        if (!paginationContainer) {
            console.error('Could not create pagination container');
            return;
        }
        
        console.log('Using pagination container:', paginationContainer.id || paginationContainer.className);
        
        // Ensure the container has an ID for the unified pagination module
        if (!paginationContainer.id) {
            paginationContainer.id = 'paginationContainer';
        }
        
        // Get URL parameters to preserve
        const urlParams = {};
        const currentParams = new URLSearchParams(window.location.search);
        currentParams.forEach((value, key) => {
            if (key !== 'page') {
                urlParams[key] = value;
            }
        });
        
        // Use unified pagination
        import('../modules/rendering/unified-pagination.js').then(module => {
            console.log('Calling renderUnifiedPagination with container:', paginationContainer.id);
            module.renderUnifiedPagination({
                currentPage: page,
                totalPages: total_pages,
                // One control: the table's Load More is rendered by the pager.
                tableId: 'keywordsTable',
                pageSize: currentPerPage,
                totalCount: loadMore.total ?? 0,
                rowsShown: loadMore.shown ?? 0,
                containerId: paginationContainer.id,
                onPageChange: (targetPage) => {
                    console.log('Pagination page change callback triggered:', targetPage);
                    // Update URL without page reload
                    const url = new URL(window.location);
                    url.searchParams.set('page', targetPage);
                    window.history.pushState({}, '', url);
                    fetchPage(targetPage);
                },
                urlParams: urlParams,
                showInfo: true,
                showJump: total_pages > 5,
                baseUrl: window.location.pathname
            });
            console.log('Pagination rendered, container visible:', paginationContainer.offsetParent !== null);
        }).catch(err => {
            console.error('Error loading unified pagination:', err);
            // Fallback to old pagination if module fails to load
            renderOldPaginator(page, total_pages, paginationContainer);
        });
    }
    
    // Fallback old pagination renderer
    function renderOldPaginator(page, total_pages, paginationContainer) {
        paginationContainer.innerHTML = '';
        
        const pageInfo = document.createElement('div');
        pageInfo.className = 'small text-muted pagination-info';
        pageInfo.textContent = `${translations.page || 'Page'} ${page} ${translations.of || 'of'} ${total_pages}`;
        paginationContainer.appendChild(pageInfo);
        
        const pagContainer = document.createElement('ul');
        pagContainer.className = 'pagination pagination-sm mb-0';
        paginationContainer.appendChild(pagContainer);
        
        const windowSize = 2;
        const start = Math.max(1, page - windowSize);
        const end = Math.min(total_pages, page + windowSize);

        function addItem(label, p, disabled=false, active=false) {
            const li = document.createElement('li');
            li.className = 'page-item' + (disabled ? ' disabled' : '') + (active ? ' active' : '');
            const a = document.createElement('a');
            a.className = 'page-link';
            a.href = '#';
            a.innerHTML = label;
            a.addEventListener('click', function(ev){
                ev.preventDefault();
                if (disabled || active) return;
                const url = new URL(window.location);
                url.searchParams.set('page', p);
                window.history.pushState({}, '', url);
                fetchPage(p);
            });
            li.appendChild(a);
            pagContainer.appendChild(li);
        }

        addItem('<i class="bi bi-chevron-double-left" aria-hidden="true"></i>', 1, page === 1);
        addItem('<i class="bi bi-chevron-left" aria-hidden="true"></i>', Math.max(1, page - 1), page === 1);

        if (start > 1) addItem('1', 1);
        if (start > 2) {
            const li = document.createElement('li'); 
            li.className='page-item disabled';
            li.innerHTML = '<span class="page-link">…</span>'; 
            pagContainer.appendChild(li);
        }

        for (let p = start; p <= end; p++) {
            addItem(String(p), p, false, p === page);
        }

        if (end < total_pages - 1) {
            const li = document.createElement('li'); 
            li.className='page-item disabled';
            li.innerHTML = '<span class="page-link">…</span>'; 
            pagContainer.appendChild(li);
        }
        if (end < total_pages) addItem(String(total_pages), total_pages);

        addItem('<i class="bi bi-chevron-right" aria-hidden="true"></i>', Math.min(total_pages, page + 1), page === total_pages);
        addItem('<i class="bi bi-chevron-double-right" aria-hidden="true"></i>', total_pages, page === total_pages);
    }

    function createPaginationContainer() {
        // The template renders the mount (`#pagination`); find it, or fall
        // back to a container inserted right after the table unit.
        const keywordsTable = document.getElementById('keywordsTable');
        if (!keywordsTable) {
            console.error('keywordsTable element not found');
            return null;
        }

        let container = document.getElementById('pagination') ||
            document.querySelector('.pagination-container') ||
            document.querySelector('.unified-pagination-container');
        if (container) {
            if (!container.id) container.id = 'paginationContainer';
            return container;
        }

        const unit = keywordsTable.closest('.ut');
        if (!unit || !unit.parentNode) return null;

        container = document.createElement('div');
        container.id = 'paginationContainer';
        container.className = 'pagination-container';
        unit.parentNode.insertBefore(container, unit.nextSibling);
        return container;
    }

    function createPaginationList() {
        const ul = document.createElement('ul');
        ul.className = 'pagination pagination-sm mb-0';
        return ul;
    }

    function syncListQuery() {
        const url = new URL(window.location.href);
        const q = searchInput && searchInput.value.trim();
        if (q) {
            url.searchParams.set('search', q);
            url.searchParams.set('q', q);
        } else {
            url.searchParams.delete('search');
            url.searchParams.delete('q');
        }
        if (currentSort) url.searchParams.set('sort', currentSort);
        else url.searchParams.delete('sort');
        if (currentOrder) url.searchParams.set('order', currentOrder);
        else url.searchParams.delete('order');
        url.searchParams.set('page', String(currentPage));
        if (statusFilter && statusFilter.value) url.searchParams.set('status', statusFilter.value);
        else url.searchParams.delete('status');
        if (categoryFilter && categoryFilter.value) url.searchParams.set('category', categoryFilter.value);
        else url.searchParams.delete('category');
        window.history.replaceState({}, '', url.pathname + '?' + url.searchParams.toString());
    }

    let pendingFetch = null;
    let requestSequence = 0;  // ✅ FIXED: Track request sequence to prevent race conditions
    
    function fetchPage(page=1) {
        currentPage = page;
        const sequence = ++requestSequence;  // ✅ FIXED: Increment sequence for each request
        
        if (pendingFetch) {
            pendingFetch.abort();
        }
        const controller = new AbortController();
        pendingFetch = controller;

        // Keep painted rows visible while a later page loads. Only the
        // first empty tbody needs a loading placeholder.
        const hasRows = tableBody && tableBody.querySelector('tr[data-keyword-id], tr[data-word-id]');
        if (tableBody && window.UnifiedTable && !hasRows) {
            UnifiedTable.renderRows(tableBody, [
                UnifiedTable.states.loading(COLUMN_COUNT, translations.loading || 'Loading...')
            ]);
        }

        // Get API URL from page data or use default
        const pageDataEl = document.getElementById('keywords-list-page-data');
        let apiUrl = '/api/keywords';
        if (pageDataEl) {
            try {
                const data = JSON.parse(pageDataEl.textContent);
                apiUrl = data.api_url || '/api/keywords';
            } catch (e) {
                console.warn('Error parsing page data, using default API URL');
            }
        }

        const url = new URL(apiUrl, window.location.origin);
        url.searchParams.set('page', page);
        url.searchParams.set('per_page', currentPerPage);
        if (currentSort) url.searchParams.set('sort', currentSort);
        if (currentOrder) url.searchParams.set('order', currentOrder);
        
        if (searchInput.value.trim()) url.searchParams.set('q', searchInput.value.trim());
        if (statusFilter.value) url.searchParams.set('status', statusFilter.value);
        if (categoryFilter.value) url.searchParams.set('category', categoryFilter.value);

        // Add cache-busting parameter to ensure fresh data
        url.searchParams.set('_t', Date.now());
        
        fetch(url.toString(), { 
            signal: controller.signal,
            cache: 'no-cache',
            headers: {
                'Accept': 'application/json',
                'Cache-Control': 'no-cache'
            }
        })
            .then(r => {
                if (!r.ok) {
                    throw new Error(`HTTP error! status: ${r.status}`);
                }
                return r.json();
            })
            .then(json => {
                // ✅ FIXED: Only process if this is still the latest request (prevent race conditions)
                if (sequence !== requestSequence) {
                    console.log(`Ignoring stale request ${sequence}, current is ${requestSequence}`);
                    return;
                }
                
                // ✅ DEBUG: Log the API response
                console.log('API Response:', {
                    success: json?.success,
                    keywordsCount: json?.keywords?.length || 0,
                    total: json?.total,
                    page: json?.page,
                    total_pages: json?.total_pages,
                    hasKeywords: !!json?.keywords
                });
                
                // ✅ FIXED: Handle both success:true and direct keywords array responses
                if (!json || typeof json !== 'object') {
                    console.error('API returned null/undefined or invalid format');
                    if (tableBody && window.UnifiedTable) {
                        UnifiedTable.renderRows(tableBody, [
                            UnifiedTable.states.error(COLUMN_COUNT,
                                `${translations.errorLoadingKeywords || 'Error loading keywords'}: Invalid response`)
                        ]);
                    }
                    return;
                }

                // Check if response has error (success: false)
                if (json.success === false) {
                    console.error('API error', json);
                    const errorMsg = json?.error || 'Unknown API error';
                    if (tableBody && window.UnifiedTable) {
                        UnifiedTable.renderRows(tableBody, [
                            UnifiedTable.states.error(COLUMN_COUNT,
                                `${translations.errorLoadingKeywords || 'Error loading keywords'}: ${errorMsg}`)
                        ]);
                    }
                    return;
                }

                // ✅ FIXED: Validate required fields
                if (!Array.isArray(json.keywords) && !Array.isArray(json.results) && !Array.isArray(json.items)) {
                    console.error('API response missing keywords array', json);
                    if (tableBody && window.UnifiedTable) {
                        UnifiedTable.renderRows(tableBody, [
                            UnifiedTable.states.error(COLUMN_COUNT,
                                `${translations.errorLoadingKeywords || 'Error loading keywords'}: API response missing keywords array`)
                        ]);
                    }
                    return;
                }

                // ✅ FIXED: Update currentPerPage from API response to keep in sync
                if (json.per_page) {
                    currentPerPage = json.per_page;
                    if (perPageSelect && perPageSelect.value != json.per_page) {
                        perPageSelect.value = json.per_page;
                    }
                }

                // The server decides the order; the headers show its answer.
                // No sort in the answer means the default order: cleared.
                currentSort = json.sort_by || null;
                currentOrder = json.sort_by ? (json.sort_order || null) : null;
                updateSortIcons();
                
                // Get keywords array - handle both success:true and direct array responses
                const keywords = json.keywords || json.results || json.items || [];
                const pageNum = json.page || 1;
                const totalPages = json.total_pages || 1;
                
                console.log(`Rendering ${keywords.length} keywords for page ${pageNum}`);
                console.log('First keyword sample:', keywords.length > 0 ? keywords[0] : 'No keywords');
                
                // ✅ DEBUG: Log full response if no keywords
                if (keywords.length === 0) {
                    console.warn('⚠️ No keywords in response! Full response:', json);
                    console.warn('Response keys:', Object.keys(json));
                    console.warn('Response.success:', json.success);
                    console.warn('Response.keywords type:', typeof json.keywords, Array.isArray(json.keywords));
                }
                
                // ✅ FIXED: Wrap in try-catch to catch any rendering errors
                try {
                    // Double-check sequence before rendering (race condition protection)
                    if (sequence !== requestSequence) {
                        console.log(`Skipping render for stale request ${sequence}`);
                        return;
                    }
                    
                    // Force clear table first to ensure fresh render
                    if (tableBody) {
                        tableBody.innerHTML = '';
                    }
                    renderRows(keywords, pageNum);
                    renderPaginator(pageNum, totalPages, { total: json.total, shown: keywords.length });
                    updateSearchInfo(json);
                    syncListQuery();

                    // STALE-01: keep the server-rendered "Total Keywords" stat
                    // card in sync after client-side pagination/mutations.
                    const totalStat = document.getElementById('totalKeywordsStat');
                    if (totalStat && typeof json.total === 'number') {
                        totalStat.textContent = json.total;
                    }
                    
                    // Force a reflow to ensure DOM updates are visible
                    if (tableBody) {
                        void tableBody.offsetWidth;
                    }
                } catch (renderError) {
                    console.error('❌ Error rendering rows:', renderError);
                    console.error('Stack:', renderError.stack);
                    if (tableBody && window.UnifiedTable) {
                        UnifiedTable.renderRows(tableBody, [
                            UnifiedTable.states.error(COLUMN_COUNT,
                                `${translations.errorLoadingKeywords || 'Error loading keywords'}: ${renderError.message}`)
                        ]);
                    }
                }
            })
            .catch(err => {
                // Cancelled by a newer request: expected, not a failure.
                if (err.name === 'AbortError') return;
                // A failure from a request that is no longer current must not
                // replace the results of the newer request.
                if (sequence !== requestSequence) {
                    console.log(`Ignoring failed stale request ${sequence}`);
                    return;
                }
                console.error('Fetch error', err);
                if (tableBody && window.UnifiedTable) {
                    UnifiedTable.renderRows(tableBody, [
                        UnifiedTable.states.error(COLUMN_COUNT,
                            `${translations.failedToLoadKeywords}: ${err.message}`)
                    ]);
                }
                const infoEl = document.getElementById('searchResultsInfo');
                if (infoEl) infoEl.textContent = translations.errorLoadingData || 'Error loading data';
            })
            .finally(()=> { if (pendingFetch === controller) pendingFetch = null; });
    }

    function updateSearchInfo(json) {
        const infoEl = document.getElementById('searchResultsInfo');
        if (infoEl) {
            const total = json.total || 0;
            const query = searchInput.value.trim();
            // A range only exists when there are rows to number. Zero results
            // must never read "1–0".
            const shown = (json.keywords || []).length;
            const start = shown > 0 ? ((json.page - 1) * json.per_page) + 1 : 0;
            const end = shown > 0 ? start + shown - 1 : 0;

            if (query) {
                const noun = `result${total !== 1 ? 's' : ''}`;
                infoEl.textContent = shown > 0
                    ? `Found ${total} ${noun} for "${query}" (${start}–${end})`
                    : `Found ${total} ${noun} for "${query}"`;
            } else {
                infoEl.textContent = shown > 0
                    ? `${translations.showing} ${start}–${end} ${translations.of} ${total} ${translations.keywords}`
                    : `${translations.showing} 0 ${translations.of} ${total} ${translations.keywords}`;
            }
        }
    }

    // Selection management
    //
    // The page owns the selection: which rows are checked, and the set the
    // bulk operations will send. The toolbar owns how that scope is drawn and
    // announced, so this reports the numbers and nothing else - no button is
    // enabled or disabled here, and no action is named.
    function updateSelection() {
        const checkboxes = document.querySelectorAll('.keyword-checkbox:checked');
        selectedKeywords.clear();
        checkboxes.forEach(cb => selectedKeywords.add(parseInt(cb.value)));

        // Update select all checkbox
        const allCheckboxes = document.querySelectorAll('.keyword-checkbox');
        if (selectAllCheckbox) {
            selectAllCheckbox.checked = allCheckboxes.length > 0 && checkboxes.length === allCheckboxes.length;
            selectAllCheckbox.indeterminate = checkboxes.length > 0 && checkboxes.length < allCheckboxes.length;
        }

        if (window.ActionToolbar) {
            window.ActionToolbar.sync('keywordsActionBar', {
                selected: checkboxes.length,
                total: allCheckboxes.length
            });
        }
    }

    // The rows call this name and the Select All/None controls call the other;
    // there is one implementation behind both, so a row change and a Select All
    // cannot drift apart.
    function updateBulkButtons() {
        updateSelection();
    }

    function toggleSelectAll() {
        const isChecked = selectAllCheckbox.checked;
        document.querySelectorAll('.keyword-checkbox').forEach(cb => {
            cb.checked = isChecked;
        });
        updateSelection();
    }

    function selectAll() {
        document.querySelectorAll('.keyword-checkbox').forEach(cb => {
            cb.checked = true;
        });
        updateSelection();
    }

    function selectNone() {
        document.querySelectorAll('.keyword-checkbox').forEach(cb => {
            cb.checked = false;
        });
        updateSelection();
    }

    /** One control: select every row when empty, clear when any are selected. */
    function toggleAllSelection() {
        const anySelected = document.querySelectorAll('.keyword-checkbox:checked').length > 0;
        if (anySelected) {
            selectNone();
        } else {
            selectAll();
        }
    }

    // Filter and sort functions
    function applyFilters() {
        currentFilters = {
            status: statusFilter.value,
            category: categoryFilter.value
        };
        currentPage = 1;
        
        // ✅ FIXED: Update URL to persist filters
        const url = new URL(window.location);
        if (statusFilter.value) {
            url.searchParams.set('status', statusFilter.value);
        } else {
            url.searchParams.delete('status');
        }
        if (categoryFilter.value) {
            url.searchParams.set('category', categoryFilter.value);
        } else {
            url.searchParams.delete('category');
        }
        url.searchParams.set('page', '1');
        window.history.pushState({}, '', url);

        fetchPage(1);
    }

    function changePageSize() {
        currentPerPage = parseInt(perPageSelect.value);
        currentPage = 1;
        fetchPage(1);
    }

    function clearSearch() {
        searchInput.value = '';
        currentPage = 1;
        fetchPage(1);
    }

    // Inline editing removed - now using modal edit

    // Bulk operations
    function bulkDelete() {
        if (selectedKeywords.size === 0) return;
        
        if (!confirm(`${selectedKeywords.size} ${translations.deleteSelectedKeywords || 'Delete selected keywords?'}`)) return;
        
        // ✅ FIXED: Add loading state
        const btn = document.getElementById('bulkDeleteBtn');
        const originalHTML = btn ? btn.innerHTML : '';
        if (btn) {
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Deleting...';
        }
        
        const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
        fetch('/api/keywords/bulk-delete', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken
            },
            body: JSON.stringify({ keyword_ids: Array.from(selectedKeywords) })
        })
        .then(response => {
            if (!response.ok) {
                return response.json().then(data => {
                    throw new Error(data.error || `HTTP ${response.status}`);
                });
            }
            return response.json();
        })
        .then(data => {
            if (data.success) {
                showToast(`${translations.successfullyDeleted || 'Successfully deleted'} ${data.deleted_count} ${translations.keywords || 'keywords'}`, 'success');
                // ✅ Complete page reload with cache-busting
                if (window.swapNavigate) { window.swapNavigate(window.location.pathname + '?t=' + Date.now()); } else { window.location.href = window.location.pathname + '?t=' + Date.now(); }
            } else {
                showToast((translations.errorDeletingKeywords || 'Error deleting keywords') + ': ' + (data.error || translations.unknownError || 'Unknown error'), 'error');
                if (btn) {
                    btn.disabled = false;
                    btn.innerHTML = originalHTML;
                }
            }
        })
        .catch(error => {
            console.error('Error:', error);
            showToast(translations.errorDeletingKeywords || 'Error deleting keywords', 'error');
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = originalHTML;
            }
        });
    }

    // Reset modal to "Add" mode
    function setKeywordModalMode(editing) {
        const label = editing
            ? (translations.editKeyword || 'Edit Keyword')
            : (translations.addKeyword || 'Add Keyword');
        const title = document.getElementById('addKeywordModalLabel');
        if (title) title.textContent = label;
        const submitBtn = document.querySelector('#addKeywordModal [data-on-click="saveKeyword()"]');
        if (submitBtn) {
            submitBtn.textContent = editing
                ? (translations.updateKeyword || 'Update Keyword')
                : (translations.addKeyword || 'Add Keyword');
        }
    }

    // Reset modal to "Add" mode
    function resetKeywordModalToAdd() {
        editingKeywordId = null;
        setKeywordModalMode(false);
    }

    // Edit keyword in the add modal: same word chips and category picker,
    // saved with PUT /api/keywords/<id>.
    async function editKeywordInModal(keywordId) {
        try {
            const response = await fetch(`/api/keywords/${keywordId}`);
            const data = await response.json();
            if (!response.ok || !data.success || !data.keyword) {
                throw new Error(data.error || `HTTP ${response.status}`);
            }
            const keyword = data.keyword;
            const ids = keyword.word_ids || [];
            const texts = String(keyword.text || '').split(/\s+/).filter(Boolean);
            const words = ids.map((id, index) => ({
                id: id,
                text: texts.length === ids.length ? texts[index] : `#${id}`
            }));
            let categoryName = null;
            if (keyword.category_id) {
                const known = categories.find(c => c.id === keyword.category_id);
                if (known) {
                    categoryName = known.name;
                } else {
                    const catResponse = await fetch(`/api/categories/${keyword.category_id}`);
                    const cat = await catResponse.json().catch(() => ({}));
                    categoryName = cat.success ? cat.name : null;
                }
            }
            openAddKeywordModal({
                id: keyword.id,
                words: words,
                category_id: keyword.category_id || null,
                category_name: categoryName
            });
        } catch (error) {
            console.error('Error loading keyword:', error);
            showToast((translations.errorLoadingData || 'Error loading keyword data') + ': ' + error.message, 'error');
        }
    }
    
    function bulkUpdate() {
        if (selectedKeywords.size === 0) return;
        
        // If only one keyword selected, open edit modal
        if (selectedKeywords.size === 1) {
            const keywordId = Array.from(selectedKeywords)[0];
            editKeywordInModal(keywordId);
            return;
        }
        
        // For multiple keywords, show a message and edit the first one
        if (confirm(`${translations.multipleKeywordsSelected || 'Multiple keywords selected'}. ${translations.editFirstKeyword || 'Edit the first keyword?'}`)) {
            const keywordId = Array.from(selectedKeywords)[0];
            editKeywordInModal(keywordId);
        }
    }

    // Export functions
    function exportKeywords() {
        const selectedIds = Array.from(selectedKeywords);
        const url = new URL('/api/keywords/export', window.location.origin);
        url.searchParams.set('format', 'csv');
        if (selectedIds.length > 0) {
            selectedIds.forEach(id => url.searchParams.append('ids', id));
        }
        
        window.open(url.toString(), '_blank');
    }

    function mergeAllDuplicates() {
        if (!confirm(translations.mergeAllDuplicatesConfirm + '\n\n' + translations.mergeAllDuplicatesDetails)) return;
        
        // Show loading indicator
        const btn = event.target.closest('button');
        const originalHTML = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>' + translations.merging;
        
        // AUDIT-CSRF-01: the request was rejected (400) without the token.
        fetch('/api/keywords/merge-all-duplicates', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': (window.CSRF && window.CSRF.getToken()) || document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '' }
        })
        .then(response => response.json())
        .then(data => {
            btn.disabled = false;
            btn.innerHTML = originalHTML;
            
            if (data.success) {
                showToast(`${translations.successfullyMergedAll || 'Merged'} ${data.merged_count} ${translations.keywords || 'keywords'} (${data.duplicate_sets} ${translations.duplicateSets || 'sets'})`, 'success');
                // ✅ Complete page reload with cache-busting
                if (window.swapNavigate) { window.swapNavigate(window.location.pathname + '?t=' + Date.now()); } else { window.location.href = window.location.pathname + '?t=' + Date.now(); }
            } else {
                showToast((translations.errorMergingDuplicatesColon || 'Error merging duplicates') + ': ' + (data.error || translations.unknownError), 'error');
            }
        })
        .catch(error => {
            btn.disabled = false;
            btn.innerHTML = originalHTML;
            console.error('Error:', error);
            showToast((translations.errorMergingDuplicatesColon || 'Error merging duplicates') + ': ' + error.message, 'error');
        });
    }

    // Global functions for template buttons
    window.viewKeyword = function(id){ 
        if (window.swapNavigate) { window.swapNavigate(`/keywords/${id}`); } else { window.location.href = `/keywords/${id}`; }
    };
    
    window.editKeyword = function(id) {
        // Use the modal edit function instead of redirecting
        editKeywordInModal(id);
    };
    
    window.deleteKeyword = function(id){
        if (!confirm(translations.deleteKeywordConfirm + id + '?')) return;
        const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
        fetch(`/api/keywords/${id}`, {
            method: 'DELETE',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken
            }
        })
            .then(r=>r.json()).then(j=>{
                if (j.success) {
                    showToast(translations.keywordDeletedSuccessfully || 'Keyword deleted successfully', 'success');
                    if (window.swapNavigate) { window.swapNavigate(window.location.pathname + '?t=' + Date.now()); } else { window.location.href = window.location.pathname + '?t=' + Date.now(); }
                } else {
                    showToast(j.error || translations.failedToDelete || 'Failed to delete', 'error');
                }
            }).catch(e=>showToast((translations.error || 'Error') + ': ' + e.message, 'error'));
    };
    
    // ✅ NEW: Update keyword associations for all files
    window.updateKeywordAssociations = function() {
        const btn = document.getElementById('updateKeywordsBtn');
        if (!btn) return;
        
        // Confirm action
        if (!confirm(translations.updateKeywordAssociationsConfirm || 
                    'This will scan all files in the database and update keyword associations. This may take a while. Continue?')) {
            return;
        }
        
        // Disable button and show loading state
        const originalHTML = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<i class="bi bi-hourglass-split me-2"></i> ' + (translations.updating || 'Updating...');
        
        const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
        
        fetch('/api/keywords/update-associations', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken
            }
        })
        .then(response => {
            // Check if response is JSON
            const contentType = response.headers.get('content-type');
            if (!contentType || !contentType.includes('application/json')) {
                // If not JSON, try to get text for error message
                return response.text().then(text => {
                    throw new Error(`Server returned non-JSON response (${response.status}): ${text.substring(0, 100)}`);
                });
            }
            
            // Check if response is OK
            if (!response.ok) {
                return response.json().then(data => {
                    throw new Error(data.error || `HTTP ${response.status}: ${response.statusText}`);
                });
            }
            
            return response.json();
        })
        .then(data => {
            btn.disabled = false;
            btn.innerHTML = originalHTML;
            
            if (data.success) {
                let message = (translations.keywordAssociationsUpdated || 'Keyword associations updated successfully!') + '\n\n' +
                    (translations.filesProcessed || 'Files processed') + ': ' + data.files_processed + ' / ' + data.total_files + '\n' +
                    (translations.newAssociations || 'New associations') + ': ' + data.new_associations + '\n' +
                    (translations.keywordsChecked || 'Keywords checked') + ': ' + data.keywords_checked;
                if (data.errors > 0) {
                    message += '\n' + (translations.errors || 'Errors') + ': ' + data.errors;
                }
                showToast(message.replace(/\n+/g, ' · '), data.errors > 0 ? 'warning' : 'success', 8000);
                if (window.swapNavigate) { window.swapNavigate(window.location.pathname + '?t=' + Date.now()); } else { window.location.href = window.location.pathname + '?t=' + Date.now(); }
            } else {
                showToast((translations.error || 'Error') + ': ' + (data.error || translations.unknownError || 'Unknown error'), 'error');
            }
        })
        .catch(error => {
            btn.disabled = false;
            btn.innerHTML = originalHTML;
            console.error('Error updating keyword associations:', error);
            showToast((translations.error || 'Error') + ': ' + error.message, 'error');
        });
    };
    
    window.mergeDuplicates = function(keywordText, btn = null){
        keywordText = String(keywordText || '');
        if (!confirm(`${translations.mergeAllDuplicatesConfirm || 'Merge all duplicates of'} "${keywordText}"? This will combine usage counts and keep the first occurrence.`)) return;
        
        // The action button is passed by the delegated click handler; never
        // build a selector from the keyword text.
        const originalHTML = btn ? btn.innerHTML : '';
        if (btn) {
            btn.disabled = true;
            btn.setAttribute('aria-busy', 'true');
            btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1" aria-hidden="true"></span> Merging...';
        }
        
        const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
        fetch('/api/keywords/merge-duplicates', {
            method: 'POST',
            headers: { 
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken
            },
            body: JSON.stringify({ keyword_text: keywordText })
        })
        .then(response => {
            if (!response.ok) {
                return response.json().then(data => {
                    throw new Error(data.error || `HTTP ${response.status}`);
                });
            }
            return response.json();
        })
        .then(data => {
            if (data.success) {
                showToast(`${translations.successfullyMerged || 'Successfully merged'} ${data.merged_count} ${translations.duplicateKeywordsFor || 'duplicate keywords for'} "${keywordText}". ${translations.totalUsage || 'Total usage'}: ${data.total_usage || 0}.`, 'success');
                fetchPage(currentPage);
            } else {
                showToast((translations.errorMergingDuplicates || 'Error merging duplicates') + ': ' + (data.error || translations.unknownError || 'Unknown error'), 'error');
                if (btn) {
                    btn.disabled = false;
                    btn.removeAttribute('aria-busy');
                    btn.innerHTML = originalHTML;
                }
            }
        })
        .catch(error => {
            console.error('Error:', error);
            showToast(translations.errorMergingDuplicates || 'Error merging duplicates', 'error');
            if (btn) {
                btn.disabled = false;
                btn.removeAttribute('aria-busy');
                btn.innerHTML = originalHTML;
            }
        });
    };

    // Helper functions for keyword modal
    function escapeHtml(text) {
        if (text == null || text === undefined) {
            return '';
        }
        const div = document.createElement('div');
        div.textContent = String(text);
        return div.innerHTML;
    }

    async function getCSRFTokenAsync() {
        const metaToken = document.querySelector('meta[name="csrf-token"]');
        if (metaToken) {
            const token = metaToken.getAttribute('content');
            if (token) return token;
        }
        
        try {
            const response = await fetch('/api/csrf-token');
            if (!response.ok) {
                console.warn('Failed to fetch CSRF token from API');
                return '';
            }
            const data = await response.json();
            return data.csrf_token || '';
        } catch (error) {
            console.error('Error fetching CSRF token:', error);
            return '';
        }
    }

    function showToast(message, type = 'info', duration = 4000) {
        const tone = type === 'error' ? 'error' : type === 'success' ? 'success' : type === 'warning' ? 'warning' : 'info';
        if (window.Toast && typeof window.Toast.show === 'function') {
            window.Toast.show({ tone: tone, message: message, timeout: duration, dismissible: true });
            return;
        }
        if (typeof window.showToast === 'function' && window.showToast !== showToast) {
            window.showToast(message, type, duration);
            return;
        }
        let toastContainer = (document.querySelector('[data-toast-region]') || document.getElementById('toastContainer'));
        if (!toastContainer) {
            toastContainer = document.createElement('div');
            toastContainer.id = 'toastContainer';
            toastContainer.className = 'toast-container position-fixed top-0 end-0 p-3';
            toastContainer.style.zIndex = '9999';
            document.body.appendChild(toastContainer);
        }
        
        const toastId = 'toast-' + Date.now();
        const icons = {
            success: 'check-circle-fill',
            error: 'exclamation-triangle-fill',
            warning: 'exclamation-triangle-fill',
            info: 'info-circle-fill'
        };
        
        const bgColors = {
            success: 'success',
            error: 'danger',
            warning: 'warning',
            info: 'info'
        };
        
        const toastHtml = `
            <div id="${toastId}" class="toast align-items-center text-white bg-${bgColors[type]} border-0" role="alert">
                <div class="d-flex">
                    <div class="toast-body">
                        <i class="bi bi-${icons[type]} me-2"></i>
                        ${escapeHtml(message)}
                    </div>
                    <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
                </div>
            </div>
        `;
        
        toastContainer.insertAdjacentHTML('beforeend', toastHtml);
        const toastElement = document.getElementById(toastId);
        if (typeof bootstrap !== 'undefined' && bootstrap.Toast) {
            const toast = new bootstrap.Toast(toastElement, { delay: duration });
            toast.show();
            
            toastElement.addEventListener('hidden.bs.toast', () => {
                toastElement.remove();
            });
        } else {
            // Fallback if Bootstrap not available
            setTimeout(() => toastElement.remove(), duration);
        }
    }

    // Keyword modal state
    let keywordWordSearchTimeout = null;
    let keywordCategorySearchTimeout = null;
    let keywordWordInputHandler = null;
    let keywordCategoryInputHandler = null;
    let keywordWordKeydownHandler = null;
    let keywordCategoryKeydownHandler = null;
    let keywordClickOutsideHandler = null;
    let selectedWords = [];
    let selectedCategoryId = null;
    let selectedCategoryText = null;
    let editingKeywordId = null;

    // Open the keyword modal: empty to add, or seeded with an existing
    // keyword ({id, words: [{id, text}], category_id, category_name}) to edit.
    function openAddKeywordModal(existing) {
        const modalElement = document.getElementById('addKeywordModal');
        if (!modalElement) {
            console.error('Add keyword modal not found');
            return;
        }
        
        const modal = new bootstrap.Modal(modalElement);
        const wordInput = document.getElementById('keywordWordInput');
        const categoryInput = document.getElementById('keywordCategoryInput');
        const wordResults = document.getElementById('keywordWordSearchResults');
        const categoryResults = document.getElementById('keywordCategorySearchResults');
        const selectedWordsContainer = document.getElementById('selectedWordsContainer');
        const selectedWordsList = document.getElementById('selectedWordsList');
        const selectedCategoryIdInput = document.getElementById('selectedCategoryId');
        
        if (!wordInput || !categoryInput || !wordResults || !categoryResults || !selectedWordsList) {
            console.error('Required modal elements not found');
            return;
        }
        
        // Clean up any existing handlers
        if (keywordClickOutsideHandler) {
            document.removeEventListener('click', keywordClickOutsideHandler, true);
            keywordClickOutsideHandler = null;
        }
        if (keywordWordInputHandler && wordInput) {
            wordInput.removeEventListener('input', keywordWordInputHandler);
            keywordWordInputHandler = null;
        }
        if (keywordCategoryInputHandler && categoryInput) {
            categoryInput.removeEventListener('input', keywordCategoryInputHandler);
            keywordCategoryInputHandler = null;
        }
        if (keywordWordKeydownHandler && wordInput) {
            wordInput.removeEventListener('keydown', keywordWordKeydownHandler);
            keywordWordKeydownHandler = null;
        }
        if (keywordCategoryKeydownHandler && categoryInput) {
            categoryInput.removeEventListener('keydown', keywordCategoryKeydownHandler);
            keywordCategoryKeydownHandler = null;
        }
        if (keywordWordSearchTimeout) {
            clearTimeout(keywordWordSearchTimeout);
            keywordWordSearchTimeout = null;
        }
        if (keywordCategorySearchTimeout) {
            clearTimeout(keywordCategorySearchTimeout);
            keywordCategorySearchTimeout = null;
        }
        
        // Reset state
        selectedWords = [];
        selectedCategoryId = null;
        selectedCategoryText = null;
        wordInput.value = '';
        categoryInput.value = '';
        if (selectedCategoryIdInput) selectedCategoryIdInput.value = '';
        wordResults.style.display = 'none';
        wordResults.innerHTML = '';
        categoryResults.style.display = 'none';
        categoryResults.innerHTML = '';
        selectedWordsContainer.style.display = 'none';
        selectedWordsList.innerHTML = '';

        const editing = !!(existing && existing.id);
        editingKeywordId = editing ? existing.id : null;
        setKeywordModalMode(editing);
        if (editing) {
            selectedWords = (existing.words || []).slice();
            selectedCategoryId = existing.category_id;
            selectedCategoryText = existing.category_name;
            categoryInput.value = existing.category_name || '';
            if (selectedCategoryIdInput) selectedCategoryIdInput.value = existing.category_id || '';
            updateSelectedWordsDisplay();
        }
        
        // Initialize word search
        function initializeWordSearch() {
            if (keywordWordSearchTimeout) {
                clearTimeout(keywordWordSearchTimeout);
                keywordWordSearchTimeout = null;
            }
            
            if (keywordWordInputHandler) {
                wordInput.removeEventListener('input', keywordWordInputHandler);
            }
            if (keywordWordKeydownHandler) {
                wordInput.removeEventListener('keydown', keywordWordKeydownHandler);
            }
            
            keywordWordInputHandler = function(e) {
                const searchTerm = e.target.value.trim();
                
                if (keywordWordSearchTimeout) {
                    clearTimeout(keywordWordSearchTimeout);
                }
                
                if (searchTerm.length === 0) {
                    wordResults.style.display = 'none';
                    wordResults.innerHTML = '';
                    return;
                }
                
                keywordWordSearchTimeout = setTimeout(() => {
                    searchWordsForKeyword(searchTerm);
                }, 300);
            };
            wordInput.addEventListener('input', keywordWordInputHandler);
            
            keywordWordKeydownHandler = function(e) {
                if (e.key === 'ArrowDown') {
                    e.preventDefault();
                    const firstItem = wordResults.querySelector('.word-result-item');
                    if (firstItem) {
                        firstItem.focus();
                        firstItem.classList.add('active');
                    }
                } else if (e.key === 'Escape') {
                    wordResults.style.display = 'none';
                }
            };
            wordInput.addEventListener('keydown', keywordWordKeydownHandler);
            
            setTimeout(() => {
                wordInput.focus();
            }, 300);
        }
        
        // Initialize category search
        function initializeCategorySearch() {
            if (keywordCategorySearchTimeout) {
                clearTimeout(keywordCategorySearchTimeout);
                keywordCategorySearchTimeout = null;
            }
            
            if (keywordCategoryInputHandler) {
                categoryInput.removeEventListener('input', keywordCategoryInputHandler);
            }
            if (keywordCategoryKeydownHandler) {
                categoryInput.removeEventListener('keydown', keywordCategoryKeydownHandler);
            }
            
            keywordCategoryInputHandler = function(e) {
                const searchTerm = e.target.value.trim();
                
                if (keywordCategorySearchTimeout) {
                    clearTimeout(keywordCategorySearchTimeout);
                }
                
                if (searchTerm.length === 0) {
                    categoryResults.style.display = 'none';
                    categoryResults.innerHTML = '';
                    return;
                }
                
                keywordCategorySearchTimeout = setTimeout(() => {
                    searchCategoriesForKeyword(searchTerm);
                }, 300);
            };
            categoryInput.addEventListener('input', keywordCategoryInputHandler);
            
            keywordCategoryKeydownHandler = function(e) {
                if (e.key === 'ArrowDown') {
                    e.preventDefault();
                    const firstItem = categoryResults.querySelector('.word-result-item');
                    if (firstItem) {
                        firstItem.focus();
                        firstItem.classList.add('active');
                    }
                } else if (e.key === 'Escape') {
                    categoryResults.style.display = 'none';
                }
            };
            categoryInput.addEventListener('keydown', keywordCategoryKeydownHandler);
        }
        
        // Search words function
        async function searchWordsForKeyword(searchTerm) {
            if (!searchTerm || searchTerm.length === 0) {
                wordResults.style.display = 'none';
                return;
            }
            
            try {
                const params = new URLSearchParams({
                    q: searchTerm,
                    page: 1,
                    per_page: 20
                });
                
                const response = await fetch(`/api/words/search?${params.toString()}`);
                const data = await response.json();
                
                if (data.results && data.results.length > 0) {
                    renderWordResultsForKeyword(data.results, searchTerm);
                } else {
                    renderWordResultsForKeyword([], searchTerm);
                }
            } catch (error) {
                console.error('Error searching words:', error);
                wordResults.style.display = 'none';
            }
        }
        
        // Render word results
        function renderWordResultsForKeyword(results, searchTerm) {
            wordResults.innerHTML = '';
            
            if (results.length > 0) {
                results.forEach((item, index) => {
                    const wordId = item.id || item.word_id;
                    const wordText = item.text || item.word || '';
                    const usageCount = item.usage_count || 0;
                    
                    // Skip if already selected
                    if (selectedWords.some(w => w.text === wordText)) {
                        return;
                    }
                    
                    const itemDiv = document.createElement('div');
                    itemDiv.className = 'word-result-item';
                    itemDiv.tabIndex = 0;
                    itemDiv.setAttribute('data-word-id', wordId);
                    itemDiv.setAttribute('data-word-text', wordText);
                    
                    itemDiv.innerHTML = `
                        <div class="d-flex align-items-center">
                            <i class="bi bi-check-circle me-2 text-primary"></i>
                            <span class="word-text">${escapeHtml(wordText)}</span>
                            ${usageCount > 0 ? `<small class="text-muted ms-2">(${usageCount} files)</small>` : ''}
                        </div>
                    `;
                    
                    itemDiv.addEventListener('click', function() {
                        selectWordForKeyword(wordId, wordText);
                    });
                    
                    itemDiv.addEventListener('keydown', function(e) {
                        if (e.key === 'Enter' || e.key === ' ') {
                            e.preventDefault();
                            selectWordForKeyword(wordId, wordText);
                        } else if (e.key === 'ArrowDown') {
                            e.preventDefault();
                            const next = wordResults.querySelectorAll('.word-result-item')[index + 1];
                            if (next) {
                                itemDiv.classList.remove('active');
                                next.focus();
                                next.classList.add('active');
                            }
                        } else if (e.key === 'ArrowUp') {
                            e.preventDefault();
                            if (index > 0) {
                                const prev = wordResults.querySelectorAll('.word-result-item')[index - 1];
                                itemDiv.classList.remove('active');
                                if (prev) {
                                    prev.focus();
                                    prev.classList.add('active');
                                } else {
                                    wordInput.focus();
                                }
                            }
                        }
                    });
                    
                    wordResults.appendChild(itemDiv);
                });
            }
            
            // Show option to create new word if search term doesn't match exactly
            const exactMatch = results.some(item => {
                const wordText = (item.text || item.word || '').toLowerCase();
                return wordText === searchTerm.toLowerCase();
            });
            
            if (!exactMatch && searchTerm.length > 0 && !searchTerm.match(/^\d+$/)) {
                const createDiv = document.createElement('div');
                createDiv.className = 'word-result-item word-result-create';
                createDiv.tabIndex = 0;
                createDiv.setAttribute('data-word-id', 'new');
                createDiv.setAttribute('data-word-text', searchTerm);
                
                createDiv.innerHTML = `
                    <div class="d-flex align-items-center">
                        <i class="bi bi-plus-circle me-2 text-success"></i>
                        <span class="word-text">${escapeHtml(searchTerm)}</span>
                        <small class="text-muted ms-2">(create new)</small>
                    </div>
                `;
                
                createDiv.addEventListener('click', function() {
                    selectWordForKeyword('new', searchTerm);
                });
                
                createDiv.addEventListener('keydown', function(e) {
                    if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        selectWordForKeyword('new', searchTerm);
                    }
                });
                
                wordResults.appendChild(createDiv);
            }
            
            wordResults.style.display = 'block';
        }
        
        // Select word function
        async function selectWordForKeyword(wordId, wordText) {
            if (wordId === 'new') {
                // Create new word
                try {
                    showToast('Creating new word...', 'info');
                    const csrfToken = getCSRFToken() || await getCSRFTokenAsync();
                    const response = await fetch('/api/words', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-CSRFToken': csrfToken
                        },
                        body: JSON.stringify({
                            word: wordText.trim(),
                            csrf_token: csrfToken
                        })
                    });
                    
                    const data = await response.json();
                    if (data.success) {
                        showToast('Word created successfully', 'success');
                        wordId = data.id;
                    } else {
                        throw new Error(data.error || 'Failed to create word');
                    }
                } catch (error) {
                    console.error('Error creating word:', error);
                    showToast('Error creating word: ' + error.message, 'error');
                    return;
                }
            }
            
            // Add to selected words
            if (!selectedWords.some(w => w.text === wordText)) {
                selectedWords.push({ id: wordId, text: wordText });
                updateSelectedWordsDisplay();
            }
            
            wordInput.value = '';
            wordResults.style.display = 'none';
            wordInput.focus();
        }
        
        // Update selected words display
        function updateSelectedWordsDisplay() {
            const selectedWordsList = document.getElementById('selectedWordsList');
            const selectedWordsContainer = document.getElementById('selectedWordsContainer');
            
            if (!selectedWordsList || !selectedWordsContainer) return;
            
            if (selectedWords.length === 0) {
                selectedWordsContainer.style.display = 'none';
                selectedWordsList.innerHTML = '';
                return;
            }
            
            selectedWordsContainer.style.display = 'block';
            selectedWordsList.innerHTML = selectedWords.map((word, index) => `
                <span class="badge bg-primary d-flex align-items-center gap-1" style="font-size: 0.875rem; padding: 0.375rem 0.75rem;">
                    ${escapeHtml(word.text)}
                    <button type="button" class="btn-close btn-close-white" style="font-size: 0.6rem;" data-on-click="removeSelectedWord(${index})" aria-label="Remove"></button>
                </span>
            `).join('');
        }
        
        // Remove selected word
        window.removeSelectedWord = function(index) {
            selectedWords.splice(index, 1);
            updateSelectedWordsDisplay();
        };
        
        // Search categories function
        async function searchCategoriesForKeyword(searchTerm) {
            if (!searchTerm || searchTerm.length === 0) {
                categoryResults.style.display = 'none';
                return;
            }
            
            try {
                const params = new URLSearchParams({
                    q: searchTerm,
                    page: 1,
                    per_page: 20
                });
                
                const response = await fetch(`/api/categories/search?${params.toString()}`);
                const data = await response.json();
                
                if (data.results && data.results.length > 0) {
                    renderCategoryResultsForKeyword(data.results, searchTerm);
                } else {
                    renderCategoryResultsForKeyword([], searchTerm);
                }
            } catch (error) {
                console.error('Error searching categories:', error);
                categoryResults.style.display = 'none';
            }
        }
        
        // Render category results
        function renderCategoryResultsForKeyword(results, searchTerm) {
            categoryResults.innerHTML = '';
            
            if (results.length > 0) {
                results.forEach((item, index) => {
                    const categoryId = item.id;
                    const categoryName = item.name || '';
                    const fileCount = item.file_count || 0;
                    
                    const itemDiv = document.createElement('div');
                    itemDiv.className = 'word-result-item';
                    itemDiv.tabIndex = 0;
                    itemDiv.setAttribute('data-category-id', categoryId);
                    itemDiv.setAttribute('data-category-name', categoryName);
                    
                    itemDiv.innerHTML = `
                        <div class="d-flex align-items-center">
                            <i class="bi bi-check-circle me-2 text-primary"></i>
                            <span class="word-text">${escapeHtml(categoryName)}</span>
                            ${fileCount > 0 ? `<small class="text-muted ms-2">(${fileCount} files)</small>` : ''}
                        </div>
                    `;
                    
                    itemDiv.addEventListener('click', function() {
                        selectCategoryForKeyword(categoryId, categoryName);
                    });
                    
                    itemDiv.addEventListener('keydown', function(e) {
                        if (e.key === 'Enter' || e.key === ' ') {
                            e.preventDefault();
                            selectCategoryForKeyword(categoryId, categoryName);
                        } else if (e.key === 'ArrowDown') {
                            e.preventDefault();
                            const next = categoryResults.querySelectorAll('.word-result-item')[index + 1];
                            if (next) {
                                itemDiv.classList.remove('active');
                                next.focus();
                                next.classList.add('active');
                            }
                        } else if (e.key === 'ArrowUp') {
                            e.preventDefault();
                            if (index > 0) {
                                const prev = categoryResults.querySelectorAll('.word-result-item')[index - 1];
                                itemDiv.classList.remove('active');
                                if (prev) {
                                    prev.focus();
                                    prev.classList.add('active');
                                } else {
                                    categoryInput.focus();
                                }
                            }
                        }
                    });
                    
                    categoryResults.appendChild(itemDiv);
                });
            }
            
            // Show option to create new category if search term doesn't match exactly
            const exactMatch = results.some(item => {
                const categoryName = (item.name || '').toLowerCase();
                return categoryName === searchTerm.toLowerCase();
            });
            
            if (!exactMatch && searchTerm.length > 0 && !searchTerm.match(/^\d+$/)) {
                const createDiv = document.createElement('div');
                createDiv.className = 'word-result-item word-result-create';
                createDiv.tabIndex = 0;
                createDiv.setAttribute('data-category-id', 'new');
                createDiv.setAttribute('data-category-name', searchTerm);
                
                createDiv.innerHTML = `
                    <div class="d-flex align-items-center">
                        <i class="bi bi-plus-circle me-2 text-success"></i>
                        <span class="word-text">${escapeHtml(searchTerm)}</span>
                        <small class="text-muted ms-2">(create new)</small>
                    </div>
                `;
                
                createDiv.addEventListener('click', function() {
                    selectCategoryForKeyword('new', searchTerm);
                });
                
                createDiv.addEventListener('keydown', function(e) {
                    if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        selectCategoryForKeyword('new', searchTerm);
                    }
                });
                
                categoryResults.appendChild(createDiv);
            }
            
            categoryResults.style.display = 'block';
        }
        
        // Select category function
        async function selectCategoryForKeyword(categoryId, categoryName) {
            if (categoryId === 'new') {
                // Create new category
                try {
                    showToast('Creating new category...', 'info');
                    const csrfToken = getCSRFToken() || await getCSRFTokenAsync();
                    const response = await fetch('/category/add', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-CSRFToken': csrfToken
                        },
                        body: JSON.stringify({
                            category_name: categoryName.trim(),
                            csrf_token: csrfToken
                        })
                    });
                    
                    const data = await response.json();
                    if (data.success) {
                        showToast('Category created successfully', 'success');
                        categoryId = data.category_id;
                    } else {
                        throw new Error(data.error || 'Failed to create category');
                    }
                } catch (error) {
                    console.error('Error creating category:', error);
                    showToast('Error creating category: ' + error.message, 'error');
                    return;
                }
            }
            
            selectedCategoryId = categoryId;
            selectedCategoryText = categoryName;
            categoryInput.value = categoryName;
            if (selectedCategoryIdInput) selectedCategoryIdInput.value = categoryId;
            categoryResults.style.display = 'none';
            categoryInput.focus();
        }
        
        // Show modal and initialize
        modal.show();
        
        // Wait for modal to be fully shown
        modalElement.addEventListener('shown.bs.modal', function onShown() {
            modalElement.removeEventListener('shown.bs.modal', onShown);
            initializeWordSearch();
            initializeCategorySearch();
            
            // Add click outside handler
            const wordInputContainer = wordInput.closest('.position-relative') || wordInput.parentElement;
            const categoryInputContainer = categoryInput.closest('.position-relative') || categoryInput.parentElement;
            
            keywordClickOutsideHandler = function(e) {
                const target = e.target;
                if (wordInputContainer && wordResults && 
                    !wordInputContainer.contains(target) && 
                    !wordResults.contains(target)) {
                    wordResults.style.display = 'none';
                }
                if (categoryInputContainer && categoryResults && 
                    !categoryInputContainer.contains(target) && 
                    !categoryResults.contains(target)) {
                    categoryResults.style.display = 'none';
                }
            };
            setTimeout(() => {
                document.addEventListener('click', keywordClickOutsideHandler, true);
            }, 100);
        }, { once: true });
        
        // Clean up when modal is hidden
        modalElement.addEventListener('hidden.bs.modal', function onHidden() {
            // ✅ FIXED: Reset modal to "Add" mode when closed/cancelled
            resetKeywordModalToAdd();
            
            // Clean up event listeners
            if (keywordClickOutsideHandler) {
                document.removeEventListener('click', keywordClickOutsideHandler, true);
                keywordClickOutsideHandler = null;
            }
            
            // Clear search timeouts
            if (keywordWordSearchTimeout) {
                clearTimeout(keywordWordSearchTimeout);
                keywordWordSearchTimeout = null;
            }
            if (keywordCategorySearchTimeout) {
                clearTimeout(keywordCategorySearchTimeout);
                keywordCategorySearchTimeout = null;
            }
            
            // Remove input event listeners
            if (keywordWordInputHandler && wordInput) {
                wordInput.removeEventListener('input', keywordWordInputHandler);
                keywordWordInputHandler = null;
            }
            if (keywordCategoryInputHandler && categoryInput) {
                categoryInput.removeEventListener('input', keywordCategoryInputHandler);
                keywordCategoryInputHandler = null;
            }
            if (keywordWordKeydownHandler && wordInput) {
                wordInput.removeEventListener('keydown', keywordWordKeydownHandler);
                keywordWordKeydownHandler = null;
            }
            if (keywordCategoryKeydownHandler && categoryInput) {
                categoryInput.removeEventListener('keydown', keywordCategoryKeydownHandler);
                keywordCategoryKeydownHandler = null;
            }
            
            // Reset form
            if (wordInput) wordInput.value = '';
            if (categoryInput) categoryInput.value = '';
            if (selectedCategoryIdInput) selectedCategoryIdInput.value = '';
            if (wordResults) {
                wordResults.style.display = 'none';
                wordResults.innerHTML = '';
            }
            if (categoryResults) {
                categoryResults.style.display = 'none';
                categoryResults.innerHTML = '';
            }
            if (selectedWordsList) selectedWordsList.innerHTML = '';
            if (selectedWordsContainer) selectedWordsContainer.style.display = 'none';
            
            // Reset state
            selectedWords = [];
            selectedCategoryId = null;
            selectedCategoryText = null;
        }, { once: true });
    }
    
    // Save keyword function
    async function saveKeyword() {
        if (selectedWords.length === 0) {
            showToast(translations.atLeastOneWordRequired || 'At least one word is required', 'warning');
            return;
        }
        
        if (selectedWords.length < 2) {
            showToast(translations.keywordRequiresMultipleWords || 'Keywords must contain at least 2 words. Please select multiple words to create a keyword phrase.', 'warning');
            return;
        }
        
        if (editingKeywordId) {
            await submitKeywordUpdate(editingKeywordId);
            return;
        }

        const keywordPhrase = selectedWords.map(w => w.text).join(' ');
        const categoryId = selectedCategoryId || '1';
        
        // Check for duplicate
        try {
            const csrfToken = getCSRFToken() || await getCSRFTokenAsync();
            const checkResponse = await fetch('/api/keyword/check', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfToken
                },
                body: JSON.stringify({
                    keyword_text: keywordPhrase,
                    category_id: categoryId
                })
            });
            
            const checkData = await checkResponse.json();
            if (checkData.exists) {
                showToast(checkData.message || `Keyword "${keywordPhrase}" already exists in this category`, 'error');
                return;
            }
        } catch (error) {
            console.warn('Error checking keyword duplicate:', error);
        }
        
        // Submit keyword
        try {
            showToast('Adding keyword...', 'info');
            const csrfToken = getCSRFToken() || await getCSRFTokenAsync();
            const formData = new FormData();
            formData.append('keywords_text', keywordPhrase);
            formData.append('category_id', categoryId);
            
            const response = await fetch('/keywords/add', {
                method: 'POST',
                headers: {
                    'X-CSRFToken': csrfToken,
                    'X-Requested-With': 'XMLHttpRequest'
                },
                body: formData,
                redirect: 'follow'
            });
            
            // Check response content type
            const contentType = response.headers.get('content-type') || '';
            
            let data;
            if (contentType.includes('application/json')) {
                // JSON response
                data = await response.json();
            } else {
                // HTML response (redirect happened) - treat as success
                // The server logs show it was successful, so we'll treat redirect as success
                data = { success: true, message: translations.keywordAddedSuccessfully || 'Keyword added successfully' };
            }
            if (data.success) {
                showToast(translations.keywordAddedSuccessfully || 'Keyword added successfully', 'success');
                
                // Close modal
                const modalElement = document.getElementById('addKeywordModal');
                if (modalElement) {
                    const modal = bootstrap.Modal.getInstance(modalElement);
                    if (modal) {
                        modal.hide();
                    }
                }
                
                // Refresh page
                setTimeout(() => {
                    fetchPage(currentPage);
                }, 500);
            } else {
                showToast(data.error || translations.error || 'Error adding keyword', 'error');
            }
        } catch (error) {
            console.error('Error adding keyword:', error);
            showToast('Error adding keyword: ' + error.message, 'error');
        }
    }

    async function submitKeywordUpdate(keywordId) {
        try {
            const csrfToken = getCSRFToken() || await getCSRFTokenAsync();
            const response = await fetch(`/api/keywords/${keywordId}`, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfToken
                },
                body: JSON.stringify({
                    word_ids: selectedWords.map(w => Number(w.id)),
                    category_id: selectedCategoryId ? Number(selectedCategoryId) : null
                })
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok || !data.success) {
                throw new Error(data.error || `HTTP ${response.status}`);
            }
            showToast(translations.keywordUpdatedSuccessfully || 'Keyword updated successfully', 'success');
            const modalElement = document.getElementById('addKeywordModal');
            const modal = modalElement && bootstrap.Modal.getInstance(modalElement);
            if (modal) modal.hide();
            selectedKeywords.clear();
            updateSelection();
            fetchPage(currentPage);
        } catch (error) {
            console.error('Error updating keyword:', error);
            showToast((translations.errorUpdatingKeyword || 'Error updating keyword') + ': ' + error.message, 'error');
        }
    }

    // Expose functions for global access
    window.updateSelection = updateSelection;
    window.updateBulkButtons = updateBulkButtons;  // ✅ FIXED: Expose updateBulkButtons
    window.toggleSelectAll = toggleSelectAll;
    window.selectAll = selectAll;
    window.selectNone = selectNone;
    window.toggleAllSelection = toggleAllSelection;
    window.applyFilters = applyFilters;
    window.changePageSize = changePageSize;
    window.clearSearch = clearSearch;
    window.bulkDelete = bulkDelete;
    window.bulkUpdate = bulkUpdate;
    window.editKeywordInModal = editKeywordInModal;
    window.resetKeywordModalToAdd = resetKeywordModalToAdd;
    window.exportKeywords = exportKeywords;
    window.mergeAllDuplicates = mergeAllDuplicates;
    window.openAddKeywordModal = openAddKeywordModal;
    window.saveKeyword = saveKeyword;

    // Initialize the application
    init();

    // AUDIT (UI-01): /keywords/add used to render a missing template (500).
    // It now redirects here with ?add=1 (optionally ?keyword_id=N for edit),
    // so open the existing modal instead of a second, parallel add page.
    (function openAddFromQueryString() {
        try {
            const params = new URLSearchParams(window.location.search);
            if (params.get('add') !== '1') return;
            const keywordId = parseInt(params.get('keyword_id'), 10);
            params.delete('add');
            params.delete('keyword_id');
            const rest = params.toString();
            history.replaceState(history.state, '', window.location.pathname + (rest ? '?' + rest : ''));
            setTimeout(() => {
                if (keywordId && typeof editKeywordInModal === 'function') {
                    editKeywordInModal(keywordId);
                } else {
                    openAddKeywordModal();
                }
            }, 250);
        } catch (e) {
            console.warn('Could not auto-open the keyword modal:', e);
        }
    })();
})();