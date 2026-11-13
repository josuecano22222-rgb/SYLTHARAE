/**
 * Words List Page JavaScript
 * Extracted from Word/Word_list.html
 */

// Load translations from JSON script tag
let translations = {};

function notify(message, type = 'info', duration = 5000) {
    const tone = type === 'error' ? 'error' : type === 'success' ? 'success' : type === 'warning' ? 'warning' : 'info';
    if (window.Toast && typeof window.Toast.show === 'function') {
        window.Toast.show({ tone: tone, message: String(message), timeout: duration, dismissible: true });
        return;
    }
    if (typeof window.showToast === 'function') {
        window.showToast(message, type, duration);
        return;
    }
    console[type === 'error' ? 'error' : 'log'](message);
}

// The universal initializer imports this module and runs init() on a full
// load AND on every in-application navigation (the static sidebar), so a
// revisit always shows the current view. One dispatcher, no self-run.
function loadPageTranslations() {
    // Load translations from JSON script tag
    const pageDataEl = document.getElementById('words-list-page-data');
    if (pageDataEl) {
        try {
            const data = JSON.parse(pageDataEl.textContent);
            translations = data.translations || {};
            // Also make available on window for backward compatibility
            window.translations = window.translations || {};
            Object.assign(window.translations, translations);
        } catch (e) {
            console.error('Error parsing words list page data:', e);
        }
    }

    console.log('Words list page loaded');
}

    const tableBody = document.getElementById('wordsTableBody');
    const searchInput = document.getElementById('searchWords');
    const statusFilter = document.getElementById('statusFilter');
    const perPageSelect = document.getElementById('perPage');
    const selectAllCheckbox = document.getElementById('selectAllCheckbox');
    const TABLE_ID = 'wordsTable';
    const COLUMN_COUNT = 6;
    
    let currentPage = 1;
    let currentPerPage = 10;
    // Seed from the URL so a server-rendered, sorted view (the sort/order
    // query params the headers wrote) is the state the page starts in.
    const SORTABLE_COLUMNS = ['usage_count', 'word', 'id', 'status'];
    const initialParams = new URLSearchParams(window.location.search);
    const sortFromUrl = initialParams.get('sort');
    const orderFromUrl = initialParams.get('order');
    let currentSort = SORTABLE_COLUMNS.includes(sortFromUrl) ? sortFromUrl : 'usage_count';
    let currentOrder = orderFromUrl === 'asc' ? 'asc' : 'desc';
    let selectedWords = new Set();
    let wordModal = null;
    let editWordId = null;
    
    // Initialize Bootstrap modal
    function initializeModal() {
        const modalElement = document.getElementById('wordModal');
        if (modalElement) {
            if (typeof bootstrap !== 'undefined') {
                try {
                    wordModal = new bootstrap.Modal(modalElement);
                } catch (e) {
                    console.error('Error initializing Bootstrap modal:', e);
                }
            } else {
                // Bootstrap not loaded yet, try again after a short delay
                setTimeout(initializeModal, 100);
            }
        }
    }
    
    // Initialize
    function init() {
        loadPageTranslations();
        const urlParams = new URLSearchParams(window.location.search);
        currentPage = parseInt(urlParams.get('page')) || 1;
        
        if (perPageSelect) {
            currentPerPage = parseInt(perPageSelect.value) || 10;
        }
        
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
        
        // The server already rendered this page. A second /api/words
        // fetch was replacing those rows with "Loading…" on every visit.
        const ssrRows = tableBody ? tableBody.querySelectorAll('tr[data-word-id], tbody tr .ut-name').length : 0;
        if (tableBody && !ssrRows) {
            fetchPage(currentPage);
        }
        
        setupEventListeners();
        
        // Initialize Bootstrap modal - ensure it's available
        initializeModal();

        // AUDIT (UI-01): /words/add used to render a missing template (500).
        // It now redirects here with ?add=1, so open the existing modal.
        if (urlParams.get('add') === '1') {
            setTimeout(() => openAddWordModal(), 200);
        }
    }
    
    function updateSortIcons() {
        // The header is the one sort control; the unified table owns how the
        // decision is drawn (indicator, aria-sort).
        if (window.UnifiedTable) {
            window.UnifiedTable.setSort(TABLE_ID, currentSort, currentOrder);
        }
    }
    
    function setupEventListeners() {
        let searchTimeout;
        
        if (searchInput) {
            searchInput.addEventListener('input', () => {
                clearTimeout(searchTimeout);
                searchTimeout = setTimeout(() => {
                    currentPage = 1;
                    fetchPage(1);
                }, 300);
            });
        }
        
        if (perPageSelect) {
            perPageSelect.addEventListener('change', () => {
                currentPerPage = parseInt(perPageSelect.value);
                currentPage = 1;
                fetchPage(1);
            });
        }
    }
    
    function renderRows(words, page) {
        if (!tableBody || !window.UnifiedTable) return;

        if (!words || words.length === 0) {
            const query = searchInput?.value?.trim() || '';
            const message = query ?
                `${translations.noWordsFound} "${query}".` :
                translations.noWordsYet;

            UnifiedTable.renderRows(tableBody, [
                UnifiedTable.states.empty(COLUMN_COUNT, { message, filtered: Boolean(query) })
            ]);
            return;
        }

        // The same row the server renders, drawn with the same pieces: one
        // join, one insertion, a hundred rows for one reflow.
        const rows = [];
        words.forEach((word, idx) => {
            if (!word || !word.id) return;

            const globalIndex = ((page - 1) * currentPerPage) + (idx + 1);
            const usage = word.usage_count || 0;
            const active = usage > 0;
            rows.push(`
                <tr data-word-id="${word.id}" data-usage-count="${usage}" data-status="${active ? 'active' : 'unused'}">
                    <td class="ut-col-select">
                        <input type="checkbox" class="word-checkbox ut-row-check"
                               value="${word.id}" data-on-change="updateBulkButtons()"
                               aria-label="${translations.editWord || 'Word'}: ${UnifiedTable.fmt.escapeHtml(word.word || '')}">
                    </td>
                    <td class="ut-index-cell"><span class="ut-badge ut-badge-neutral">${globalIndex}</span></td>
                    <td><span class="ut-name" data-word-id="${word.id}">${UnifiedTable.fmt.escapeHtml(word.word || '')}</span></td>
                    <td class="text-end" data-ut-value="${usage}">${usage}</td>
                    <td data-ut-value="${active ? 'active' : 'unused'}">${active
                        ? `<span class="badge bg-success"><i class="bi bi-check-circle me-1" aria-hidden="true"></i>${translations.active}</span>`
                        : `<span class="badge bg-secondary"><i class="bi bi-dash-circle me-1" aria-hidden="true"></i>${translations.unused}</span>`}</td>
                    <td>
                        <div class="btn-group btn-group-sm ut-actions">
                            <button class="btn btn-outline-primary" data-on-click="viewWord(${word.id})" title="${translations.viewDetails || 'View Details'}">
                                <i class="bi bi-eye"></i>
                            </button>
                            <button class="btn btn-outline-warning" data-on-click="editWord(${word.id})" title="${translations.editWord || 'Edit'}">
                                <i class="bi bi-pencil"></i>
                            </button>
                            <button class="btn btn-outline-danger" data-on-click="deleteWord(${word.id})" title="${translations.deleteWord || 'Delete'}">
                                <i class="bi bi-trash"></i>
                            </button>
                        </div>
                    </td>
                </tr>`);
        });
        UnifiedTable.renderRows(tableBody, rows);

        // Update stats
        const activeCount = words.filter(w => w.usage_count > 0).length;
        const unusedCount = words.filter(w => w.usage_count === 0).length;
        const activeEl = document.getElementById('activeCount');
        const unusedEl = document.getElementById('unusedCount');
        if (activeEl) activeEl.textContent = activeCount;
        if (unusedEl) unusedEl.textContent = unusedCount;
    }
    
    function renderPaginator(page, total_pages, loadMore = {}) {
        // Find or create pagination container
        let paginationContainer = document.querySelector('.pagination-container');
        if (!paginationContainer) {
            paginationContainer = createPaginationContainer();
        }
        
        if (!paginationContainer) {
            console.error('Could not create pagination container');
            return;
        }
        
        // Ensure container has an ID
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
            module.renderUnifiedPagination({
                currentPage: page,
                totalPages: total_pages,
                // One control: the table's Load More is rendered by the pager.
                tableId: 'wordsTable',
                totalCount: loadMore.total ?? 0,
                rowsShown: loadMore.shown ?? 0,
                containerId: paginationContainer.id,
                onPageChange: (targetPage) => {
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
        }).catch(err => {
            console.error('Error loading unified pagination:', err);
            // Fallback to old pagination if module fails to load
            renderOldPaginator(page, total_pages, paginationContainer);
        });
    }
    
    // Fallback old pagination renderer
    function renderOldPaginator(page, total_pages, paginationContainer) {
        paginationContainer.innerHTML = '';
        
        let pageInfo = paginationContainer.querySelector('.pagination-info');
        if (!pageInfo) {
            pageInfo = document.createElement('div');
            pageInfo.className = 'small text-muted pagination-info';
            paginationContainer.insertBefore(pageInfo, paginationContainer.firstChild);
        }
        pageInfo.textContent = `${translations.page} ${page} ${translations.of} ${total_pages}`;
        
        let pagContainer = paginationContainer.querySelector('ul.pagination');
        if (!pagContainer) {
            pagContainer = createPaginationList();
            paginationContainer.appendChild(pagContainer);
        }
        pagContainer.innerHTML = '';
        
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
        // The server template renders the mount; this is only a fallback.
        const wordsTable = document.getElementById('wordsTable');
        if (!wordsTable) return null;

        const unit = wordsTable.closest('.ut');
        if (!unit || !unit.parentNode) return null;

        let container = unit.parentNode.querySelector('.pagination-container');
        if (container) return container;

        container = document.createElement('div');
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
        window.history.replaceState({}, '', url.pathname + '?' + url.searchParams.toString());
    }

    let pendingFetch = null;
    function fetchPage(page=1) {
        currentPage = page;
        if (pendingFetch) pendingFetch.abort();
        const controller = new AbortController();
        pendingFetch = controller;

        const hasRows = tableBody && tableBody.querySelector('tr[data-word-id], tr .ut-name');
        if (tableBody && window.UnifiedTable && !hasRows) {
            UnifiedTable.renderRows(tableBody, [
                UnifiedTable.states.loading(COLUMN_COUNT, translations.loading || 'Loading...')
            ]);
        }
        
        // Get API URL from page data or use default
        const pageDataEl = document.getElementById('words-list-page-data');
        let apiUrl = '/api/words';
        if (pageDataEl) {
            try {
                const data = JSON.parse(pageDataEl.textContent);
                apiUrl = data.api_url || '/api/words';
            } catch (e) {
                console.warn('Error parsing page data, using default API URL');
            }
        }
        
        const url = new URL(apiUrl, window.location.origin);
        url.searchParams.set('page', page);
        url.searchParams.set('per_page', currentPerPage);
        if (currentSort) url.searchParams.set('sort', currentSort);
        if (currentOrder) url.searchParams.set('order', currentOrder);
        
        if (searchInput && searchInput.value.trim()) url.searchParams.set('q', searchInput.value.trim());
        if (statusFilter && statusFilter.value) url.searchParams.set('status', statusFilter.value);
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
                if (!r.ok) throw new Error(`HTTP error! status: ${r.status}`);
                return r.json();
            })
            .then(json => {
                if (!tableBody) return;

                if (!json || json.success === false) {
                    if (window.UnifiedTable) {
                        UnifiedTable.renderRows(tableBody, [
                            UnifiedTable.states.error(COLUMN_COUNT,
                                `${translations.errorLoadingWords || 'Error loading words'}: ${json?.error || 'Invalid response'}`)
                        ]);
                    }
                    return;
                }

                if (json.per_page) {
                    currentPerPage = json.per_page;
                    if (perPageSelect) perPageSelect.value = json.per_page;
                }

                // The server decides the order; the headers show its answer.
                // No sort in the answer means the default order: cleared.
                currentSort = json.sort_by || null;
                currentOrder = json.sort_by ? (json.sort_order || null) : null;
                updateSortIcons();
                
                const words = json.words || [];
                const pageNum = json.page || 1;
                const totalPages = json.total_pages || 1;
                
                renderRows(words, pageNum);
                renderPaginator(pageNum, totalPages, { total: json.total, shown: words.length });
                updateSearchInfo(json);
                syncListQuery();

                // STALE-01: keep the server-rendered "Total Words" stat card
                // in sync after client-side pagination/mutations.
                const totalStat = document.getElementById('totalWordsStat');
                if (totalStat && typeof json.total === 'number') {
                    totalStat.textContent = json.total;
                }
            })
            .catch(err => {
                if (err.name === 'AbortError') return;
                console.error('Fetch error', err);
                if (tableBody && window.UnifiedTable) {
                    UnifiedTable.renderRows(tableBody, [
                        UnifiedTable.states.error(COLUMN_COUNT,
                            `${translations.errorLoadingWords || 'Error loading words'}: ${err.message}`)
                    ]);
                }
            })
            .finally(() => { if (pendingFetch === controller) pendingFetch = null; });
    }
    
    function updateSearchInfo(json) {
        const infoEl = document.getElementById('searchResultsInfo');
        if (infoEl) {
            const start = ((json.page - 1) * json.per_page) + 1;
            const end = start + ((json.words || []).length) - 1;
            const total = json.total || 0;
            infoEl.textContent = `${translations.showing} ${start}–${end} ${translations.of} ${total} ${translations.words}`;
        }
    }
    
    // The page owns the selection (which rows, and the set the operations
    // send); the toolbar owns how that scope is drawn and announced. So this
    // reports the numbers and nothing else: it never enables or disables a
    // button, and it never names an action.
    function updateBulkButtons() {
        const checkboxes = document.querySelectorAll('.word-checkbox:checked');
        selectedWords.clear();
        checkboxes.forEach(cb => selectedWords.add(parseInt(cb.value)));

        const allCheckboxes = document.querySelectorAll('.word-checkbox');
        if (selectAllCheckbox) {
            selectAllCheckbox.checked = allCheckboxes.length > 0 && checkboxes.length === allCheckboxes.length;
            selectAllCheckbox.indeterminate = checkboxes.length > 0 && checkboxes.length < allCheckboxes.length;
        }

        if (window.ActionToolbar) {
            window.ActionToolbar.sync('wordsActionBar', {
                selected: checkboxes.length,
                total: allCheckboxes.length
            });
        }
    }
    
    function toggleSelectAll() {
        const isChecked = selectAllCheckbox.checked;
        document.querySelectorAll('.word-checkbox').forEach(cb => {
            cb.checked = isChecked;
        });
        updateBulkButtons();
    }
    
    function selectAll() {
        document.querySelectorAll('.word-checkbox').forEach(cb => {
            cb.checked = true;
        });
        updateBulkButtons();
    }
    
    function selectNone() {
        document.querySelectorAll('.word-checkbox').forEach(cb => {
            cb.checked = false;
        });
        updateBulkButtons();
    }

    /** One control: select every row when empty, clear when any are selected. */
    function toggleAllSelection() {
        const anySelected = document.querySelectorAll('.word-checkbox:checked').length > 0;
        if (anySelected) {
            selectNone();
        } else {
            selectAll();
        }
    }
    
    function applyFilters() {
        currentPage = 1;
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
    
    function openAddWordModal() {
        // Get modal element - try both possible IDs
        let modalElement = document.getElementById('wordModal');
        if (!modalElement) {
            modalElement = document.getElementById('addWordToCategoryModal');
        }
        if (!modalElement) {
            // Modal doesn't exist on this page, silently return
            return;
        }
        
        // Check if Bootstrap is available
        if (typeof bootstrap === 'undefined') {
            console.error('Bootstrap is not loaded');
            return;
        }
        
        // Create modal instance (create new each time to avoid timing issues)
        let modal;
        try {
            modal = new bootstrap.Modal(modalElement);
        } catch (e) {
            console.error('Error creating Bootstrap modal:', e);
            return;
        }
        
        // Reset form and set title
        editWordId = null;
        const wordInput = document.getElementById('wordInput');
        const wordModalLabel = document.getElementById('wordModalLabel');
        
        if (wordInput) wordInput.value = '';
        if (wordModalLabel) wordModalLabel.innerHTML = '<i class="bi bi-book"></i> ' + translations.addWord;
        
        // Show modal
        try {
            modal.show();
            // Also update the stored reference for consistency
            wordModal = modal;
        } catch (e) {
            console.error('Error showing modal:', e);
        }
    }
    
    function editWord(wordId) {
        fetch(`/api/words/${wordId}`)
            .then(response => response.json())
            .then(data => {
                if (data.success && data.word) {
                    const modalElement = document.getElementById('wordModal');
                    if (!modalElement || typeof bootstrap === 'undefined') {
                        notify(translations.errorLoadingWords, 'error');
                        return;
                    }
                    
                    editWordId = wordId;
                    document.getElementById('wordInput').value = data.word.word;
                    document.getElementById('wordModalLabel').innerHTML = '<i class="bi bi-pencil"></i> ' + translations.editWord;
                    
                    // Create and show modal
                    try {
                        const modal = new bootstrap.Modal(modalElement);
                        modal.show();
                        wordModal = modal; // Store for consistency
                    } catch (e) {
                        console.error('Error showing edit modal:', e);
                        notify(translations.errorLoadingWords, 'error');
                    }
                } else {
                    notify(translations.errorLoadingWords, 'error');
                }
            })
            .catch(error => {
                console.error('Error loading word:', error);
                notify(translations.errorLoadingWords, 'error');
            });
    }
    
    function submitWord() {
        const wordText = document.getElementById('wordInput').value.trim();
        if (!wordText) {
            notify(translations.wordRequired, 'warning');
            return;
        }
        
        const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
        const url = editWordId ? `/api/words/${editWordId}` : '/api/words';
        const method = editWordId ? 'PUT' : 'POST';
        
        fetch(url, {
            method: method,
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken
            },
            body: JSON.stringify({ word: wordText })
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                // Hide modal
                const modalElement = document.getElementById('wordModal');
                if (modalElement && typeof bootstrap !== 'undefined') {
                    try {
                        if (wordModal) {
                            wordModal.hide();
                        } else {
                            const modal = bootstrap.Modal.getInstance(modalElement);
                            if (modal) modal.hide();
                        }
                    } catch (e) {
                        console.error('Error hiding modal:', e);
                    }
                }
                notify(editWordId ? translations.wordUpdatedSuccessfully : translations.wordAddedSuccessfully, 'success');
                fetchPage(currentPage);
            } else {
                notify(data.error || translations.error, 'error');
            }
        })
        .catch(error => {
            console.error('Error:', error);
            notify(translations.error, 'error');
        });
    }
    
    function bulkDelete() {
        if (selectedWords.size === 0) return;
        
        if (!confirm(`${selectedWords.size} ${translations.deleteSelectedWords}`)) return;
        
        const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
        fetch('/api/words/bulk-delete', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken
            },
            body: JSON.stringify({ word_ids: Array.from(selectedWords) })
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                notify(`${translations.successfullyDeleted} ${data.deleted_count} ${translations.words}`, 'success');
                if (window.swapNavigate) { window.swapNavigate(window.location.pathname + '?t=' + Date.now()); } else { window.location.href = window.location.pathname + '?t=' + Date.now(); }
            } else {
                notify(translations.errorDeletingWords + ': ' + (data.error || translations.error), 'error');
            }
        })
        .catch(error => {
            console.error('Error:', error);
            notify(translations.errorDeletingWords, 'error');
        });
    }
    
    function bulkUpdate() {
        if (selectedWords.size === 0) return;
        
        if (selectedWords.size === 1) {
            const wordId = Array.from(selectedWords)[0];
            editWord(wordId);
            return;
        }
        
        if (confirm(`Multiple words selected. Edit the first word?`)) {
            const wordId = Array.from(selectedWords)[0];
            editWord(wordId);
        }
    }
    
    // Global functions
    window.viewWord = function(id) {
        if (window.swapNavigate) { window.swapNavigate(`/words/${id}`); } else { window.location.href = `/words/${id}`; }
    };
    
    window.deleteWord = function(id) {
        if (!confirm(translations.deleteWordConfirm + id + '?')) return;
        const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
        fetch(`/api/words/${id}`, {
            method: 'DELETE',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken
            }
        })
        .then(r => r.json())
        .then(j => {
            if (j.success) {
                notify(translations.wordDeletedSuccessfully, 'success');
                if (window.swapNavigate) { window.swapNavigate(window.location.pathname + '?t=' + Date.now()); } else { window.location.href = window.location.pathname + '?t=' + Date.now(); }
            } else {
                notify(j.error || translations.failedToDelete, 'error');
            }
        })
        .catch(e => notify(translations.error + ': ' + e.message, 'error'));
    };
    
    window.updateBulkButtons = updateBulkButtons;
    // The selection path under its original name as well: the keyword and
    // word pages share one contract name with the toolbar harness.
    window.updateSelection = updateBulkButtons;
    window.toggleSelectAll = toggleSelectAll;
    window.selectAll = selectAll;
    window.selectNone = selectNone;
    window.toggleAllSelection = toggleAllSelection;
    window.applyFilters = applyFilters;
    window.changePageSize = changePageSize;
    window.clearSearch = clearSearch;
    window.bulkDelete = bulkDelete;
    window.bulkUpdate = bulkUpdate;
    window.openAddWordModal = openAddWordModal;
    window.editWord = editWord;
    window.submitWord = submitWord;
    
    // Wait for DOM and Bootstrap to be ready - optimized with better checks
    function waitForBootstrap(callback, maxAttempts = 10) {
        const modalElement = document.getElementById('wordModal');
        if (typeof bootstrap !== 'undefined' && (modalElement || maxAttempts <= 5)) {
            callback();
        } else if (maxAttempts > 0) {
            setTimeout(() => waitForBootstrap(callback, maxAttempts - 1), 50);
        } else {
            // Bootstrap might not be needed if modal doesn't exist
            console.warn('Bootstrap not available after waiting, initializing anyway');
            callback();
        }
    }
    
export { init };
