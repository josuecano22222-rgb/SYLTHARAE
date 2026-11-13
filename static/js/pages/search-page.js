/**
 * Search Page JavaScript
 * Extracted from Search/search.html
 * 
 * Handles client-side enhancements for the basic search page
 * Now includes advanced search options support
 */

import { apiGet } from '../modules/api/api-client.js';
import { endpoints } from '../modules/api/endpoints.js';

document.addEventListener('DOMContentLoaded', function() {
    console.log('Search page loaded');
    
    // Get search form and input
    const searchForm = document.getElementById('searchForm');
    const searchInput = document.getElementById('searchQuery');
    
    if (!searchForm || !searchInput) {
        console.warn('Search form or input not found');
        return;
    }
    
    // Handle form submission with advanced search options
    searchForm.addEventListener('submit', async function(e) {
        e.preventDefault();
        
        const query = searchInput.value.trim();
        if (query.length < 2) {
            if (window.MessageSystem && typeof window.MessageSystem.show === 'function') {
                window.MessageSystem.show(
                    'Search query must be at least 2 characters long',
                    'warning',
                    { duration: 3000 }
                );
            } else {
                alert('Search query must be at least 2 characters long');
            }
            return false;
        }
        
        // Perform search using enhanced API
        await performEnhancedSearch(query);
    });
    
    // Handle Enter key in search input
    searchInput.addEventListener('keypress', function(e) {
        if (e.key === 'Enter') {
            // Form submission will be handled by the form submit handler
        }
    });
    
    // Add focus to search input if it's empty
    if (!searchInput.value || searchInput.value.trim() === '') {
        setTimeout(() => {
            searchInput.focus();
        }, 100);
    }
    
    // If there's a query in the URL and results are displayed, use enhanced search
    const urlParams = new URLSearchParams(window.location.search);
    const queryParam = urlParams.get('q');
    if (queryParam && queryParam.trim().length >= 2) {
        // Check if results are already displayed (server-side rendered)
        const resultsContainer = document.getElementById('searchResults');
        if (resultsContainer && resultsContainer.querySelector('.list-group')) {
            // Results are already displayed, just highlight terms
            highlightSearchTerms();
        } else {
            // No results displayed, perform enhanced search
            performEnhancedSearch(queryParam);
        }
    } else {
        // Highlight search terms in results (if any results are displayed)
        highlightSearchTerms();
    }
});

/**
 * Perform enhanced search using the API
 */
async function performEnhancedSearch(query) {
    const resultsContainer = document.getElementById('searchResults');
    if (!resultsContainer) return;
    
    // Show loading
    resultsContainer.innerHTML = '<div class="text-center py-5"><div class="spinner-border" role="status"><span class="visually-hidden">Loading...</span></div></div>';
    
    try {
        // Get advanced search options
        const advancedOptions = getAdvancedSearchOptions();
        
        // Build search parameters
        const params = {
            query: query,
            page: 1,
            per_page: 10,
            sort_by: 'relevance',
            sort_order: 'desc',
            use_fulltext: 'true',
            use_advanced: advancedOptions.use_advanced ? 'true' : 'false',
            use_bm25: advancedOptions.use_bm25 ? 'true' : 'false',
            use_expansion: advancedOptions.use_expansion ? 'true' : 'false',
            use_fuzzy: advancedOptions.use_fuzzy ? 'true' : 'false',
            case_sensitive: advancedOptions.case_sensitive ? 'true' : 'false',
            whole_word: advancedOptions.whole_word ? 'true' : 'false'
        };
        
        const apiUrl = endpoints.search(params);
        const data = await apiGet(apiUrl);
        
        // Display results
        displaySearchResults(data, query);
        
        // Update URL without page reload
        const url = new URL(window.location);
        url.searchParams.set('q', query);
        window.history.pushState({}, '', url);
        
    } catch (error) {
        console.error('Search error:', error);
        resultsContainer.innerHTML = `
            <div class="empty-state">
                <i class="bi bi-exclamation-triangle"></i>
                <p>Error performing search: ${escapeHtml(error.message || 'Unknown error')}</p>
            </div>
        `;
    }
}

/**
 * Get advanced search options from form
 */
function getAdvancedSearchOptions() {
    return {
        use_advanced: document.getElementById('useAdvanced')?.checked !== false,
        use_bm25: document.getElementById('useBM25')?.checked !== false,
        use_expansion: document.getElementById('useExpansion')?.checked !== false,
        use_fuzzy: document.getElementById('useFuzzy')?.checked !== false,
        case_sensitive: document.getElementById('caseSensitive')?.checked === true,
        whole_word: document.getElementById('wholeWord')?.checked === true
    };
}

/**
 * Display search results
 */
function displaySearchResults(data, query) {
    const resultsContainer = document.getElementById('searchResults');
    if (!resultsContainer) return;
    
    const results = data.results || [];
    const totalResults = data.pagination?.total || 0;
    
    if (results.length === 0) {
        resultsContainer.innerHTML = `
            <div class="empty-state">
                <i class="bi bi-search"></i>
                <p>No results found for "${escapeHtml(query)}"</p>
            </div>
        `;
        return;
    }
    
    let html = `
        <div class="list-group list-group-flush">
    `;
    
    results.forEach(result => {
        const fileId = result.id;
        const fileName = result.file_name || 'Unknown';
        const fileType = result.file_type || '';
        const sourceName = result.source_name || '';
        const sideName = result.side_name || '';
        const fileDate = result.file_date || '';
        
        html += `
            <a href="/file/${fileId}" class="list-group-item list-group-item-action">
                <div class="d-flex justify-content-between align-items-start">
                    <div>
                        <h6 class="mb-1">
                            <i class="bi bi-file-earmark me-2"></i>
                            ${escapeHtml(fileName)}
                        </h6>
                        <small class="text-muted">
                            ${sourceName ? `Source: ${escapeHtml(sourceName)} | ` : ''}
                            ${sideName ? `Side: ${escapeHtml(sideName)} | ` : ''}
                            Date: ${fileDate || 'N/A'}
                        </small>
                    </div>
                    <span class="badge bg-secondary">${escapeHtml(fileType)}</span>
                </div>
            </a>
        `;
    });
    
    html += '</div>';
    
    // Add pagination if needed
    if (data.pagination && data.pagination.total_pages > 1) {
        html += `
            <nav aria-label="Search results pagination" class="mt-3">
                <ul class="pagination justify-content-center">
                    <li class="page-item ${!data.pagination.has_prev ? 'disabled' : ''}">
                        <a class="page-link" href="#" data-on-click="goToPage(${data.pagination.page - 1}); return false;">Previous</a>
                    </li>
                    ${Array.from({length: data.pagination.total_pages}, (_, i) => i + 1)
                        .filter(page => page === 1 || page === data.pagination.total_pages || 
                                (page >= data.pagination.page - 2 && page <= data.pagination.page + 2))
                        .map(page => `
                            <li class="page-item ${page === data.pagination.page ? 'active' : ''}">
                                <a class="page-link" href="#" data-on-click="goToPage(${page}); return false;">${page}</a>
                            </li>
                        `).join('')}
                    <li class="page-item ${!data.pagination.has_next ? 'disabled' : ''}">
                        <a class="page-link" href="#" data-on-click="goToPage(${data.pagination.page + 1}); return false;">Next</a>
                    </li>
                </ul>
            </nav>
        `;
    }
    
    resultsContainer.innerHTML = html;
    
    // Highlight search terms
    highlightSearchTerms();
}

/**
 * Go to specific page
 */
async function goToPage(page) {
    const searchInput = document.getElementById('searchQuery');
    if (!searchInput) return;
    
    const query = searchInput.value.trim();
    if (!query) return;
    
    const resultsContainer = document.getElementById('searchResults');
    if (!resultsContainer) return;
    
    // Show loading
    resultsContainer.innerHTML = '<div class="text-center py-5"><div class="spinner-border" role="status"><span class="visually-hidden">Loading...</span></div></div>';
    
    try {
        const advancedOptions = getAdvancedSearchOptions();
        const params = {
            query: query,
            page: page,
            per_page: 10,
            sort_by: 'relevance',
            sort_order: 'desc',
            use_fulltext: 'true',
            use_advanced: advancedOptions.use_advanced ? 'true' : 'false',
            use_bm25: advancedOptions.use_bm25 ? 'true' : 'false',
            use_expansion: advancedOptions.use_expansion ? 'true' : 'false',
            use_fuzzy: advancedOptions.use_fuzzy ? 'true' : 'false',
            case_sensitive: advancedOptions.case_sensitive ? 'true' : 'false',
            whole_word: advancedOptions.whole_word ? 'true' : 'false'
        };
        
        const apiUrl = endpoints.search(params);
        const data = await apiGet(apiUrl);
        
        displaySearchResults(data, query);
        window.scrollTo({ top: 0, behavior: 'smooth' });
        
    } catch (error) {
        console.error('Search error:', error);
    }
}

/**
 * Highlight search terms in search results
 */
function highlightSearchTerms() {
    const searchInput = document.getElementById('searchQuery');
    const query = searchInput ? searchInput.value.trim() : new URLSearchParams(window.location.search).get('q');
    
    if (!query || query.trim().length < 2) {
        return;
    }
    
    const resultsContainer = document.querySelector('.list-group');
    if (!resultsContainer) {
        return;
    }
    
    // Split query into terms for multi-word highlighting
    const searchTerms = query.trim().toLowerCase().split(/\s+/);
    const resultItems = resultsContainer.querySelectorAll('.list-group-item h6');
    
    resultItems.forEach(item => {
        let highlightedText = item.innerHTML;
        
        searchTerms.forEach(term => {
            if (term.length >= 2) {
                const regex = new RegExp(`(${escapeRegex(term)})`, 'gi');
                highlightedText = highlightedText.replace(regex, '<mark>$1</mark>');
            }
        });
        
        if (highlightedText !== item.innerHTML) {
            item.innerHTML = highlightedText;
        }
    });
}

/**
 * Escape special regex characters
 */
function escapeRegex(str) {
    return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/**
 * Default initialization function for universal-initializer
 */
export default async function init() {
    // The DOMContentLoaded event handler above will handle initialization
    // This function is called by universal-initializer
    console.log('Search page module initialized');
}

// Export functions for use as module if needed
export {
    performEnhancedSearch,
    getAdvancedSearchOptions,
    displaySearchResults,
    goToPage
};

// ---------------------------------------------------------------------------
// The results table's selection: the count, and the two batch export kinds
// (extracted content / original files) against the chosen rows.
// ---------------------------------------------------------------------------
import { exportSelectedFiles } from '../modules/file-operations/file-export.js';
import { escapeHtml } from '../modules/core/utils.js';

function selectedResultIds() {
    return Array.from(document.querySelectorAll('.result-checkbox:checked'))
        .map((check) => check.value);
}

function updateResultToolbar() {
    const count = selectedResultIds().length;
    const counter = document.getElementById('selectedResultCount');
    if (counter) counter.textContent = String(count);
    ['bulkResultContentBtn', 'bulkResultOriginalsBtn', 'bulkResultNamesBtn']
        .forEach((id) => {
            const button = document.getElementById(id);
            if (button) button.disabled = count === 0;
        });
}

document.addEventListener('change', (event) => {
    if (event.target.classList?.contains('result-checkbox')
        || event.target.classList?.contains('ut-check-all')) {
        // The table's own header checkbox flips the rows; this keeps the
        // toolbar's count honest either way.
        if (event.target.classList.contains('ut-check-all')) {
            const master = event.target;
            document.querySelectorAll('.result-checkbox').forEach((check) => {
                check.checked = master.checked;
            });
        }
        updateResultToolbar();
    }
});

function wireBulkResults() {
    const content = document.getElementById('bulkResultContentBtn');
    const originals = document.getElementById('bulkResultOriginalsBtn');
    const names = document.getElementById('bulkResultNamesBtn');
    if (content) content.addEventListener('click', () => exportSelectedFiles(selectedResultIds(), 'text'));
    if (originals) originals.addEventListener('click', () => exportSelectedFiles(selectedResultIds(), 'originals'));
    if (names) {
        names.addEventListener('click', () => {
            const params = new URLSearchParams(window.location.search);
            params.set('format', 'csv');
            window.location.href = '/api/export/search_results?' + params.toString();
        });
    }
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', wireBulkResults);
} else {
    wireBulkResults();
}
