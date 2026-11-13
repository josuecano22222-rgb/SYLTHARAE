/**
 * Categories List Page JavaScript
 *
 * The categories list is a server-sorted, server-paged table now: the page
 * keeps the category modal, the add-word modal, the duplicate finder and the
 * row actions, and asks the server to search, order and page the list by
 * navigating with query parameters. The unified table script owns the header
 * sort (default server navigation); this page owns everything a table row
 * does.
 */

// Use window.translations to avoid conflicts with other scripts
if (typeof window.translations === 'undefined') {
    window.translations = {};
}

// Local translations object (merged with window.translations)
var translations = window.translations;

// Load translations from JSON script tag
document.addEventListener('DOMContentLoaded', function() {
    // The documents side panel is the shared component; this page only
    // points it at its table id.
    if (window.DocumentsPanel) {
        window.DocumentsPanel.wire({ panelId: 'documentsPanel', tableId: 'categoryFilesTable' });
    }

    const pageDataEl = document.getElementById('categories-page-data');
    if (pageDataEl) {
        try {
            const jsonText = pageDataEl.textContent.trim();
            if (jsonText) {
                const data = JSON.parse(jsonText);
                Object.assign(window.translations, data.translations || {});
                translations = window.translations;
            }
        } catch (e) {
            console.error('Error parsing categories page data:', e);
            translations = window.translations || {};
        }
    }

    initializeEventListeners();
    console.log('Categories list page loaded');
});

// Initialize event listeners
function initializeEventListeners() {
    // Search: navigates with ?search=, which re-renders the list server-side
    const searchInput = document.getElementById('categorySearch');
    if (searchInput) {
        searchInput.addEventListener('keydown', function(e) {
            if (e.key === 'Enter') {
                e.preventDefault();
                navigateCategories({ search: searchInput.value.trim(), page: '1' });
            }
        });
    }

    // Event delegation for add word buttons
    document.addEventListener('click', function(e) {
        const addWordBtn = e.target.closest('.add-word-btn');
        if (addWordBtn) {
            e.preventDefault();
            const categoryId = parseInt(addWordBtn.getAttribute('data-category-id'));
            let categoryName = addWordBtn.getAttribute('data-category-name');

            // Parse JSON string if needed
            try {
                if (categoryName && (categoryName.startsWith('"') || categoryName.startsWith("'"))) {
                    categoryName = JSON.parse(categoryName);
                }
            } catch (e) {
                // Use as-is if parsing fails
            }

            addWordToCategory(categoryId, categoryName);
        }
    });
}

// Navigate to this list with changed query parameters; everything not named
// here (sort, order, per page) is carried over, so a sorted view stays
// sorted while the reader searches.
function navigateCategories(changes) {
    const params = new URLSearchParams(window.location.search);
    Object.entries(changes || {}).forEach(([name, value]) => {
        if (value === undefined || value === null || value === '') {
            params.delete(name);
        } else {
            params.set(name, value);
        }
    });
    if (window.swapNavigate) { window.swapNavigate(window.location.pathname + '?' + params.toString()); } else { window.location.href = window.location.pathname + '?' + params.toString(); }
}

// The per-page selector in the table toolbar
function changeCategoriesPageSize() {
    const perPage = document.getElementById('categoriesTablePerPage');
    if (perPage) {
        navigateCategories({ per_page: perPage.value, page: '1' });
    }
}

// Clear the search box and the search itself
function clearSearch() {
    const searchInput = document.getElementById('categorySearch');
    if (searchInput) searchInput.value = '';
    navigateCategories({ search: '', page: '1' });
}

// Debounce helper
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

// CSRF token helper functions
function getCSRFToken() {
    const metaTag = document.querySelector('meta[name="csrf-token"]');
    return metaTag ? metaTag.getAttribute('content') : '';
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

// Toast notification helper
function showToast(message, type = 'info', duration = 4000) {
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
    const toast = new bootstrap.Toast(toastElement, { delay: duration });
    toast.show();
    
    toastElement.addEventListener('hidden.bs.toast', () => {
        toastElement.remove();
    });
}

// Open category modal
function openCategoryModal(categoryId = null) {
    if (typeof bootstrap === 'undefined') {
        console.error('Bootstrap not loaded yet');
        setTimeout(() => openCategoryModal(categoryId), 100);
        return;
    }
    const modalElement = document.getElementById('categoryModal');
    if (!modalElement) {
        console.error('Category modal not found');
        return;
    }
    const modal = new bootstrap.Modal(modalElement);
    const form = document.getElementById('categoryForm');
    const title = document.getElementById('categoryModalLabel');
    const categoryIdInput = document.getElementById('categoryId');
    const categoryNameInput = document.getElementById('categoryName');
    
    if (categoryId) {
        title.textContent = translations.editCategory || 'Edit Category';
        categoryIdInput.value = categoryId;
        // Load category data
        fetch(`/api/categories/${categoryId}`)
            .then(response => response.json())
            .then(data => {
                if (data && data.name) {
                    categoryNameInput.value = data.name;
                }
            })
            .catch(error => {
                console.error('Error loading category:', error);
            });
    } else {
        title.textContent = translations.addCategory || 'Add Category';
        categoryIdInput.value = '';
        categoryNameInput.value = '';
    }

    form.reset();
    // The parent and adopt selectors are filled from the hierarchy API, so the
    // modal offers the same tree the hierarchy page shows instead of a flat
    // list of names that would let an operator pick a level that does not exist.
    loadCategoryChoices();
    syncCategoryMode();
    categoryModalGeneration += 1;
    modal.show();
}

/**
 * Fill the parent and adopt selectors from the tree, indented by level.
 *
 * The endpoint pages at 500 rows; the selector is meant for the ordinary
 * catalogue, and a deeper catalogue is served by the hierarchy page. If the
 * fetch fails the modal still works in "top level" mode, which is why the
 * failure only logs.
 */
function loadCategoryChoices() {
    const parent = document.getElementById('categoryParentSelect');
    const adopt = document.getElementById('categoryAdoptSelect');
    if (!parent && !adopt) { return; }

    fetch('/api/categories/tree?limit=500&total=0', {headers: {'Accept': 'application/json'}})
        .then(function (response) {
            if (!response.ok) { throw new Error('HTTP ' + response.status); }
            return response.json();
        })
        .then(function (payload) {
            const rows = payload.rows || [];
            if (parent) {
                parent.textContent = '';
                const none = document.createElement('option');
                none.value = '';
                none.textContent = translations.none || 'None';
                parent.appendChild(none);
                rows.forEach(function (row) {
                    const option = document.createElement('option');
                    option.value = String(row.id);
                    option.textContent = '\u00A0\u00A0'.repeat(Math.max(0, row.depth - 1)) + row.name;
                    parent.appendChild(option);
                });
            }
            if (adopt) {
                adopt.textContent = '';
                // Only the rows drawn on this page can be adopted from here:
                // the table is server-paged and the operator can see those.
                // The rows carry data-category-id wherever the table macro puts
                // them; the fallback keeps this working if the table is
                // re-rendered without a tbody wrapper.
                const rows = document.querySelectorAll('#categoriesTable tr[data-category-id]');
                (rows.length ? rows : document.querySelectorAll('tr[data-category-id]'))
                    .forEach(function (tr) {
                        const option = document.createElement('option');
                        option.value = tr.dataset.categoryId;
                        option.textContent = tr.dataset.name;
                        adopt.appendChild(option);
                    });
            }
        })
        .catch(function (error) {
            console.warn('Could not load category choices:', error);
        });
}

function currentCategoryMode() {
    const checked = document.querySelector('input[name="categoryMode"]:checked');
    return checked ? checked.value : 'root';
}

function syncCategoryMode() {
    const mode = currentCategoryMode();
    const parentGroup = document.getElementById('categoryParentGroup');
    const adoptGroup = document.getElementById('categoryAdoptGroup');
    const childrenGroup = document.getElementById('categoryChildrenGroup');
    const editing = (document.getElementById('categoryId') || {}).value;
    if (parentGroup) { parentGroup.style.display = (mode === 'child' && !editing) ? '' : 'none'; }
    if (adoptGroup) { adoptGroup.style.display = (mode === 'parent-above' && !editing) ? '' : 'none'; }
    if (childrenGroup) {
        childrenGroup.style.display = (mode === 'parent-above' || editing) ? 'none' : '';
    }
}

document.addEventListener('change', function (event) {
    if (event.target && event.target.name === 'categoryMode') { syncCategoryMode(); }
});

// Save category
/**
 * Close the add-category dialog, forcing the presentation state if needed.
 *
 * The bundled Bootstrap reports "not shown" while the dialog and its backdrop
 * are still in the DOM, so ``hide()`` alone leaves the dialog covering the
 * list. See the same helper in categories-tree-page.js.
 */
var categoryModalGeneration = 0;

function closeCategoryModal() {
    const element = document.getElementById('categoryModal');
    if (!element || typeof bootstrap === 'undefined') { return; }
    const generation = categoryModalGeneration;
    try {
        bootstrap.Modal.getOrCreateInstance(element).hide();
    } catch (error) {
        console.warn('modal hide failed:', error);
    }
    window.setTimeout(function () {
        if (generation !== categoryModalGeneration) { return; }
        if (!element.classList.contains('show')) { return; }
        element.classList.remove('show');
        element.setAttribute('aria-hidden', 'true');
        element.style.display = 'none';
        document.querySelectorAll('.modal-backdrop').forEach(function (backdrop) {
            backdrop.remove();
        });
        document.body.classList.remove('modal-open');
        document.body.style.removeProperty('padding-right');
    }, 400);
}

/**
 * POST JSON and turn the server's refusal into the message shown to the user.
 *
 * The hierarchy API answers a refused operation with ``{error, code}`` (409 for
 * a sibling-name clash or a cycle, 400 for a malformed body); surfacing the
 * server's text keeps the rule where it is enforced.
 */
async function postJson(url, body, csrfToken) {
    const response = await fetch(url, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken
        },
        body: JSON.stringify(body)
    });
    let payload = null;
    try { payload = await response.json(); } catch (error) { payload = null; }
    if (!response.ok) {
        throw new Error((payload && payload.error) || ('HTTP ' + response.status));
    }
    return payload;
}

async function saveCategory() {
    const form = document.getElementById('categoryForm');
    const formData = new FormData(form);
    const categoryId = formData.get('category_id');
    const categoryName = formData.get('category_name').trim();
    
    if (!categoryName) {
        showToast('Category name is required', 'error');
        return;
    }
    
    // Check for duplicate before submitting
    try {
        let csrfToken = getCSRFToken();
        if (!csrfToken) {
            csrfToken = await getCSRFTokenAsync();
        }
        
        const checkResponse = await fetch('/api/category/check', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken
            },
            body: JSON.stringify({
                category_name: categoryName
            })
        });
        
        const checkData = await checkResponse.json();
        if (checkData.exists) {
            showToast(checkData.message || `Category "${categoryName}" already exists`, 'error');
            return;
        }
    } catch (error) {
        console.warn('Error checking category duplicate:', error);
        // Continue with submission if check fails (backend will catch it)
    }
    
    // Get CSRF token
    let csrfToken = getCSRFToken();
    if (!csrfToken) {
        csrfToken = await getCSRFTokenAsync();
    }
    
    if (!csrfToken) {
        showToast('Unable to obtain CSRF token. Please refresh the page.', 'error');
        return;
    }

    // Renaming keeps the historic form endpoint; creating a category now says
    // where it goes, which is the hierarchy API's job.
    if (!categoryId) {
        const mode = currentCategoryMode();
        const body = {name: categoryName, mode: mode === 'parent-above' ? 'root' : mode};
        try {
            if (mode === 'parent-above') {
                const adopt = document.getElementById('categoryAdoptSelect');
                const ids = adopt ? Array.from(adopt.selectedOptions).map(function (o) {
                    return Number(o.value);
                }) : [];
                if (!ids.length) {
                    showToast(translations.adoptRequired || 'Choose at least one category to move', 'error');
                    return;
                }
                await postJson('/api/categories/tree/parent-above',
                    {name: categoryName, child_ids: ids}, csrfToken);
            } else {
                if (mode === 'child') {
                    const parent = document.getElementById('categoryParentSelect');
                    if (!parent || !parent.value) {
                        showToast(translations.parentRequired || 'Choose a parent category', 'error');
                        return;
                    }
                    body.parent_id = Number(parent.value);
                }
                const childrenField = document.getElementById('categoryChildren');
                const lines = childrenField ? childrenField.value.split('\n')
                    .map(function (line) { return line.trim(); })
                    .filter(Boolean) : [];
                if (lines.length) { body.children = lines; }
                await postJson('/api/categories/tree', body, csrfToken);
            }
            showToast(translations.categoryAdded || 'Category added successfully', 'success');
            closeCategoryModal();
            setTimeout(function () {
                if (window.swapNavigate) { window.swapNavigate(window.location.href); } else { window.location.reload(); }
            }, 400);
        } catch (error) {
            showToast(error.message, 'error');
        }
        return;
    }

    const url = '/category/add';
    const method = 'POST';
    
    fetch(url, {
        method: method,
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken
        },
        body: JSON.stringify({
            category_name: categoryName,
            category_id: categoryId,
            csrf_token: csrfToken
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            showToast(data.message || translations.categoryAdded || 'Category added successfully', 'success');
            const modalElement = document.getElementById('categoryModal');
            if (modalElement && typeof bootstrap !== 'undefined') {
                const modal = bootstrap.Modal.getInstance(modalElement);
                if (modal) modal.hide();
            }
            // Add new category to the list immediately
            if (data.category_id) {
                allCategories.push({
                    id: data.category_id,
                    name: categoryName,
                    file_count: 0,
                    word_count: 0
                });
                applyFiltersAndRender();
            } else {
                // Fallback to page reload if no ID returned
                setTimeout(() => {
                    if (window.swapNavigate) { window.swapNavigate(window.location.href); } else { window.location.reload(); }
                }, 500);
            }
        } else {
            showToast(data.error || translations.error || 'Error', 'error');
        }
    })
    .catch(error => {
        console.error('Error saving category:', error);
        showToast('Error saving category: ' + error.message, 'error');
    });
}

// View words in category
function viewCategoryWords(categoryId, categoryName) {
    if (window.swapNavigate) { window.swapNavigate(`/categories/${categoryId}/words`); } else { window.location.href = `/categories/${categoryId}/words`; }
}

// Add word to category
function addWordToCategory(categoryId, categoryName) {
    const modalElement = document.getElementById('addWordToCategoryModal');
    if (!modalElement) {
        console.error('Add word modal not found');
        return;
    }
    
    const modal = new bootstrap.Modal(modalElement);
    const title = document.getElementById('addWordToCategoryModalLabel');
    const categoryIdInput = document.getElementById('targetCategoryId');
    const wordSelect = document.getElementById('wordSelect');
    
    if (!title || !categoryIdInput || !wordSelect) {
        console.error('Required modal elements not found');
        return;
    }
    
    // Handle JSON-encoded category name
    let displayName = categoryName;
    try {
        if (typeof categoryName === 'string' && (categoryName.startsWith('"') || categoryName.startsWith("'"))) {
            displayName = JSON.parse(categoryName);
        }
    } catch (e) {
        displayName = categoryName;
    }
    
    title.textContent = `${translations.addWordToCategory || 'Add Word to Category'}: ${escapeHtml(displayName)}`;
    categoryIdInput.value = categoryId;
    
    // Show modal - Bootstrap will handle aria-hidden automatically
    modal.show();
    
    // Initialize Select2 for word selection - wait for libraries to be ready
    function initializeSelect2() {
        if (typeof jQuery === 'undefined' || typeof jQuery.fn === 'undefined') {
            // jQuery not ready yet, try again
            setTimeout(initializeSelect2, 50);
            return;
        }
        
        if (typeof jQuery.fn.select2 === 'undefined') {
            // Select2 not loaded yet, try again
            setTimeout(initializeSelect2, 50);
            return;
        }
        
        // Destroy existing Select2 if it exists
        if ($(wordSelect).hasClass('select2-hidden-accessible')) {
            $(wordSelect).select2('destroy');
        }
        
        // Wait a bit for modal to be fully visible
        setTimeout(function() {
            $(wordSelect).select2({
                placeholder: translations.searchWord || 'Search for a word or type to create new...',
                allowClear: true,
                width: '100%',
                minimumInputLength: 0,
                dropdownParent: $(modalElement),
                tags: true, // Allow creating new tags/words
                createTag: function (params) {
                    const term = params.term.trim();
                    if (term === '') {
                        return null;
                    }
                    // Don't create tag if it matches an existing option
                    if (params.term.match(/^\d+$/)) {
                        return null; // Don't allow pure numbers as new words
                    }
                    return {
                        id: 'new:' + term,
                        text: term + ' (new)',
                        isNew: true
                    };
                },
                ajax: {
                    url: '/api/words/search',
                    dataType: 'json',
                    delay: 300,
                    data: function (params) {
                        const categoryIdInput = document.getElementById('targetCategoryId');
                        const categoryId = categoryIdInput ? parseInt(categoryIdInput.value) : null;
                        const requestData = {
                            q: params.term || '',
                            page: params.page || 1,
                            per_page: 20
                        };
                        // Exclude words already in this category
                        if (categoryId) {
                            requestData.exclude_category_id = categoryId;
                        }
                        return requestData;
                    },
                    processResults: function (data, params) {
                        params.page = params.page || 1;
                        
                        const results = (data.results || []).map(function(item) {
                            const wordId = item.id || item.word_id;
                            const wordText = item.text || item.word || String(wordId || '');
                            
                            return {
                                id: wordId,
                                text: wordText,
                                usage_count: item.usage_count || 0
                            };
                        });
                        
                        return {
                            results: results,
                            pagination: {
                                more: (params.page * 20) < (data.pagination?.total || 0)
                            }
                        };
                    },
                    cache: true
                },
                templateResult: function (data) {
                    if (data.loading) {
                        return data.text || 'Searching...';
                    }
                    
                    if (data.isNew) {
                        const $result = $('<span><i class="bi bi-plus-circle me-1"></i>' + escapeHtml(data.text.replace(' (new)', '')) + ' <small class="text-muted">(create new)</small></span>');
                        return $result;
                    }
                    
                    const $result = $('<span>' + escapeHtml(data.text) + '</span>');
                    if (data.usage_count && data.usage_count > 0) {
                        $result.append(' <small class="text-muted">(' + data.usage_count + ' files)</small>');
                    }
                    return $result;
                },
                templateSelection: function (data) {
                    if (typeof data === 'string') {
                        return data;
                    }
                    if (data && data.text) {
                        return data.text.replace(' (new)', '');
                    }
                    if (data && data.id) {
                        return data.id.toString().startsWith('new:') ? data.id.replace('new:', '') : data.id;
                    }
                    return data || '';
                },
                escapeMarkup: function (markup) {
                    return markup;
                }
            });
            
            // Clear selection
            $(wordSelect).val(null).trigger('change');
        }, 200);
    }
    
    // Start initialization
    initializeSelect2();
}

// Create a new word
async function createNewWord(wordText) {
    const csrfToken = getCSRFToken() || await getCSRFTokenAsync();
    if (!csrfToken) {
        throw new Error('Unable to obtain CSRF token');
    }
    
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
        return data.id;
    } else {
        throw new Error(data.error || 'Failed to create word');
    }
}

// Save word to category
async function saveWordToCategory() {
    const categoryIdInput = document.getElementById('targetCategoryId');
    const wordSelect = document.getElementById('wordSelect');
    
    if (!categoryIdInput || !wordSelect) {
        showToast('Form elements not found', 'error');
        return;
    }
    
    const categoryId = categoryIdInput.value;
    
    // Get word ID or new word text - handle both Select2 and regular select
    let wordIdOrText;
    if (typeof jQuery !== 'undefined' && $(wordSelect).hasClass('select2-hidden-accessible')) {
        wordIdOrText = $(wordSelect).val();
    } else {
        wordIdOrText = wordSelect.value;
    }
    
    if (!wordIdOrText) {
        showToast('Please select or enter a word', 'warning');
        return;
    }
    
    // Check if this is a new word (starts with "new:")
    let wordId;
    if (typeof wordIdOrText === 'string' && wordIdOrText.startsWith('new:')) {
        // This is a new word, create it first
        const wordText = wordIdOrText.replace('new:', '');
        try {
            showToast('Creating new word...', 'info');
            wordId = await createNewWord(wordText);
            showToast('Word created successfully', 'success');
        } catch (error) {
            console.error('Error creating word:', error);
            showToast('Error creating word: ' + error.message, 'error');
            return;
        }
    } else {
        // Existing word, use the ID
        wordId = parseInt(wordIdOrText);
        if (isNaN(wordId)) {
            showToast('Invalid word selection', 'error');
            return;
        }
    }
    
    // Get CSRF token
    let csrfToken = getCSRFToken();
    if (!csrfToken) {
        csrfToken = await getCSRFTokenAsync();
    }
    
    if (!csrfToken) {
        showToast('Unable to obtain CSRF token. Please refresh the page.', 'error');
        return;
    }
    
    fetch('/api/words-categorys/add', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken
        },
        body: JSON.stringify({
            word_id: parseInt(wordId),
            category_id: parseInt(categoryId),
            csrf_token: csrfToken
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            showToast(data.message || translations.wordAdded || 'Word added to category successfully', 'success');
            const modalElement = document.getElementById('addWordToCategoryModal');
            if (modalElement) {
                const modal = bootstrap.Modal.getInstance(modalElement);
                if (modal) {
                    // Clear Select2 before closing
                    const wordSelect = document.getElementById('wordSelect');
                    if (wordSelect && typeof jQuery !== 'undefined' && $(wordSelect).hasClass('select2-hidden-accessible')) {
                        $(wordSelect).val(null).trigger('change');
                    }
                    modal.hide();
                }
            }
            // Refresh the page to update counts
            setTimeout(() => {
                if (window.swapNavigate) { window.swapNavigate(window.location.href); } else { window.location.reload(); }
            }, 500);
        } else {
            showToast(data.error || translations.error || 'Error', 'error');
        }
    })
    .catch(error => {
        console.error('Error adding word to category:', error);
        showToast('Error adding word to category: ' + error.message, 'error');
    });
}

// Delete category
async function deleteCategory(categoryId, categoryName) {
    // CAT-UI-01: resolve the display name from the row/card when the caller
    // (inline onclick) can only pass the id — avoids quoting category names
    // into HTML attributes.
    if (!categoryName) {
        categoryName = document.querySelector(`[data-category-id="${categoryId}"]`)?.dataset.name || '';
    }
    if (!confirm(translations.confirmDelete || `Are you sure you want to delete the category "${categoryName}"?`)) {
        return;
    }
    
    // Get CSRF token
    let csrfToken = getCSRFToken();
    if (!csrfToken) {
        csrfToken = await getCSRFTokenAsync();
    }
    
    if (!csrfToken) {
        showToast('Unable to obtain CSRF token. Please refresh the page.', 'error');
        return;
    }
    
    fetch(`/api/categories/${categoryId}`, {
        method: 'DELETE',
        headers: {
            'X-CSRFToken': csrfToken
        }
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            showToast(data.message || translations.categoryDeleted || 'Category deleted successfully', 'success');
            setTimeout(() => {
                if (window.swapNavigate) { window.swapNavigate(window.location.href); } else { window.location.reload(); }
            }, 500);
        } else {
            showToast(data.error || translations.error || 'Error', 'error');
        }
    })
    .catch(error => {
        console.error('Error deleting category:', error);
        showToast('Error deleting category: ' + error.message, 'error');
    });
}

// Helper function to escape HTML
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Find duplicate categories
async function findDuplicates() {
    const modal = new bootstrap.Modal(document.getElementById('duplicatesModal'));
    const loadingEl = document.getElementById('duplicatesLoading');
    const contentEl = document.getElementById('duplicatesContent');
    const emptyEl = document.getElementById('duplicatesEmpty');
    const removeBtn = document.getElementById('removeDuplicatesBtn');
    const summaryEl = document.getElementById('duplicatesSummary');
    const listEl = document.getElementById('duplicatesList');
    
    // Show modal and loading state
    modal.show();
    loadingEl.style.display = 'block';
    contentEl.style.display = 'none';
    emptyEl.style.display = 'none';
    removeBtn.style.display = 'none';
    
    try {
        const response = await fetch('/api/categories/find-duplicates');
        const data = await response.json();
        
        loadingEl.style.display = 'none';
        
        if (!data.success) {
            showToast(data.error || translations.errorFindingDuplicates || 'Error finding duplicates', 'error');
            return;
        }
        
        if (data.total_duplicates === 0) {
            emptyEl.style.display = 'block';
            removeBtn.style.display = 'none';
            return;
        }
        
        // Show duplicates
        contentEl.style.display = 'block';
        removeBtn.style.display = 'block';
        
        // Update summary
        summaryEl.innerHTML = `
            <strong>${translations.totalDuplicates || 'Total Duplicates'}:</strong> ${data.total_duplicates} ${translations.duplicateGroup || 'groups'}<br>
            <strong>${translations.totalToRemove || 'Total to Remove'}:</strong> ${data.total_to_remove} ${translations.categories || 'categories'}
        `;
        
        // Build duplicates list
        listEl.innerHTML = '';
        data.duplicates.forEach((dup, index) => {
            const item = document.createElement('div');
            item.className = 'list-group-item';
            item.innerHTML = `
                <div class="d-flex justify-content-between align-items-start">
                    <div class="flex-grow-1">
                        <h6 class="mb-2">
                            <span class="badge bg-warning me-2">${index + 1}</span>
                            <strong>${escapeHtml(dup.name)}</strong>
                            <span class="badge bg-secondary ms-2">${dup.count} ${translations.categories || 'categories'}</span>
                        </h6>
                        <div class="mt-2">
                            <small class="text-muted">
                                <strong>${translations.keep || 'Keep'}:</strong> ID ${dup.keep_id}<br>
                                <strong>${translations.remove || 'Remove'}:</strong> IDs ${dup.remove_ids.join(', ')}
                            </small>
                        </div>
                    </div>
                </div>
            `;
            listEl.appendChild(item);
        });
        
    } catch (error) {
        console.error('Error finding duplicates:', error);
        loadingEl.style.display = 'none';
        showToast(translations.errorFindingDuplicates || 'Error finding duplicates', 'error');
    }
}

// Remove duplicate categories
async function removeDuplicates() {
    if (!confirm(translations.confirmRemoveDuplicates || 'Are you sure you want to remove all duplicate categories? This action cannot be undone.')) {
        return;
    }
    
    const removeBtn = document.getElementById('removeDuplicatesBtn');
    const originalText = removeBtn.innerHTML;
    removeBtn.disabled = true;
    removeBtn.innerHTML = `<span class="spinner-border spinner-border-sm me-2"></span>${translations.removing || 'Removing...'}`;
    
    try {
        const response = await fetch('/api/categories/remove-duplicates', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCSRFToken()
            }
        });
        
        const data = await response.json();
        
        removeBtn.disabled = false;
        removeBtn.innerHTML = originalText;
        
        if (!data.success) {
            showToast(data.error || translations.errorRemovingDuplicates || 'Error removing duplicates', 'error');
            return;
        }
        
        let message = data.message || `${translations.duplicatesRemoved || 'Duplicates removed successfully'}: ${data.removed} ${translations.categories || 'categories'}`;
        if (data.words_merged && data.words_merged > 0) {
            message += ` (${data.words_merged} ${translations.words || 'words'} ${translations.merged || 'merged'})`;
        }
        showToast(message, 'success');
        
        // Close modal and reload page
        const modal = bootstrap.Modal.getInstance(document.getElementById('duplicatesModal'));
        if (modal) {
            modal.hide();
        }
        
        setTimeout(() => {
            if (window.swapNavigate) { window.swapNavigate(window.location.href); } else { window.location.reload(); }
        }, 1000);
        
    } catch (error) {
        console.error('Error removing duplicates:', error);
        removeBtn.disabled = false;
        removeBtn.innerHTML = originalText;
        showToast(translations.errorRemovingDuplicates || 'Error removing duplicates', 'error');
    }
}

// Make functions available globally
window.openCategoryModal = openCategoryModal;
window.viewCategoryWords = viewCategoryWords;
window.addWordToCategory = addWordToCategory;
window.saveCategory = saveCategory;
window.saveWordToCategory = saveWordToCategory;
window.deleteCategory = deleteCategory;
window.changeCategoriesPageSize = changeCategoriesPageSize;
window.clearSearch = clearSearch;
window.findDuplicates = findDuplicates;
window.removeDuplicates = removeDuplicates;
