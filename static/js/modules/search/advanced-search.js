/**
 * Advanced Search Module
 * Google-like search with autocomplete, BM25, query expansion, fuzzy matching
 */

import { apiGet, apiPost } from '../api/api-client.js';
import { endpoints } from '../api/endpoints.js';
import { escapeHtml, escapeAttribute } from '../core/utils.js';
import notificationSystem from '../ui/notifications.js';

// Autocomplete state
let autocompleteTimeout = null;
let autocompleteController = null;
let currentSuggestions = [];
let selectedSuggestionIndex = -1;

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
 * Initialize advanced search features
 */
export function initializeAdvancedSearch() {
    const searchInput = document.getElementById('searchQuery');
    if (!searchInput || searchInput.dataset.advancedSearchInitialized === 'true') return;
    searchInput.dataset.advancedSearchInitialized = 'true';

    // Create autocomplete container
    createAutocompleteContainer();

    // Setup autocomplete
    setupAutocomplete(searchInput);

    // Add advanced search options UI
    addAdvancedSearchOptions();

    // Handle keyboard navigation
    setupKeyboardNavigation(searchInput);
}

/**
 * Create autocomplete dropdown container
 */
function createAutocompleteContainer() {
    const searchInput = document.getElementById('searchQuery');
    if (!searchInput) return;

    // Check if container already exists
    if (document.getElementById('autocompleteContainer')) return;

    const container = document.createElement('div');
    container.id = 'autocompleteContainer';
    container.className = 'autocomplete-container position-relative';
    
    const dropdown = document.createElement('div');
    dropdown.id = 'autocompleteDropdown';
    dropdown.className = 'autocomplete-dropdown list-group position-absolute w-100';
    dropdown.style.display = 'none';
    dropdown.setAttribute('role', 'listbox');
    searchInput.setAttribute('role', 'combobox');
    searchInput.setAttribute('aria-autocomplete', 'list');
    searchInput.setAttribute('aria-expanded', 'false');
    searchInput.setAttribute('aria-controls', 'autocompleteDropdown');
    
    container.appendChild(dropdown);
    
    // Insert after search input
    const parent = searchInput.parentElement;
    parent.appendChild(container);
    
    // Add CSS if not already added
    if (!document.getElementById('autocompleteStyles')) {
        const style = document.createElement('style');
        style.id = 'autocompleteStyles';
        style.textContent = `
            .autocomplete-container {
                position: relative;
            }
            .autocomplete-dropdown {
                z-index: 1000;
                max-height: 300px;
                overflow-y: auto;
                border: 1px solid #dee2e6;
                border-radius: 0.375rem;
                background: white;
                box-shadow: 0 0.5rem 1rem rgba(0, 0, 0, 0.15);
                margin-top: 2px;
            }
            .autocomplete-item {
                cursor: pointer;
                padding: 0.5rem 1rem;
                border-bottom: 1px solid #f0f0f0;
            }
            .autocomplete-item:hover,
            .autocomplete-item.active {
                background-color: #f8f9fa;
            }
            .autocomplete-item:last-child {
                border-bottom: none;
            }
            .autocomplete-item-type {
                font-size: 0.75rem;
                color: #6c757d;
                margin-left: 0.5rem;
            }
            .autocomplete-item-count {
                font-size: 0.75rem;
                color: #6c757d;
                float: right;
            }
            .search-highlight {
                background-color: #fff3cd;
                padding: 0.1rem 0.2rem;
                border-radius: 0.2rem;
            }
        `;
        document.head.appendChild(style);
    }
}

/**
 * Setup autocomplete functionality
 */
function setupAutocomplete(searchInput) {
    if (!searchInput) return;

    // Debounced input handler
    searchInput.addEventListener('input', function() {
        const query = this.value.trim();
        
        // Clear previous timeout
        if (autocompleteTimeout) {
            clearTimeout(autocompleteTimeout);
        }
        
        // Cancel previous request
        if (autocompleteController) {
            autocompleteController.abort();
        }

        // Hide dropdown if query is too short
        if (query.length < 2) {
            hideAutocomplete();
            return;
        }

        // Show autocomplete after delay
        autocompleteTimeout = setTimeout(() => {
            fetchAutocompleteSuggestions(query);
        }, 200);
    });

    // Hide autocomplete when clicking outside
    document.addEventListener('click', function(e) {
        const container = document.getElementById('autocompleteContainer');
        if (container && !container.contains(e.target) && e.target !== searchInput) {
            hideAutocomplete();
        }
    });

    // Handle input focus
    searchInput.addEventListener('focus', function() {
        const query = this.value.trim();
        if (query.length >= 2 && currentSuggestions.length > 0) {
            showAutocomplete(currentSuggestions);
        }
    });
}

/**
 * Fetch autocomplete suggestions
 */
async function fetchAutocompleteSuggestions(query) {
    if (!query || query.length < 2) return;

    autocompleteController?.abort();
    const requestController = new AbortController();
    autocompleteController = requestController;

    try {
        const url = `/api/search/autocomplete?query=${encodeURIComponent(query)}&limit=10`;
        const response = await fetch(url, { signal: requestController.signal });
        if (!response.ok) throw new Error('Autocomplete request failed');
        const data = await response.json();
        if (requestController.signal.aborted || autocompleteController !== requestController) return;
        currentSuggestions = Array.isArray(data?.suggestions)
            ? data.suggestions.filter(suggestion => suggestion && typeof suggestion === 'object')
            : [];
        showAutocomplete(currentSuggestions);
    } catch (error) {
        if (error.name !== 'AbortError') {
            console.error('Autocomplete error:', error);
        }
    } finally {
        if (autocompleteController === requestController) autocompleteController = null;
    }
}

/**
 * Show autocomplete dropdown
 */
function showAutocomplete(suggestions) {
    const dropdown = document.getElementById('autocompleteDropdown');
    const searchInput = document.getElementById('searchQuery');
    if (!dropdown || !searchInput) return;

    const validSuggestions = Array.isArray(suggestions)
        ? suggestions.filter(suggestion => suggestion && typeof suggestion === 'object')
        : [];
    if (validSuggestions.length === 0) {
        hideAutocomplete();
        return;
    }

    let html = '';
    validSuggestions.forEach((suggestion, index) => {
        const text = String(suggestion.text == null ? '' : suggestion.text);
        const highlightedText = highlightQuery(text, searchInput.value || '');
        const typeIcon = getTypeIcon(suggestion.type);
        const typeLabel = escapeHtml(getTypeLabel(suggestion.type));
        const count = Number(suggestion.count);
        const hasCount = Number.isSafeInteger(count) && count > 0;

        html += `
            <div id="autocomplete-option-${index}" class="autocomplete-item list-group-item list-group-item-action"
                 data-index="${index}"
                 data-value="${escapeAttribute(text)}"
                 role="option"
                 aria-selected="false"
                 tabindex="-1">
                <div class="d-flex justify-content-between align-items-center">
                    <div class="flex-grow-1">
                        <i class="bi ${typeIcon} me-2" aria-hidden="true"></i>
                        ${highlightedText}
                        <span class="autocomplete-item-type">${typeLabel}</span>
                    </div>
                    ${hasCount ? `<span class="autocomplete-item-count">${count}</span>` : ''}
                </div>
            </div>
        `;
    });

    dropdown.innerHTML = html;
    dropdown.style.display = 'block';
    searchInput.setAttribute('aria-expanded', 'true');
    selectedSuggestionIndex = -1;

    dropdown.querySelectorAll('.autocomplete-item').forEach(item => {
        item.addEventListener('click', () => selectSuggestion(item.dataset.value || ''));
    });
}

/**
 * Hide autocomplete dropdown
 */
function hideAutocomplete() {
    const dropdown = document.getElementById('autocompleteDropdown');
    const searchInput = document.getElementById('searchQuery');
    if (dropdown) dropdown.style.display = 'none';
    searchInput?.setAttribute('aria-expanded', 'false');
    searchInput?.removeAttribute('aria-activedescendant');
    selectedSuggestionIndex = -1;
}

/**
 * Select a suggestion
 */
function selectSuggestion(value) {
    const searchInput = document.getElementById('searchQuery');
    if (searchInput) {
        searchInput.value = value;
        hideAutocomplete();
        
        // Trigger search if form exists
        const form = document.getElementById('enhancedSearchForm');
        if (form) {
            form.dispatchEvent(new Event('submit', { cancelable: true }));
        }
    }
}

/**
 * Highlight query in text
 */
function highlightQuery(text, query) {
    const source = String(text == null ? '' : text);
    const searchTerm = String(query == null ? '' : query);
    if (!searchTerm) return escapeHtml(source);

    const escapedTerm = searchTerm.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const matcher = new RegExp(escapedTerm, 'gi');
    let output = '';
    let lastIndex = 0;
    for (const match of source.matchAll(matcher)) {
        output += escapeHtml(source.slice(lastIndex, match.index));
        output += `<span class="search-highlight">${escapeHtml(match[0])}</span>`;
        lastIndex = match.index + match[0].length;
    }
    return output + escapeHtml(source.slice(lastIndex));
}

/**
 * Get icon for suggestion type
 */
function getTypeIcon(type) {
    const icons = {
        'file_name': 'bi-file-earmark',
        'word': 'bi-text-paragraph',
        'fuzzy_match': 'bi-search'
    };
    return Object.prototype.hasOwnProperty.call(icons, type) ? icons[type] : 'bi-circle';
}

/**
 * Get label for suggestion type
 */
function getTypeLabel(type) {
    const labels = {
        'file_name': searchText('File', 'File'),
        'word': searchText('Word', 'Word'),
        'fuzzy_match': searchText('Similar', 'Similar')
    };
    return Object.prototype.hasOwnProperty.call(labels, type)
        ? labels[type]
        : String(type == null ? '' : type);
}

/**
 * Setup keyboard navigation for autocomplete
 */
function setupKeyboardNavigation(searchInput) {
    if (!searchInput) return;

    searchInput.addEventListener('keydown', function(e) {
        const dropdown = document.getElementById('autocompleteDropdown');
        if (!dropdown || dropdown.style.display === 'none') {
            if (e.key === 'ArrowDown' && this.value.trim().length >= 2) {
                // Show autocomplete if hidden
                fetchAutocompleteSuggestions(this.value.trim());
            }
            return;
        }

        const items = dropdown.querySelectorAll('.autocomplete-item');
        if (items.length === 0) return;

        switch(e.key) {
            case 'ArrowDown':
                e.preventDefault();
                selectedSuggestionIndex = Math.min(selectedSuggestionIndex + 1, items.length - 1);
                updateSelection(items);
                break;
            case 'ArrowUp':
                e.preventDefault();
                selectedSuggestionIndex = Math.max(selectedSuggestionIndex - 1, -1);
                updateSelection(items);
                break;
            case 'Enter':
                e.preventDefault();
                if (selectedSuggestionIndex >= 0 && selectedSuggestionIndex < items.length) {
                    selectSuggestion(items[selectedSuggestionIndex].dataset.value);
                } else {
                    // Submit form if no suggestion selected
                    const form = document.getElementById('enhancedSearchForm');
                    if (form) {
                        form.dispatchEvent(new Event('submit', { cancelable: true }));
                    }
                }
                break;
            case 'Escape':
                e.preventDefault();
                hideAutocomplete();
                break;
        }
    });
}

/**
 * Update selected suggestion highlight
 */
function updateSelection(items) {
    const searchInput = document.getElementById('searchQuery');
    items.forEach((item, index) => {
        const selected = index === selectedSuggestionIndex;
        item.classList.toggle('active', selected);
        item.setAttribute('aria-selected', String(selected));
        if (selected) {
            searchInput?.setAttribute('aria-activedescendant', item.id);
            item.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' });
        }
    });
    if (selectedSuggestionIndex < 0) searchInput?.removeAttribute('aria-activedescendant');
}

/**
 * Add advanced search options UI
 */
function addAdvancedSearchOptions() {
    const searchForm = document.getElementById('enhancedSearchForm');
    if (!searchForm) return;

    // Check if options already exist
    if (document.getElementById('advancedSearchOptions')) return;

    const optionsContainer = document.createElement('div');
    optionsContainer.id = 'advancedSearchOptions';
    optionsContainer.className = 'mb-3';
    const labels = {
        options: searchText('Advanced Search Options', 'Advanced Search Options'),
        bm25: searchText('BM25 Ranking', 'BM25 Ranking'),
        bm25Help: searchText('Better relevance scoring', 'Better relevance scoring'),
        expansion: searchText('Query Expansion', 'Query Expansion'),
        expansionHelp: searchText('Include synonyms', 'Include synonyms'),
        fuzzy: searchText('Fuzzy Matching', 'Fuzzy Matching'),
        fuzzyHelp: searchText('Typo tolerance', 'Typo tolerance'),
        caseSensitive: searchText('Case sensitive', 'Case sensitive'),
        caseSensitiveHelp: searchText('Match uppercase and lowercase exactly', 'Match uppercase and lowercase exactly'),
        wholeWord: searchText('Whole word', 'Whole word'),
        wholeWordHelp: searchText('Match complete words only', 'Match complete words only'),
        algorithms: searchText('Advanced Algorithms', 'Advanced Algorithms'),
        algorithmsHelp: searchText('Enable all features', 'Enable all features'),
        tip: searchText('Tip:', 'Tip:'),
        quoteTip: searchText('Use quotes for exact phrases, e.g., "financial report"', 'Use quotes for exact phrases, e.g., "financial report"')
    };
    optionsContainer.innerHTML = `
        <div class="card">
            <div class="card-header py-2">
                <button class="btn btn-link p-0 text-decoration-none w-100 text-start"
                        type="button"
                        data-bs-toggle="collapse"
                        data-bs-target="#advancedOptionsCollapse"
                        aria-controls="advancedOptionsCollapse"
                        aria-expanded="false">
                    <i class="bi bi-gear me-2" aria-hidden="true"></i>
                    <strong>${escapeHtml(labels.options)}</strong>
                    <i class="bi bi-chevron-down float-end" aria-hidden="true"></i>
                </button>
            </div>
            <div id="advancedOptionsCollapse" class="collapse">
                <div class="card-body">
                    <div class="row g-2">
                        <div class="col-md-6">
                            <div class="form-check form-switch">
                                <input class="form-check-input" type="checkbox" id="useBM25" checked>
                                <label class="form-check-label" for="useBM25">
                                    <i class="bi bi-graph-up me-1" aria-hidden="true"></i>
                                    ${escapeHtml(labels.bm25)}
                                    <small class="d-block text-muted">${escapeHtml(labels.bm25Help)}</small>
                                </label>
                            </div>
                        </div>
                        <div class="col-md-6">
                            <div class="form-check form-switch">
                                <input class="form-check-input" type="checkbox" id="useExpansion" checked>
                                <label class="form-check-label" for="useExpansion">
                                    <i class="bi bi-arrows-angle-expand me-1" aria-hidden="true"></i>
                                    ${escapeHtml(labels.expansion)}
                                    <small class="d-block text-muted">${escapeHtml(labels.expansionHelp)}</small>
                                </label>
                            </div>
                        </div>
                        <div class="col-md-6">
                            <div class="form-check form-switch">
                                <input class="form-check-input" type="checkbox" id="useFuzzy" checked>
                                <label class="form-check-label" for="useFuzzy">
                                    <i class="bi bi-search me-1" aria-hidden="true"></i>
                                    ${escapeHtml(labels.fuzzy)}
                                    <small class="d-block text-muted">${escapeHtml(labels.fuzzyHelp)}</small>
                                </label>
                            </div>
                        </div>
                        <div class="col-md-6">
                            <div class="form-check form-switch">
                                <input class="form-check-input" type="checkbox" id="caseSensitive">
                                <label class="form-check-label" for="caseSensitive">
                                    <i class="bi bi-type me-1" aria-hidden="true"></i>
                                    ${escapeHtml(labels.caseSensitive)}
                                    <small class="d-block text-muted">${escapeHtml(labels.caseSensitiveHelp)}</small>
                                </label>
                            </div>
                        </div>
                        <div class="col-md-6">
                            <div class="form-check form-switch">
                                <input class="form-check-input" type="checkbox" id="wholeWord">
                                <label class="form-check-label" for="wholeWord">
                                    <i class="bi bi-fonts me-1" aria-hidden="true"></i>
                                    ${escapeHtml(labels.wholeWord)}
                                    <small class="d-block text-muted">${escapeHtml(labels.wholeWordHelp)}</small>
                                </label>
                            </div>
                        </div>
                        <div class="col-md-6">
                            <div class="form-check form-switch">
                                <input class="form-check-input" type="checkbox" id="useAdvanced" checked>
                                <label class="form-check-label" for="useAdvanced">
                                    <i class="bi bi-magic me-1" aria-hidden="true"></i>
                                    ${escapeHtml(labels.algorithms)}
                                    <small class="d-block text-muted">${escapeHtml(labels.algorithmsHelp)}</small>
                                </label>
                            </div>
                        </div>
                    </div>
                    <div class="mt-3">
                        <small class="text-muted">
                            <i class="bi bi-info-circle me-1" aria-hidden="true"></i>
                            <strong>${escapeHtml(labels.tip)}</strong> ${escapeHtml(labels.quoteTip)}
                        </small>
                    </div>
                </div>
            </div>
        </div>
    `;

    // Insert after search input
    const searchInput = document.getElementById('searchQuery');
    if (searchInput && searchInput.parentElement) {
        searchInput.parentElement.insertBefore(optionsContainer, searchInput.nextSibling);
    }
}

/**
 * Get advanced search options from form
 */
export function getAdvancedSearchOptions() {
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
 * Enhance search results display with relevance scores
 */
export function enhanceResultsDisplay(results) {
    if (!Array.isArray(results)) return [];

    return results.filter(result => result && typeof result === 'object').map(result => {
        const score = Number(result.relevance_score);
        if (result.relevance_score != null && Number.isFinite(score) && score > 0) {
            result.relevance_display = score.toFixed(2);
            result.relevance_percentage = Math.min(100, Math.round(score * 10));
        }
        return result;
    });
}

/**
 * Format search result with highlights
 */
export function formatSearchResult(result, query) {
    if (!result || typeof result !== 'object') return '';
    const fileId = Number(result.id);
    if (!Number.isSafeInteger(fileId) || fileId < 1) return '';
    const score = Number(result.relevance_score);
    const hasScore = result.relevance_score != null && Number.isFinite(score);
    const fileName = String(result.file_name || searchText('Unknown', 'Unknown'));
    const highlightedName = highlightQuery(fileName, query);
    const queryString = query ? `?q=${encodeURIComponent(String(query))}` : '';
    const fileHref = `/file/${fileId}${queryString}`;
    const previewLabel = searchText('Preview', 'Preview');
    const relevanceLabel = searchText('Relevance', 'Relevance');

    let html = `
        <div class="search-result-item mb-3 p-3 border rounded">
            <div class="d-flex justify-content-between align-items-start gap-3">
                <div class="flex-grow-1 min-w-0">
                    <h6 class="mb-2">
                        <a href="${escapeAttribute(fileHref)}" class="text-decoration-none">
                            ${highlightedName}
                        </a>
                        ${hasScore ? `
                            <span class="badge bg-info ms-2" title="${escapeAttribute(relevanceLabel)}">
                                ${score.toFixed(2)}
                            </span>
                        ` : ''}
                    </h6>
                    <div class="small text-muted mb-2">
                        <span class="me-3">
                            <i class="bi bi-building me-1" aria-hidden="true"></i>${escapeHtml(result.source_name || searchText('Unknown', 'Unknown'))}
                        </span>
                        <span class="me-3">
                            <i class="bi bi-diagram-3 me-1" aria-hidden="true"></i>${escapeHtml(result.side_name || searchText('Unknown', 'Unknown'))}
                        </span>
                        <span class="me-3">
                            <i class="bi bi-calendar me-1" aria-hidden="true"></i>${escapeHtml(result.file_date || searchText('N/A', 'N/A'))}
                        </span>
                        <span class="badge bg-secondary">${escapeHtml(result.file_type || searchText('Unknown', 'Unknown'))}</span>
                    </div>
    `;

    // Rebuild highlights from raw line text; never trust API-supplied HTML.
    const lineMatches = Array.isArray(result.line_matches)
        ? result.line_matches.filter(match => match && typeof match === 'object').slice(0, 3)
        : [];
    if (lineMatches.length > 0) {
        html += `<div class="mt-2"><small class="text-muted">${escapeHtml(searchText('Matching content:', 'Matching content:'))}</small><ul class="list-unstyled ms-3 mt-1">`;
        lineMatches.forEach(match => {
            const location = match.location || `${searchText('Line', 'Line')} ${match.line_number ?? ''}`;
            html += `
                <li class="small mb-1">
                    <span class="text-muted">${escapeHtml(location)}:</span>
                    <span class="ms-2">${highlightQuery(match.line_text || '', query)}</span>
                </li>
            `;
        });
        html += '</ul></div>';
    }

    html += `
                </div>
                <button type="button" class="btn btn-sm btn-outline-secondary flex-shrink-0"
                        data-file-preview-id="${fileId}"
                        aria-label="${escapeAttribute(`${previewLabel}: ${fileName}`)}"
                        title="${escapeAttribute(previewLabel)}">
                    <i class="bi bi-eye" aria-hidden="true"></i>
                    <span class="visually-hidden">${escapeHtml(previewLabel)}</span>
                </button>
            </div>
        </div>
    `;

    return html;
}

// Export API
export default {
    initializeAdvancedSearch,
    getAdvancedSearchOptions,
    enhanceResultsDisplay,
    formatSearchResult,
    fetchAutocompleteSuggestions,
    hideAutocomplete
};

