/**
 * Advanced Search Page JavaScript - Google/YouTube-like Implementation
 * Complete overhaul with professional filtering and intelligent algorithms
 */

import {
    chooseExportDestination,
    ensureExportExtension,
    saveExportBlob,
} from '../modules/core/export-download.js';
import {
    enhanceMultiSelects,
    refreshMultiSelect,
    iconForFileType,
} from '../modules/ui/multi-select-dropdown.js';
import { onPageTeardown } from '../modules/core/page-lifecycle.js';
import { escapeHtml } from '../modules/core/utils.js';

const ADVANCED_MULTI_SELECT_IDS = [
    'fileType',
    'categoriesSelect',
    'analystCategoriesFilter',
    'sourcesSelect',
    'sidesSelect',
];

function enhanceAdvancedFilterDropdowns() {
    const root = document.getElementById('filtersPanelContent') || document;
    enhanceMultiSelects(root, {
        fileType: {
            placeholder: tPage('allTypes', 'All Types'),
            iconForOption: (value) => iconForFileType(value),
        },
        categoriesSelect: {
            placeholder: tPage('allCategories', 'All categories'),
            markerForOption: () => '#10b981',
            iconForOption: () => 'bi-tags',
        },
        analystCategoriesFilter: {
            placeholder: tPage('allAnalystCategories', 'All analyst categories'),
            markerForOption: () => '#7c3aed',
            iconForOption: () => 'bi-person-check',
        },
        sourcesSelect: {
            placeholder: tPage('allSources', 'All sources'),
            markerForOption: () => '#0ea5e9',
            iconForOption: () => 'bi-building',
        },
        sidesSelect: {
            placeholder: tPage('allSides', 'All sides'),
            markerForOption: () => '#8b5cf6',
            iconForOption: () => 'bi-diagram-3',
        },
    });
}

function refreshAdvancedFilterDropdowns() {
    ADVANCED_MULTI_SELECT_IDS.forEach((id) => {
        const select = document.getElementById(id);
        if (select) refreshMultiSelect(select);
    });
}

// Global state
/** Detail-page URL that carries the originating content search so the
 *  term is located precisely when the file opens (?q= alias). */
function fileDetailHref(fileId) {
    const params = new URLSearchParams();
    if (searchState.query) {
        params.set('q', searchState.query);
        params.set('case_sensitive', String(!!(searchState.options && searchState.options.caseSensitive)));
        params.set('whole_word', String(!!(searchState.options && searchState.options.wholeWord)));
    }
    const qs = params.toString();
    return `/file/${fileId}${qs ? '?' + qs : ''}`;
}

let activeAdvancedSearchController = null;
let advancedSearchRequestSequence = 0;
let activeSearchCorrelationId = null;

function newSearchCorrelationId() {
    if (typeof crypto !== 'undefined' && crypto.randomUUID) {
        return crypto.randomUUID();
    }
    return `search-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function cancelServerSearch(correlationId) {
    if (!correlationId) return;
    void fetch('/api/search/cancel', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': analystCsrftoken(),
        },
        body: JSON.stringify({ correlation_id: correlationId }),
    }).catch(() => {});
}
let searchAdvancedPopstateHandler = null;
let searchAdvancedListenersBound = false;
let searchAdvancedScopeDelegation = false;
/** @type {((event: Event) => void) | null} */
let searchAdvancedScopeChangeHandler = null;

function teardownSearchAdvancedPage() {
    if (searchAdvancedPopstateHandler) {
        window.removeEventListener('popstate', searchAdvancedPopstateHandler);
        searchAdvancedPopstateHandler = null;
    }
    if (activeAdvancedSearchController) {
        activeAdvancedSearchController.abort();
        activeAdvancedSearchController = null;
    }
    searchAdvancedListenersBound = false;
    searchAdvancedScopeDelegation = false;
    if (searchAdvancedScopeChangeHandler) {
        document.removeEventListener('change', searchAdvancedScopeChangeHandler);
        searchAdvancedScopeChangeHandler = null;
    }
    if (document.body) {
        delete document.body.dataset.searchAdvancedListeners;
    }
    const mainInput = document.getElementById('mainSearchInput');
    if (mainInput) delete mainInput.dataset.searchAdvancedInputBound;
}

onPageTeardown('search-advanced', teardownSearchAdvancedPage);

const searchState = {
    query: '',
    filters: {
        fileType: [],
        categories: [],
        analystCategories: [],
        sources: [],
        sides: [],
        dateFrom: '',
        dateTo: '',
        status: ['Read']
    },
    options: {
        caseSensitive: false,
        wholeWord: false,
        useFuzzy: true
    },
    // Analyst-categorization search scope (FR-2.x): 'uncategorized' (default),
    // 'all' or 'categorized'. Operates ONLY on analyst categorization status.
    scope: 'uncategorized',
    // Manual categorization state (FR-1.2 / FR-1.3)
    canCategorize: false,
    analystCategories: [],
    selectedIds: new Set(),
    activeSelectionKey: null,
    similarityGroups: null,
    results: [],
    pagination: null,
    lastDefinition: null,
    currentPage: 1,
    resultsPerPage: 20,
    totalResults: 0,
    searchTime: 0,
    searchEngine: null,
    nextCursor: null,
    fetchWithCursor: null,
    clientPerf: null,
    suggestions: [],
    searchHistory: []
};

async function initializeSearchAdvancedPage() {
    teardownSearchAdvancedPage();
    console.log('Advanced Search page loaded - Google-like implementation');
    initializePageData();
    initializeSearch();
    initializeScopeSelector();
    setupEventListeners();

    // History is independent of filter controls; load it without delaying the
    // initial search. URL/saved-search restoration must wait until every
    // asynchronous select has its options, or selected IDs are silently lost.
    void loadSearchHistory();
    void loadSearchExportColumns();
    await loadFilterOptions();
    restoreSearchFromUrlAndRun();
    searchAdvancedPopstateHandler = restoreSearchFromUrlAndRun;
    window.addEventListener('popstate', searchAdvancedPopstateHandler);
}

export default function init() {
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initializeSearchAdvancedPage, { once: true });
    } else {
        initializeSearchAdvancedPage();
    }
}

// ====================================================================
// Search-state persistence (the "search is lost" fix, part 1)
// ---------------------------------------------------------------------
// The complete search definition — query, analyst scope, filters,
// match options, sort and page — is serialized into the page URL after
// every search (history.replaceState, no history spam) and restored
// from the URL on load. A refresh, a return to this tab, or a shared
// link therefore reproduces the exact result list.
//
// The same definition object is what "Save search" stores, and the
// same parameter encoding is mirrored server-side in
// Api/routes/search.py (_advanced_search_run_url) so saved searches
// from the management page land here fully restored.
// ====================================================================

// URL parameters understood by restoreSearchFromUrl(). Keep in sync
// with _advanced_search_run_url in Api/routes/search.py.
const SEARCH_URL_PARAMS = ['q', 'scope', 'sort', 'cs', 'ww', 'fz', 'ft',
    'cat', 'acat', 'src', 'side', 'df', 'dt', 'st', 'page', 'hd', 'sim'];

/** The search exactly as the on-screen controls currently define it. */
function currentSearchDefinition() {
    return {
        query: document.getElementById('mainSearchInput')?.value?.trim() || '',
        scope: searchState.scope,
        sort_by: document.getElementById('sortBy')?.value || 'relevance',
        page: searchState.currentPage,
        similarity_threshold: Number(document.getElementById('similarityThreshold')?.value) || 0.32,
        options: {
            case_sensitive: document.getElementById('caseSensitive')?.checked || false,
            whole_word: document.getElementById('wholeWord')?.checked || false,
            use_fuzzy: document.getElementById('useFuzzy')?.checked !== false
        },
        filters: collectFilters()
    };
}

/** Stable selection identity ignores page and sort: both are views of the
 * same filtered result set. The selected ids themselves live only in this tab. */
function selectionKeyForDefinition(definition) {
    const identity = {
        query: definition.query || '',
        scope: definition.scope || 'uncategorized',
        options: definition.options || {},
        filters: definition.filters || {}
    };
    const serialized = JSON.stringify(identity);
    let hash = 2166136261;
    for (let i = 0; i < serialized.length; i += 1) {
        hash ^= serialized.charCodeAt(i);
        hash = Math.imul(hash, 16777619);
    }
    return `inforaxis.search.selection.v1.${(hash >>> 0).toString(36)}`;
}

function readStoredSelection(key) {
    try {
        const parsed = JSON.parse(sessionStorage.getItem(key) || '[]');
        return new Set(Array.isArray(parsed)
            ? parsed.map(Number).filter(id => Number.isSafeInteger(id) && id > 0)
            : []);
    } catch (_error) {
        return new Set();
    }
}

function persistSelectedIds() {
    if (!searchState.activeSelectionKey) return;
    try {
        sessionStorage.setItem(searchState.activeSelectionKey,
            JSON.stringify(Array.from(searchState.selectedIds)));
    } catch (_error) {
        // The search remains usable when storage is unavailable or full.
    }
}

/** Encode a search definition into URL parameters (lossless). */
function serializeDefinitionToParams(def) {
    const params = new URLSearchParams();
    const filters = def.filters || {};
    const options = def.options || {};

    if (def.query) params.set('q', def.query);
    if (def.scope && def.scope !== 'uncategorized') params.set('scope', def.scope);
    if (def.sort_by && def.sort_by !== 'relevance') params.set('sort', def.sort_by);
    if (options.case_sensitive) params.set('cs', '1');
    if (options.whole_word) params.set('ww', '1');
    if (options.use_fuzzy === false) params.set('fz', '0');
    if (def.similarity_threshold && Number(def.similarity_threshold) !== 0.32) {
        params.set('sim', String(Number(def.similarity_threshold)));
    }

    const appendAll = (key, values) =>
        (values || []).forEach(v => params.append(key, String(v)));
    appendAll('ft', filters.file_type);
    appendAll('cat', filters.category_id);
    appendAll('acat', filters.analyst_category_id);
    appendAll('src', filters.source_id);
    appendAll('side', filters.side_id);

    if (filters.date_from) params.set('df', filters.date_from);
    if (filters.date_to) params.set('dt', filters.date_to);
    if (filters.hide_duplicates) params.set('hd', '1');

    const status = Array.isArray(filters.status) ? filters.status : ['Read'];
    const read = status.includes('Read');
    const unread = status.includes('Unread');
    if (read && unread) params.set('st', 'read,unread');
    else if (!read && unread) params.set('st', 'unread');
    else if (!read && !unread) params.set('st', 'none');

    if (def.page && def.page > 1) params.set('page', String(def.page));
    return params;
}

/** Reflect the current search into the address bar (replace, not push). */
function persistSearchToUrl(definition = null) {
    try {
        const qs = serializeDefinitionToParams(definition || currentSearchDefinition()).toString();
        window.history.replaceState(null, '', window.location.pathname + (qs ? '?' + qs : ''));
    } catch (e) {
        console.warn('Could not update the address bar', e);
    }
}

/** Multi-select helper: select exactly the given option values (those
 *  that exist); null/undefined leaves the control untouched. */
function setMultiSelectValues(selectId, values) {
    if (values === null || values === undefined) return;
    const select = document.getElementById(selectId);
    if (!select) return;
    const wanted = new Set((values || []).map(v => String(v)));
    Array.from(select.options).forEach(opt => {
        opt.selected = wanted.has(opt.value);
    });
    refreshMultiSelect(select);
}

/** Apply a search definition (from the URL or a saved search) to the
 *  on-screen controls. Absent optional fields keep their defaults. */
function applyDefinitionToControls(def) {
    const filters = def.filters || {};
    const options = def.options || {};

    const mainInput = document.getElementById('mainSearchInput');
    if (mainInput) {
        mainInput.value = def.query || '';
        const clearBtn = document.getElementById('clearSearchBtn');
        if (clearBtn) clearBtn.style.display = def.query ? 'block' : 'none';
    }
    searchState.query = def.query || '';

    if (['uncategorized', 'all', 'categorized'].includes(def.scope)) {
        searchState.scope = def.scope;
        const radio = document.querySelector(`input[name="analystScope"][value="${def.scope}"]`);
        if (radio) radio.checked = true;
    }

    const sortSelect = document.getElementById('sortBy');
    if (sortSelect && def.sort_by) sortSelect.value = def.sort_by;
    const similaritySelect = document.getElementById('similarityThreshold');
    if (similaritySelect && Number.isFinite(Number(def.similarity_threshold))) {
        const threshold = Number(def.similarity_threshold);
        if (threshold >= 0.05 && threshold <= 0.95) similaritySelect.value = String(threshold);
    }

    const caseEl = document.getElementById('caseSensitive');
    if (caseEl) caseEl.checked = !!options.case_sensitive;
    const wholeEl = document.getElementById('wholeWord');
    if (wholeEl) wholeEl.checked = !!options.whole_word;
    const fuzzyEl = document.getElementById('useFuzzy');
    if (fuzzyEl) fuzzyEl.checked = options.use_fuzzy !== false;
    searchState.options = {
        caseSensitive: !!options.case_sensitive,
        wholeWord: !!options.whole_word,
        useFuzzy: options.use_fuzzy !== false
    };

    setMultiSelectValues('fileType', filters.file_type);
    setMultiSelectValues('categoriesSelect', filters.category_id);
    setMultiSelectValues('analystCategoriesFilter', filters.analyst_category_id);
    setMultiSelectValues('sourcesSelect', filters.source_id);
    setMultiSelectValues('sidesSelect', filters.side_id);

    const duplicateToggle = document.getElementById('hideDuplicates');
    if (duplicateToggle) duplicateToggle.checked = !!filters.hide_duplicates;

    const from = document.getElementById('dateFrom');
    if (from) from.value = filters.date_from || '';
    const to = document.getElementById('dateTo');
    if (to) to.value = filters.date_to || '';

    if (Array.isArray(filters.status)) {
        const readEl = document.getElementById('statusRead');
        const unreadEl = document.getElementById('statusUnread');
        if (readEl) readEl.checked = filters.status.includes('Read');
        if (unreadEl) unreadEl.checked = filters.status.includes('Unread');
    }
}

/** Parse a search definition from the current URL. Returns null when no
 *  search parameters are present. */
function readDefinitionFromUrl() {
    const params = new URLSearchParams(window.location.search);
    if (!SEARCH_URL_PARAMS.some(k => params.has(k))) return null;

    const statusParam = params.get('st');
    let status;
    if (statusParam === 'none') {
        status = [];
    } else if (statusParam) {
        const parts = statusParam.split(',').map(s => s.trim().toLowerCase());
        status = [];
        if (parts.includes('read')) status.push('Read');
        if (parts.includes('unread')) status.push('Unread');
    }

    return {
        query: params.get('q') || '',
        scope: params.get('scope') || undefined,
        sort_by: params.get('sort') || undefined,
        page: Math.max(1, parseInt(params.get('page'), 10) || 1),
        similarity_threshold: Number(params.get('sim')) || 0.32,
        options: {
            case_sensitive: params.get('cs') === '1',
            whole_word: params.get('ww') === '1',
            use_fuzzy: params.get('fz') !== '0'
        },
        filters: {
            file_type: params.getAll('ft'),
            category_id: params.getAll('cat').map(v => parseInt(v, 10)).filter(v => !isNaN(v)),
            analyst_category_id: params.getAll('acat').map(v => parseInt(v, 10)).filter(v => !isNaN(v)),
            source_id: params.getAll('src').map(v => parseInt(v, 10)).filter(v => !isNaN(v)),
            side_id: params.getAll('side').map(v => parseInt(v, 10)).filter(v => !isNaN(v)),
            date_from: params.get('df') || null,
            date_to: params.get('dt') || null,
            status: status,
            hide_duplicates: params.get('hd') === '1'
        }
    };
}

/** Restore a search from the URL into the controls. True when restored. */
function restoreSearchFromUrl() {
    const def = readDefinitionFromUrl();
    if (!def) return false;
    applyDefinitionToControls(def);
    searchState.currentPage = def.page;
    updateFilterChips();
    return true;
}

function restoreSearchFromUrlAndRun() {
    if (restoreSearchFromUrl()) {
        executeAdvancedSearch(true);
    }
}

// Read server-provided page data (initial scope, permissions, translations)
function initializePageData() {
    const pageDataEl = document.getElementById('search-advanced-page-data');
    if (!pageDataEl) return;
    try {
        const data = JSON.parse(pageDataEl.textContent);
        if (data.initialScope && ['uncategorized', 'all', 'categorized'].includes(data.initialScope)) {
            searchState.scope = data.initialScope;
        }
        searchState.canCategorize = !!data.canCategorize;
        const radio = document.querySelector(`input[name="analystScope"][value="${searchState.scope}"]`);
        if (radio) radio.checked = true;
        // Viewers cannot categorize: hide the manual-categorization bar entirely.
        if (!searchState.canCategorize) {
            const bar = document.getElementById('analystCategorizationBar');
            if (bar) bar.remove();
        }
    } catch (e) {
        console.warn('Could not parse page data', e);
    }
}

// Wire the scope radio group (FR-2.2). Changing scope re-runs the search so
// the result set immediately reflects the newly selected scope.
function initializeScopeSelector() {
    if (searchAdvancedScopeDelegation) return;
    searchAdvancedScopeDelegation = true;
    searchAdvancedScopeChangeHandler = function (event) {
        const radio = event.target;
        if (!radio.matches || !radio.matches('input[name="analystScope"]')) return;
        if (!radio.checked) return;
        searchState.scope = radio.value;
        const results = document.getElementById('searchResultsSection');
        if (results && results.style.display !== 'none') {
            executeAdvancedSearch();
        }
    };
    document.addEventListener('change', searchAdvancedScopeChangeHandler);
}

// Initialize search functionality
function initializeSearch() {
    const mainInput = document.getElementById('mainSearchInput');
    if (!mainInput || mainInput.dataset.searchAdvancedInputBound === '1') return;
    mainInput.dataset.searchAdvancedInputBound = '1';

    // Real-time search suggestions
    let suggestionTimeout;
    mainInput.addEventListener('input', function(e) {
        const query = e.target.value.trim();
        
        // Show/hide clear button
        const clearBtn = document.getElementById('clearSearchBtn');
        if (clearBtn) {
            clearBtn.style.display = query ? 'block' : 'none';
        }
        
        // Debounce suggestions
        clearTimeout(suggestionTimeout);
        if (query.length >= 2) {
            suggestionTimeout = setTimeout(() => {
                loadSearchSuggestions(query);
            }, 300);
        } else {
            hideSuggestions();
        }
    });
    
    // Enter key to search
    mainInput.addEventListener('keydown', function(e) {
        if (e.key === 'Enter') {
            e.preventDefault();
            executeAdvancedSearch();
        } else if (e.key === 'Escape') {
            hideSuggestions();
        }
    });
    
    // Clear search
    const clearBtn = document.getElementById('clearSearchBtn');
    if (clearBtn) {
        clearBtn.addEventListener('click', function() {
            mainInput.value = '';
            searchState.query = '';
            clearBtn.style.display = 'none';
            hideSuggestions();
            mainInput.focus();
        });
    }
}

// Setup event listeners
function setupEventListeners() {
    if (searchAdvancedListenersBound) return;
    searchAdvancedListenersBound = true;
    if (document.body) document.body.dataset.searchAdvancedListeners = '1';

    // Filter select changes
    ['fileType', 'categoriesSelect', 'analystCategoriesFilter', 'sourcesSelect', 'sidesSelect'].forEach(id => {
        const element = document.getElementById(id);
        if (element) {
            element.addEventListener('change', updateFilterChips);
        }
    });
    
    // Date changes
    ['dateFrom', 'dateTo'].forEach(id => {
        const element = document.getElementById(id);
        if (element) {
            element.addEventListener('change', updateFilterChips);
        }
    });
    
    // Status checkboxes
    ['statusRead', 'statusUnread'].forEach(id => {
        const element = document.getElementById(id);
        if (element) {
            element.addEventListener('change', updateFilterChips);
        }
    });

    const similaritySelect = document.getElementById('similarityThreshold');
    similaritySelect?.addEventListener('change', () => {
        if (searchState.lastDefinition) {
            searchState.lastDefinition = {
                ...searchState.lastDefinition,
                similarity_threshold: Number(similaritySelect.value) || 0.32,
            };
            persistSearchToUrl(searchState.lastDefinition);
        }
    });

    const suggestionsList = document.getElementById('suggestionsList');
    suggestionsList?.addEventListener('click', (event) => {
        const item = event.target.closest('[data-suggestion]');
        if (item && suggestionsList.contains(item)) selectSuggestion(item.dataset.suggestion || '');
    });

    const filterChips = document.getElementById('filtersChips');
    filterChips?.addEventListener('click', (event) => {
        const button = event.target.closest('[data-remove-filter-chip]');
        if (button && filterChips.contains(button)) {
            removeFilterChip(button.dataset.filterType || '', button.dataset.filterId || '');
        }
    });
}

// Load filter options
async function loadFilterOptions() {
    try {
        // Populate the type filter from ingestion-detected formats so new
        // readers/extensions appear automatically without editing this page.
        const fileTypesResponse = await fetch('/api/files/types');
        if (fileTypesResponse.ok) {
            const fileTypesData = await fileTypesResponse.json();
            const fileTypeSelect = document.getElementById('fileType');
            if (fileTypeSelect && Array.isArray(fileTypesData.types)) {
                const allTypesLabel = escapeHtml(tPage('allTypes', 'All Types'));
                fileTypeSelect.innerHTML = `<option value="">${allTypesLabel}</option>` +
                    fileTypesData.types.map(item => {
                        const value = String(item.file_type || '');
                        const label = String(item.label || value || 'Unknown');
                        const count = Number(item.file_count) || 0;
                        return `<option value="${escapeAttr(value)}">${escapeHtml(label)} (${count.toLocaleString()})</option>`;
                    }).join('');
            }
        }

        // Load categories
        const categoriesRes = await fetch('/api/categories');
        const categories = await categoriesRes.json();
        const categoriesSelect = document.getElementById('categoriesSelect');
        if (categoriesSelect && Array.isArray(categories)) {
            categoriesSelect.innerHTML = categories.map(c =>
                `<option value="${c.id}">${escapeHtml(c.name)}</option>`
            ).join('');
        }
        
        // Load sources (advanced-filters panel - the single home for
        // source/side scoping since the old "Search Within" block was
        // merged into it)
        const sourcesRes = await fetch('/api/sources');
        const sources = await sourcesRes.json();

        const sourcesSelect = document.getElementById('sourcesSelect');
        if (sourcesSelect && Array.isArray(sources)) {
            sourcesSelect.innerHTML = sources.map(s =>
                `<option value="${s.id}">${escapeHtml(s.name)}</option>`
            ).join('');
        }

        // Load sides
        const sidesRes = await fetch('/api/sides');
        const sides = await sidesRes.json();

        const sidesSelect = document.getElementById('sidesSelect');
        if (sidesSelect && Array.isArray(sides)) {
            sidesSelect.innerHTML = sides.map(s =>
                `<option value="${s.id}">${escapeHtml(s.name)}</option>`
            ).join('');
        }

        // Load ANALYST categories (FR-1.4): a dedicated namespace, fetched
        // from /api/analyst/categories - NEVER from /api/categories (which
        // serves the system "smart" taxonomy). These populate the analyst
        // filter and the manual-categorization bar only.
        await loadAnalystCategories();
        enhanceAdvancedFilterDropdowns();
    } catch (error) {
        console.error('Error loading filter options:', error);
    }
}

// Translate a key using the page-data translations (server-rendered) with an
// English fallback. Used by the analyst-categorization UI added in this page.
function tPage(key, fallback) {
    const pageDataEl = document.getElementById('search-advanced-page-data');
    if (pageDataEl) {
        try {
            const data = JSON.parse(pageDataEl.textContent);
            if (data.translations && data.translations[key]) {
                return data.translations[key];
            }
        } catch (e) { /* fall through */ }
    }
    return window.appTranslations?.[key] || fallback;
}

// Load analyst-defined categories into their own controls (FR-1.3, FR-1.4)
async function loadAnalystCategories() {
    try {
        const response = await fetch('/api/analyst/categories');
        if (!response.ok) return;
        const categories = await response.json();
        searchState.analystCategories = Array.isArray(categories) ? categories : [];

        // Advanced-filter multi-select (analyst namespace, separate control
        // from the "Smart Categories" select above)
        const filterSelect = document.getElementById('analystCategoriesFilter');
        if (filterSelect) {
            filterSelect.innerHTML = searchState.analystCategories.map(c =>
                `<option value="${c.id}">${escapeHtml(c.name)} (${c.file_count ?? 0})</option>`
            ).join('');
            refreshMultiSelect(filterSelect);
        }

        // Manual-categorization bar dropdown
        const barSelect = document.getElementById('analystCategorySelect');
        if (barSelect) {
            barSelect.innerHTML = '<option value="">' +
                escapeHtml(tPage('chooseOrCreateCategory', 'Choose analyst category…')) +
                '</option>' +
                searchState.analystCategories.map(c =>
                    `<option value="${c.id}">${escapeHtml(c.name)}</option>`
                ).join('');
        }
    } catch (error) {
        console.error('Error loading analyst categories:', error);
    }
}

// Load search suggestions
async function loadSearchSuggestions(query) {
    try {
        const response = await fetch(`/api/search/suggestions?query=${encodeURIComponent(query)}&limit=8`);
        const data = await response.json();
        
        if (data.suggestions && Array.isArray(data.suggestions)) {
            searchState.suggestions = data.suggestions;
            displaySuggestions(data.suggestions, query);
        }
    } catch (error) {
        console.error('Error loading suggestions:', error);
    }
}

// Display search suggestions
function displaySuggestions(suggestions, query) {
    const dropdown = document.getElementById('searchSuggestions');
    const list = document.getElementById('suggestionsList');
    
    if (!dropdown || !list) return;
    
    if (suggestions.length === 0) {
        hideSuggestions();
        return;
    }
    
    list.innerHTML = suggestions.map(suggestion => `
        <button type="button" class="suggestion-item" data-suggestion="${escapeAttr(suggestion)}">
            <i class="bi bi-search" aria-hidden="true"></i>
            <span>${highlightMatch(suggestion, query)}</span>
        </button>
    `).join('');
    
    dropdown.classList.add('active');
    document.getElementById('mainSearchInput')?.setAttribute('aria-expanded', 'true');
}

// Render suggestion text safely while marking query matches.
function highlightMatch(text, query) {
    return highlightQueryTerms(text, query);
}

// Select suggestion
function selectSuggestion(suggestion) {
    document.getElementById('mainSearchInput').value = suggestion;
    searchState.query = suggestion;
    hideSuggestions();
    executeAdvancedSearch();
}

// Hide suggestions
function hideSuggestions() {
    const dropdown = document.getElementById('searchSuggestions');
    if (dropdown) {
        dropdown.classList.remove('active');
    }
    document.getElementById('mainSearchInput')?.setAttribute('aria-expanded', 'false');
}

// Update filter chips
function updateFilterChips() {
    const chips = [];
    
    // File types
    const fileTypes = Array.from(document.getElementById('fileType').selectedOptions).map(o => o.value);
    if (fileTypes.length > 0) {
        fileTypes.forEach(type => {
            if (type) chips.push({ type: 'fileType', label: tPage('fileType', 'File Type'), value: type, id: type });
        });
    }
    
    // Categories (smart taxonomy - separate from analyst categories, FR-1.4)
    const categoriesSelect = document.getElementById('categoriesSelect');
    const categories = Array.from(categoriesSelect.selectedOptions).map(o => o.value);
    if (categories.length > 0) {
        categories.forEach(catId => {
            const option = Array.from(categoriesSelect.options).find(candidate => candidate.value === catId);
            if (option) {
                chips.push({ type: 'category', label: tPage('smartCategory', 'Smart Category'), value: option.textContent, id: catId });
            }
        });
    }

    // Analyst categories (manual taxonomy - separate namespace, FR-1.4)
    const analystFilter = document.getElementById('analystCategoriesFilter');
    if (analystFilter) {
        Array.from(analystFilter.selectedOptions).forEach(opt => {
            chips.push({ type: 'analystCategory', label: tPage('analystCategory', 'Analyst Category'), value: opt.textContent, id: opt.value });
        });
    }
    
    // Sources
    const sourcesSelect = document.getElementById('sourcesSelect');
    const sources = Array.from(sourcesSelect.selectedOptions).map(o => o.value);
    if (sources.length > 0) {
        sources.forEach(sourceId => {
            const option = Array.from(sourcesSelect.options).find(candidate => candidate.value === sourceId);
            if (option) {
                chips.push({ type: 'source', label: tPage('source', 'Source'), value: option.textContent, id: sourceId });
            }
        });
    }
    
    // Sides
    const sidesSelect = document.getElementById('sidesSelect');
    const sides = Array.from(sidesSelect.selectedOptions).map(o => o.value);
    if (sides.length > 0) {
        sides.forEach(sideId => {
            const option = Array.from(sidesSelect.options).find(candidate => candidate.value === sideId);
            if (option) {
                chips.push({ type: 'side', label: tPage('side', 'Side'), value: option.textContent, id: sideId });
            }
        });
    }
    
    // Date range
    const dateFrom = document.getElementById('dateFrom').value;
    const dateTo = document.getElementById('dateTo').value;
    if (dateFrom) {
        chips.push({ type: 'dateFrom', label: tPage('from', 'From'), value: dateFrom });
    }
    if (dateTo) {
        chips.push({ type: 'dateTo', label: tPage('to', 'To'), value: dateTo });
    }
    
    // Status
    const statusRead = document.getElementById('statusRead').checked;
    const statusUnread = document.getElementById('statusUnread').checked;
    if (statusRead && !statusUnread) {
        // Read is the default status, so it is not shown as an active filter.
    } else if (!statusRead && statusUnread) {
        chips.push({ type: 'status', label: tPage('status', 'Status'), value: tPage('pending', 'Pending') });
    } else if (!statusRead && !statusUnread) {
        chips.push({ type: 'status', label: tPage('status', 'Status'), value: tPage('noStatusesSelected', 'No statuses selected') });
    }
    
    // Display chips
    displayFilterChips(chips);
    
    // Update active filters count
    const countEl = document.getElementById('activeFiltersCount');
    if (countEl) {
        countEl.textContent = chips.length;
    }
}

// Display filter chips
function displayFilterChips(chips) {
    const container = document.getElementById('filtersChipsContainer');
    const chipsEl = document.getElementById('filtersChips');
    
    if (!container || !chipsEl) return;
    
    if (chips.length === 0) {
        container.style.display = 'none';
        return;
    }
    
    container.style.display = 'block';
    chipsEl.innerHTML = chips.map((chip) => {
        const chipClass = chip.priority ? 'filter-chip priority-chip' : 'filter-chip';
        const type = escapeAttr(chip.type || '');
        const id = escapeAttr(chip.id == null ? '' : chip.id);
        return `
            <div class="${chipClass}">
                <span class="chip-label">${escapeHtml(chip.label)}:</span>
                <span class="chip-value">${escapeHtml(chip.value)}</span>
                <button type="button" class="chip-remove" data-remove-filter-chip="true"
                        data-filter-type="${type}" data-filter-id="${id}"
                        aria-label="${escapeAttr(tPage('removeFilter', 'Remove filter'))}">
                    <i class="bi bi-x" aria-hidden="true"></i>
                </button>
            </div>
        `;
    }).join('');
}

// Remove filter chip
function removeFilterChip(type, id) {
    {
        // Handle regular filters
        switch (type) {
            case 'fileType':
                const fileTypeSelect = document.getElementById('fileType');
                const fileTypeOption = Array.from(fileTypeSelect.options).find(option => option.value === String(id));
                if (fileTypeOption) fileTypeOption.selected = false;
                refreshMultiSelect(fileTypeSelect);
                break;
            case 'category':
                const categorySelect = document.getElementById('categoriesSelect');
                const categoryOption = Array.from(categorySelect.options).find(option => option.value === String(id));
                if (categoryOption) categoryOption.selected = false;
                refreshMultiSelect(categorySelect);
                break;
            case 'analystCategory':
                const analystFilter = document.getElementById('analystCategoriesFilter');
                if (analystFilter) {
                    const analystOption = Array.from(analystFilter.options).find(option => option.value === String(id));
                    if (analystOption) analystOption.selected = false;
                    refreshMultiSelect(analystFilter);
                }
                break;
            case 'source':
                const sourceSelect = document.getElementById('sourcesSelect');
                const sourceOption = Array.from(sourceSelect.options).find(option => option.value === String(id));
                if (sourceOption) sourceOption.selected = false;
                refreshMultiSelect(sourceSelect);
                break;
            case 'side':
                const sideSelect = document.getElementById('sidesSelect');
                const sideOption = Array.from(sideSelect.options).find(option => option.value === String(id));
                if (sideOption) sideOption.selected = false;
                refreshMultiSelect(sideSelect);
                break;
            case 'dateFrom':
                document.getElementById('dateFrom').value = '';
                break;
            case 'dateTo':
                document.getElementById('dateTo').value = '';
                break;
            case 'status':
                document.getElementById('statusRead').checked = true;
                document.getElementById('statusUnread').checked = false;
                break;
        }
    }
    updateFilterChips();
}

// Clear all filters
function clearAllFilters() {
    document.getElementById('fileType').selectedIndex = -1;
    document.getElementById('categoriesSelect').selectedIndex = -1;
    const analystFilter = document.getElementById('analystCategoriesFilter');
    if (analystFilter) analystFilter.selectedIndex = -1;
    document.getElementById('sourcesSelect').selectedIndex = -1;
    document.getElementById('sidesSelect').selectedIndex = -1;
    document.getElementById('dateFrom').value = '';
    document.getElementById('dateTo').value = '';
    document.getElementById('statusRead').checked = true;
    document.getElementById('statusUnread').checked = false;
    const duplicateToggle = document.getElementById('hideDuplicates');
    if (duplicateToggle) duplicateToggle.checked = false;
    refreshAdvancedFilterDropdowns();
    updateFilterChips();
}

// Toggle filters panel
function toggleFiltersPanel() {
    const content = document.getElementById('filtersPanelContent');
    const icon = document.getElementById('filtersToggleIcon');
    
    if (content && icon) {
        content.classList.toggle('active');
        icon.classList.toggle('bi-chevron-down');
        icon.classList.toggle('bi-chevron-up');
    }
}

// Map the user-facing sort choices to the API's stable field/direction pair.
function getAdvancedSortDefinition(choice = document.getElementById('sortBy')?.value || 'relevance') {
    const sortMap = {
        relevance: { sort_by: 'relevance', sort_order: 'desc' },
        date: { sort_by: 'date', sort_order: 'desc' },
        date_old: { sort_by: 'date', sort_order: 'asc' },
        name: { sort_by: 'name', sort_order: 'asc' },
        type: { sort_by: 'type', sort_order: 'asc' },
        size: { sort_by: 'size', sort_order: 'desc' },
    };
    return sortMap[choice] || sortMap.relevance;
}

// Execute advanced search
async function executeAdvancedSearch(preservePage = false) {
    const startTime = performance.now();
    const query = document.getElementById('mainSearchInput')?.value.trim() || '';
    const filters = collectFilters();

    if (!query && getActiveFiltersCount() === 0) {
        alert('Please enter a search query or select filters');
        return;
    }

    // Confirm before changing selection or entering a loading state. Returning
    // here must leave the previous results usable, not strand a spinner.
    if (!filters.source_id.length && !filters.side_id.length && !query) {
        const confirmSearch = confirm(tPage('largeSearchConfirm',
            'Searching without a source or side filter may be slow on large datasets. Continue?'));
        if (!confirmSearch) return;
    }

    hideSuggestions();
    searchState.query = query;

    const options = {
        case_sensitive: document.getElementById('caseSensitive')?.checked || false,
        whole_word: document.getElementById('wholeWord')?.checked || false,
        use_fuzzy: document.getElementById('useFuzzy')?.checked !== false
    };
    searchState.options = {
        caseSensitive: options.case_sensitive,
        wholeWord: options.whole_word,
        useFuzzy: options.use_fuzzy
    };
    const nextSearchIdentity = {
        query,
        scope: searchState.scope,
        options,
        filters,
    };
    if (!preservePage && searchState.lastDefinition &&
        selectionKeyForDefinition(searchState.lastDefinition) !==
            selectionKeyForDefinition(nextSearchIdentity)) {
        searchState.currentPage = 1;
        searchState.nextCursor = null;
        searchState.fetchWithCursor = null;
    }
    if (!preservePage) resetResultsScroll();
    const sort = getAdvancedSortDefinition();
    const definition = {
        query,
        scope: searchState.scope,
        sort_by: document.getElementById('sortBy')?.value || 'relevance',
        page: searchState.currentPage,
        similarity_threshold: Number(document.getElementById('similarityThreshold')?.value) || 0.32,
        options,
        filters
    };

    // Keep a selection while paging or changing sort; a different query or
    // filter set gets its own tab-scoped selection. This makes selection
    // durable across refresh and back/forward without leaking it across users.
    const selectionKey = selectionKeyForDefinition(definition);
    if (searchState.activeSelectionKey !== selectionKey) {
        if (searchState.activeSelectionKey !== null) closeReviewPane();
        searchState.activeSelectionKey = selectionKey;
        searchState.selectedIds = readStoredSelection(selectionKey);
    }
    searchState.similarityGroups = null;
    updateSimilarityButton();
    updateSelectionBar();

    // Cancel any previous request so a slower response cannot overwrite the
    // newer query/filter selection.
    activeAdvancedSearchController?.abort();
    cancelServerSearch(activeSearchCorrelationId);
    const correlationId = newSearchCorrelationId();
    activeSearchCorrelationId = correlationId;
    const controller = new AbortController();
    activeAdvancedSearchController = controller;
    const requestSequence = ++advancedSearchRequestSequence;
    showLoading();

    let clientPerfApi = null;
    try {
        clientPerfApi = await import('../modules/search/client-perf.js');
        clientPerfApi.resetClientPerf();
        clientPerfApi.markClientPerf('requestStart');
    } catch (_err) {
        clientPerfApi = null;
    }

    try {
        const params = new URLSearchParams({
            query,
            page: String(searchState.currentPage),
            per_page: String(searchState.resultsPerPage),
            use_advanced: 'true',
            use_fulltext: 'true',
            use_bm25: 'false',
            use_expansion: 'true',
            use_fuzzy: options.use_fuzzy ? 'true' : 'false',
            case_sensitive: options.case_sensitive ? 'true' : 'false',
            whole_word: options.whole_word ? 'true' : 'false',
            hide_duplicates: filters.hide_duplicates ? 'true' : 'false',
            fast_mode: 'true',
            include_snippets: 'false',
            sort_by: sort.sort_by,
            sort_order: sort.sort_order
        });

        // Analyst-categorization search scope (FR-2.x). Always sent so the
        // server can persist the selection in the session (FR-2.3).
        params.set('scope', searchState.scope);

        if (filters.file_type.length > 0) {
            filters.file_type.forEach(type => params.append('file_type', type));
        }
        if (filters.category_id.length > 0) {
            filters.category_id.forEach(id => params.append('category_id', String(id)));
        }
        if (filters.analyst_category_id.length > 0) {
            filters.analyst_category_id.forEach(id => params.append('analyst_category_id', String(id)));
        }
        if (filters.source_id.length > 0) {
            filters.source_id.forEach(id => params.append('source_id', String(id)));
        }
        if (filters.side_id.length > 0) {
            filters.side_id.forEach(id => params.append('side_id', String(id)));
        }
        if (filters.date_from) params.append('date_from', filters.date_from);
        if (filters.date_to) params.append('date_to', filters.date_to);

        // The status checkboxes represent paths.file_status (Read/Unread).
        // Send an explicit sentinel when both are cleared so the API returns
        // no statuses rather than silently dropping the filter.
        if (filters.status.length > 0) {
            filters.status.forEach(status => params.append('status', status));
        } else {
            params.append('status', 'none');
        }

        if (searchState.fetchWithCursor) {
            params.set('cursor', searchState.fetchWithCursor);
        }

        const response = await fetch(`/api/search?${params.toString()}`, {
            signal: controller.signal,
            headers: {
                'X-Request-ID': correlationId,
                'X-Search-Correlation-ID': correlationId,
            },
        });
        if (clientPerfApi) clientPerfApi.markClientPerf('firstByte');
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const data = await response.json();
        if (clientPerfApi) clientPerfApi.markClientPerf('responseComplete');
        searchState.fetchWithCursor = null;
        if (controller.signal.aborted || requestSequence !== advancedSearchRequestSequence) return;

        searchState.correlationId =
            response.headers.get('X-Search-Correlation-ID') || correlationId;
        searchState.searchTime = ((performance.now() - startTime) / 1000).toFixed(2);
        searchState.searchEngine = data.engine || null;
        searchState.nextCursor = data.pagination?.cursor || null;
        if (Array.isArray(data.results)) {
            searchState.results = data.results;
            searchState.pagination = data.pagination || null;
            searchState.totalResults = Number(data.pagination?.total) || data.results.length;
            displayResults(data.results, data.pagination);
            if (clientPerfApi) {
                clientPerfApi.markClientPerf('firstResultRendered');
                searchState.clientPerf = clientPerfApi.clientPerfSnapshot();
            }
            if (data.engine?.include_snippets === false) {
                hydrateSearchSnippets(data.results, requestSequence, clientPerfApi);
            }
        } else {
            searchState.results = [];
            searchState.pagination = null;
            searchState.totalResults = 0;
            displayResults([], null);
        }

        // Keep the address bar and exports tied to the exact definition that
        // produced these rows, not controls the reader may have since edited.
        searchState.lastDefinition = definition;
        persistSearchToUrl(definition);
    } catch (error) {
        if (error.name === 'AbortError' || requestSequence !== advancedSearchRequestSequence) return;
        console.error('Search error:', error);
        alert(tPage('searchError', 'Search error') + ': ' + error.message);
        searchState.results = [];
        searchState.pagination = null;
        searchState.totalResults = 0;
        displayResults([], null);
    } finally {
        if (requestSequence === advancedSearchRequestSequence) {
            activeAdvancedSearchController = null;
            hideLoading();
        }
    }
}

// File-type presentation metadata: bootstrap icon + color-tint CSS class
// shared by the result icon and the type chip.
function fileTypeMeta(type) {
    const map = {
        pdf:          { icon: 'bi-filetype-pdf',  css: 'type-pdf' },
        doc:          { icon: 'bi-filetype-doc',  css: 'type-doc' },
        docx:         { icon: 'bi-filetype-docx', css: 'type-docx' },
        xls:          { icon: 'bi-filetype-xlsx', css: 'type-xls' },
        xlsx:         { icon: 'bi-filetype-xlsx', css: 'type-xlsx' },
        csv:          { icon: 'bi-filetype-csv',  css: 'type-xls' },
        ppt:          { icon: 'bi-filetype-pptx', css: 'type-ppt' },
        pptx:         { icon: 'bi-filetype-pptx', css: 'type-pptx' },
        txt:          { icon: 'bi-filetype-txt',  css: 'type-txt' },
        md:           { icon: 'bi-filetype-md',   css: 'type-txt' },
        html:         { icon: 'bi-filetype-html', css: 'type-code' },
        htm:          { icon: 'bi-filetype-html', css: 'type-code' },
        json:         { icon: 'bi-filetype-json', css: 'type-code' },
        xml:          { icon: 'bi-filetype-xml',  css: 'type-code' },
        js:           { icon: 'bi-filetype-js',   css: 'type-code' },
        py:           { icon: 'bi-filetype-py',   css: 'type-code' },
        jpg:          { icon: 'bi-filetype-jpg',  css: 'type-img' },
        jpeg:         { icon: 'bi-filetype-jpg',  css: 'type-img' },
        png:          { icon: 'bi-filetype-png',  css: 'type-img' },
        gif:          { icon: 'bi-filetype-gif',  css: 'type-img' },
        bmp:          { icon: 'bi-filetype-bmp',  css: 'type-img' },
        webp:         { icon: 'bi-filetype-png',  css: 'type-img' },
        tif:          { icon: 'bi-filetype-tiff', css: 'type-img' },
        tiff:         { icon: 'bi-filetype-tiff', css: 'type-img' },
        mp3:          { icon: 'bi-filetype-mp3',  css: 'type-audio' },
        wav:          { icon: 'bi-filetype-wav',  css: 'type-audio' },
        m4a:          { icon: 'bi-filetype-mp3',  css: 'type-audio' },
        mp4:          { icon: 'bi-filetype-mp4',  css: 'type-video' },
        avi:          { icon: 'bi-filetype-avi',  css: 'type-video' },
        mkv:          { icon: 'bi-filetype-mkv',  css: 'type-video' },
        mov:          { icon: 'bi-filetype-mov',  css: 'type-video' },
        zip:          { icon: 'bi-filetype-zip',  css: 'type-archive' },
        rar:          { icon: 'bi-filetype-rar',  css: 'type-archive' },
        '7z':         { icon: 'bi-filetype-7z',   css: 'type-archive' },
        gz:           { icon: 'bi-filetype-zip',  css: 'type-archive' },
        eml:          { icon: 'bi-envelope',      css: 'type-email' },
        msg:          { icon: 'bi-envelope',      css: 'type-email' },
        email:        { icon: 'bi-envelope',      css: 'type-email' },
    };
    return map[type] || { icon: 'bi-file-earmark', css: '' };
}

function matchLocationMarkup(result) {
    const fields = result?.match_fields || {};
    const inName = fields.file_name === true;
    const inContent = fields.content === true;
    const inMetadata = fields.metadata === true;
    if (!searchState.query || (!inName && !inContent && !inMetadata)) return '';
    const labels = [];
    if (inName && inContent) {
        labels.push(tPage('matchBoth', 'Filename and content match'));
    } else if (inName) {
        labels.push(tPage('matchFilename', 'Filename match'));
    } else if (inContent) {
        labels.push(tPage('matchContent', 'Content match'));
    }
    if (inMetadata) labels.push(tPage('matchMetadata', 'Metadata match'));
    const label = labels.join(' · ');
    return `<div class="result-match-location" title="${escapeAttr(label)}">
        <i class="bi bi-crosshair" aria-hidden="true"></i><span>${escapeHtml(label)}</span>
    </div>`;
}

async function hydrateSearchSnippets(results, requestSequence, clientPerfApi = null) {
    if (!Array.isArray(results) || !results.length || !searchState.query) return;
    const fileIds = results
        .filter((row) => row && row.snippets_pending !== false && row.id)
        .map((row) => row.id);
    if (!fileIds.length) return;

    try {
        const response = await fetch('/api/search/snippets', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': analystCsrftoken(),
                'X-Search-Correlation-ID': searchState.correlationId || activeSearchCorrelationId || '',
            },
            body: JSON.stringify({
                query: searchState.query,
                file_ids: fileIds,
                case_sensitive: !!(searchState.options && searchState.options.caseSensitive),
                whole_word: !!(searchState.options && searchState.options.wholeWord),
            }),
        });
        if (!response.ok || requestSequence !== advancedSearchRequestSequence) return;
        const payload = await response.json();
        if (requestSequence !== advancedSearchRequestSequence) return;
        const snippets = payload.snippets || {};
        searchState.searchEngine = payload.engine || searchState.searchEngine;

        for (const row of searchState.results) {
            const patch = snippets[row.id];
            if (!patch) continue;
            Object.assign(row, patch);
            delete row.snippets_pending;
        }

        let firstContextMarked = false;
        for (const id of Object.keys(snippets)) {
            const card = document.querySelector(`.result-item[data-file-id="${id}"]`);
            if (!card) continue;
            const patch = snippets[id];
            const snippetEl = card.querySelector('.result-snippet');
            if (snippetEl && patch.snippet) {
                snippetEl.innerHTML = highlightQueryTerms(patch.snippet, searchState.query);
                if (clientPerfApi && !firstContextMarked) {
                    clientPerfApi.markClientPerf('firstContextRendered');
                    firstContextMarked = true;
                }
            }
            const body = card.querySelector('.result-body');
            if (body && patch.match_fields && !card.querySelector('.result-match-location')) {
                const loc = matchLocationMarkup({ ...patch, id: Number(id) });
                if (loc) {
                    snippetEl?.insertAdjacentHTML('afterend', loc);
                }
            }
        }
        if (clientPerfApi) {
            clientPerfApi.markClientPerf('hydrationComplete');
            searchState.clientPerf = clientPerfApi.clientPerfSnapshot();
            const timeEl = document.getElementById('searchTime');
            if (timeEl && searchState.clientPerf) {
                const perfLine = clientPerfApi.formatClientPerfLine(searchState.clientPerf);
                if (perfLine && !timeEl.textContent.includes('TTFR')) {
                    timeEl.textContent = `${timeEl.textContent} · ${perfLine}`;
                }
            }
        }
        if (payload.engine?.line_matcher === 'ripgrep') {
            const timeEl = document.getElementById('searchTime');
            if (timeEl && searchState.searchEngine?.ripgrep?.version) {
                const shortVersion = String(searchState.searchEngine.ripgrep.version)
                    .replace(/^ripgrep\s+/i, 'rg ');
                if (!timeEl.textContent.includes('rg ')) {
                    timeEl.textContent = `${timeEl.textContent} · ${shortVersion}`;
                }
            }
        }
    } catch (err) {
        console.warn('Snippet hydration failed', err);
    }
}

// Display results
function displayResults(results, pagination) {
    const section = document.getElementById('searchResultsSection');
    const container = document.getElementById('resultsContainer');
    const countEl = document.getElementById('resultsCount');
    const timeEl = document.getElementById('searchTime');
    
    if (!section || !container) return;
    
    // Cleared (not 'block') so the desktop stylesheet can lay the section out as
    // a fixed-height panel; the hidden state is still set inline by resetAllFilters.
    section.style.display = '';
    
    if (countEl) {
        const approx = pagination?.total_is_approximate;
        const totalVal = pagination?.total ?? searchState.totalResults;
        const display = totalVal == null
            ? tPage('manyResults', 'Many results')
            : (approx ? '~' : '') + Number(totalVal).toLocaleString();
        countEl.textContent = display;
        if (approx) {
            countEl.title = tPage('approximateCount', 'Total count timed out; showing approximate or lower bound');
        } else {
            countEl.removeAttribute('title');
        }
    }
    
    if (timeEl) {
        const pageDataEl = document.getElementById('search-advanced-page-data');
        let timeText = `in ${searchState.searchTime} seconds`;
        if (pageDataEl) {
            try {
                const data = JSON.parse(pageDataEl.textContent);
                timeText = data.translations?.inSeconds?.replace('{seconds}', searchState.searchTime) || timeText;
            } catch (e) {}
        }
        const engine = searchState.searchEngine;
        if (engine?.line_matcher === 'ripgrep' && engine.ripgrep?.version) {
            const shortVersion = String(engine.ripgrep.version).replace(/^ripgrep\s+/i, 'rg ');
            timeText = `${timeText} · ${shortVersion}`;
        }
        timeEl.textContent = timeText;
    }
    
    if (results.length === 0) {
        container.innerHTML = `
            <div class="results-empty-state">
                <i class="bi bi-search" aria-hidden="true"></i>
                <p>${escapeHtml(tPage('noResultsFound', 'No results found matching your criteria'))}</p>
            </div>
        `;
        return;
    }
    
    const renderResultCard = (result) => {
        if (!result || typeof result !== 'object') return '';
        const fileId = Number(result.id);
        if (!Number.isSafeInteger(fileId) || fileId < 1) return '';
        const snippetPending = result.snippets_pending === true;
        const snippet = result.snippet || result.file_name || '';
        const highlightedSnippet = snippetPending
            ? `<span class="snippet-loading">${escapeHtml(tPage('loadingSnippet', 'Loading match context…'))}</span>`
            : highlightQueryTerms(snippet, searchState.query);
        const matchLocation = snippetPending ? '' : matchLocationMarkup(result);

        // File-type presentation: color-coded icon + uppercase chip share
        // one tint family per type (see search-advanced.css .type-*).
        const fileType = (result.file_type || '').toLowerCase();
        const typeInfo = fileTypeMeta(fileType);

        // Analyst categories (manual layer) - always rendered in their own,
        // visually distinct badge group, never merged with smart categories
        // (FR-1.4).
        const analystBadges = (result.analyst_categories || []).length > 0 ? `
            <span class="analyst-badges" title="${escapeAttr(tPage('analystCategories', 'Analyst'))}">
                ${(result.analyst_categories || []).map(cat =>
                    `<span class="badge analyst-category-badge"><i class="bi bi-person-fill me-1"></i>${escapeHtml(cat)}</span>`
                ).join('')}
            </span>
        ` : '';

        // Smart categories (system layer) - separate badge group
        const smartBadges = result.categories && result.categories.length > 0 ? `
            <span class="smart-badges" title="${escapeAttr(tPage('smartCategories', 'Smart'))}">
                ${result.categories.map(cat => `<span class="badge bg-secondary"><i class="bi bi-tags-fill me-1"></i>${escapeHtml(cat)}</span>`).join('')}
            </span>
        ` : '';

        return `
            <div class="result-item ${searchState.selectedIds.has(fileId) ? 'result-selected' : ''}" data-file-id="${fileId}">
                <div class="result-select" data-on-click="event.stopPropagation()">
                    <input class="form-check-input result-checkbox" type="checkbox"
                           ${searchState.selectedIds.has(fileId) ? 'checked' : ''}
                           data-on-change="toggleResultSelection(${fileId}, this.checked)"
                           title="${escapeAttr(tPage('selectForCategorization', 'Select for manual categorization'))}"
                           aria-label="${escapeAttr(tPage('selectFileForCategorization', 'Select {file} for manual categorization').replace('{file}', result.file_name || 'file'))}">
                </div>
                <div class="result-body" data-on-click="openResultInNewTab(event, ${fileId})"
                     title="${escapeAttr(tPage('openInNewTab', 'Open in new tab'))}">
                    <div class="result-title-row">
                        <span class="result-file-icon ${typeInfo.css}" title="${escapeAttr(fileType || '')}">
                            <i class="bi ${typeInfo.icon}" aria-hidden="true"></i>
                        </span>
                        <a class="result-title result-title-link" href="${fileDetailHref(fileId)}"
                           target="_blank" rel="noopener"
                           data-on-click="event.stopPropagation()">${escapeHtml(result.file_name || tPage('untitled', 'Untitled'))}</a>
                        ${fileType ? `<span class="result-type-chip ${typeInfo.css}">${escapeHtml(fileType)}</span>` : ''}
                        ${result.relevance_score ? `<span class="relevance-badge">${Math.round(result.relevance_score * 100)}%</span>` : ''}
                    </div>
                    ${snippet ? `<div class="result-snippet">${highlightedSnippet}</div>` : ''}
                    ${matchLocation}
                    <div class="result-meta">
                        <span class="result-meta-item">
                            <i class="bi bi-building" aria-hidden="true"></i>
                            ${escapeHtml(result.source_name || tPage('unknown', 'unknown'))}
                        </span>
                        <span class="result-meta-item">
                            <i class="bi bi-calendar" aria-hidden="true"></i>
                            ${result.file_date ? new Date(result.file_date).toLocaleDateString() : tPage('notAvailable', 'N/A')}
                        </span>
                        ${result.file_size ? `
                            <span class="result-meta-item">
                                <i class="bi bi-hdd" aria-hidden="true"></i>
                                ${formatFileSize(result.file_size)}
                            </span>
                        ` : ''}
                    </div>
                    ${(analystBadges || smartBadges) ? `
                        <div class="result-badges">
                            ${analystBadges}
                            ${smartBadges}
                        </div>
                    ` : ''}
                    <div class="result-hover-actions">
                        <button type="button" class="result-action-btn result-action-review"
                                data-on-click="reviewResultInPane(${fileId}, event)"
                                title="${escapeAttr(tPage('reviewInPane', 'Review beside results'))}"
                                aria-label="${escapeAttr(tPage('reviewInPane', 'Review beside results'))}">
                            <i class="bi bi-layout-split" aria-hidden="true"></i>
                        </button>
                        <button type="button" class="result-action-btn result-action-preview"
                                data-on-click="showFilePreview(${fileId}); event.stopPropagation();"
                                title="${escapeAttr(tPage('preview', 'Quick preview (stays on this page)'))}"
                                aria-label="${escapeAttr(tPage('preview', 'Quick preview (stays on this page)'))}">
                            <i class="bi bi-eye" aria-hidden="true"></i>
                        </button>
                        <button type="button" class="result-action-btn result-action-export"
                                data-on-click="exportSingleFileText(${fileId}, event)"
                                title="${escapeAttr(tPage('exportText', 'Download extracted text'))}"
                                aria-label="${escapeAttr(tPage('exportText', 'Download extracted text'))}">
                            <i class="bi bi-file-earmark-arrow-down" aria-hidden="true"></i>
                        </button>
                        <button type="button" class="result-action-btn result-action-open"
                                data-on-click="openResultInNewTab(event, ${fileId})"
                                title="${escapeAttr(tPage('openInNewTab', 'Open in new tab'))}"
                                aria-label="${escapeAttr(tPage('openInNewTab', 'Open in new tab'))}">
                            <i class="bi bi-box-arrow-up-right" aria-hidden="true"></i>
                        </button>
                    </div>
                </div>
            </div>
        `;
    };

    if (searchState.similarityGroups?.length) {
        const resultsById = new Map(results.map(result => [Number(result.id), result]));
        const renderedIds = new Set();
        const groupsMarkup = searchState.similarityGroups.map(group => {
            const groupResults = (group.file_ids || [])
                .map(id => resultsById.get(Number(id)))
                .filter(Boolean);
            groupResults.forEach(result => renderedIds.add(Number(result.id)));
            if (!groupResults.length) return '';
            const groupName = tPage('similarityGroup', 'Similarity group {group}')
                .replace('{group}', String(group.group_id));
            const similarity = Number(group.similarity);
            const score = Number.isFinite(similarity)
                ? `<span class="similarity-score">${Math.round(similarity * 100)}% ${escapeHtml(tPage('similarity', 'similarity'))}</span>`
                : '';
            return `
                <section class="similarity-group" aria-label="${escapeAttr(groupName)}">
                    <h3 class="similarity-group-heading">
                        <span>${escapeHtml(groupName)} (${groupResults.length})</span>${score}
                    </h3>
                    <div class="similarity-group-results">${groupResults.map(renderResultCard).join('')}</div>
                </section>`;
        }).join('');
        const ungrouped = results.filter(result => !renderedIds.has(Number(result.id)));
        container.innerHTML = `
            <p class="similarity-scope-note">${escapeHtml(tPage('groupingPageScope', 'Similarity groups apply to this results page only.'))}</p>
            ${groupsMarkup}
            ${ungrouped.length ? `<section class="similarity-group"><h3 class="similarity-group-heading">${escapeHtml(tPage('otherResults', 'Other results'))}</h3><div class="similarity-group-results">${ungrouped.map(renderResultCard).join('')}</div></section>` : ''}
        `;
    } else {
        container.innerHTML = results.map(renderResultCard).join('');
    }

    // Sync the select-all checkbox with the fresh result page
    syncSelectAllCheckbox();
    
    // Update pagination
    if (pagination && pagination.total_pages > 1) {
        updatePagination(pagination);
    } else {
        const paginationEl = document.getElementById('pagination');
        if (paginationEl) paginationEl.innerHTML = '';
    }
}

// ====================================================================
// Analyst-driven manual categorization (FR-1.2, FR-1.3, FR-1.5)
// All actions below target ANALYST categories only - the smart
// (system-generated) taxonomy is never read or written here (FR-1.4).
// ====================================================================

// Toggle one result's selection checkbox (FR-1.2 - subset selection)
function toggleResultSelection(fileId, checked) {
    if (checked) {
        searchState.selectedIds.add(fileId);
    } else {
        searchState.selectedIds.delete(fileId);
    }
    const item = document.querySelector(`.result-item[data-file-id="${fileId}"]`);
    if (item) item.classList.toggle('result-selected', checked);
    persistSelectedIds();
    syncSelectAllCheckbox();
    updateSelectionBar();
}

// Select/deselect every result on the current page (FR-1.2 - select all)
function toggleSelectAllResults(checked) {
    searchState.results.forEach(result => {
        if (checked) {
            searchState.selectedIds.add(result.id);
        } else {
            searchState.selectedIds.delete(result.id);
        }
        const checkbox = document.querySelector(
            `.result-item[data-file-id="${result.id}"] .result-checkbox`);
        if (checkbox) checkbox.checked = checked;
        const item = document.querySelector(`.result-item[data-file-id="${result.id}"]`);
        if (item) item.classList.toggle('result-selected', checked);
    });
    persistSelectedIds();
    updateSelectionBar();
}

// Keep the select-all checkbox in sync with the visible page
function syncSelectAllCheckbox() {
    const selectAll = document.getElementById('selectAllResults');
    if (!selectAll || searchState.results.length === 0) return;
    const allSelected = searchState.results.every(r => searchState.selectedIds.has(r.id));
    const someSelected = searchState.results.some(r => searchState.selectedIds.has(r.id));
    selectAll.checked = allSelected;
    selectAll.indeterminate = !allSelected && someSelected;
}

// Show/hide the action bars and update their counters.
function updateSelectionBar() {
    const count = searchState.selectedIds.size;
    const bar = document.getElementById('analystCategorizationBar');
    const countEl = document.getElementById('analystSelectedCount');
    if (countEl) countEl.textContent = count;
    if (bar) bar.style.display = count > 0 && searchState.canCategorize ? 'flex' : 'none';

    const exportBar = document.getElementById('selectedDocumentActions');
    const exportCount = document.getElementById('selectedDocumentCount');
    if (exportCount) exportCount.textContent = count;
    if (exportBar) exportBar.style.display = count > 0 ? 'flex' : 'none';
}

// Clear the current selection
function clearResultSelection() {
    searchState.selectedIds.clear();
    persistSelectedIds();
    document.querySelectorAll('.result-checkbox').forEach(cb => cb.checked = false);
    document.querySelectorAll('.result-item.result-selected').forEach(el =>
        el.classList.remove('result-selected'));
    syncSelectAllCheckbox();
    updateSelectionBar();
}

// CSRF header helper for the categorization POSTs
function analystCsrftoken() {
    return document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
}

// Assign the chosen analyst category to every selected file (FR-1.3).
// The current search query travels with the request so each assignment is
// auditable with its originating query (FR-1.5).
async function assignAnalystCategory() {
    if (!searchState.canCategorize) return;
    const selected = Array.from(searchState.selectedIds);
    if (selected.length === 0) {
        alert(tPage('selectFilesFirst', 'Select one or more files first'));
        return;
    }

    const categorySelect = document.getElementById('analystCategorySelect');
    const newCategoryInput = document.getElementById('newAnalystCategoryInput');
    const categoryId = categorySelect ? parseInt(categorySelect.value) : NaN;
    const newCategoryName = newCategoryInput ? newCategoryInput.value.trim() : '';

    if (!newCategoryName && (isNaN(categoryId) || !categoryId)) {
        alert(tPage('chooseOrCreateCategory', 'Choose an analyst category or type a new one'));
        return;
    }

    const payload = {
        path_ids: selected,
        source_query: searchState.query || document.getElementById('mainSearchInput')?.value?.trim() || ''
    };
    if (newCategoryName) {
        // Create the analyst-defined category at the point of assignment
        payload.category_name = newCategoryName;
        payload.create_category = true;
    } else {
        payload.category_id = categoryId;
    }

    try {
        const response = await fetch('/api/analyst/assign', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': analystCsrftoken()
            },
            body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (!response.ok || !data.success) {
            throw new Error(data.error || 'Request failed');
        }

        // Update the in-memory results so the analyst badges reflect the
        // action immediately (and the scope hint stays honest).
        const categoryName = data.category_name || newCategoryName;
        searchState.results.forEach(result => {
            if (searchState.selectedIds.has(result.id)) {
                result.analyst_categories = result.analyst_categories || [];
                if (categoryName && !result.analyst_categories.includes(categoryName)) {
                    result.analyst_categories.push(categoryName);
                }
            }
        });
        displayResults(searchState.results, {
            total_pages: Math.ceil(searchState.totalResults / searchState.resultsPerPage) || 1
        });

        if (newCategoryInput) newCategoryInput.value = '';
        if (categorySelect) categorySelect.value = '';
        // Refresh the analyst category lists (a new category may exist now)
        await loadAnalystCategories();

        showAnalystToast(
            tPage('assignedToast', 'Assigned "{category}" to {count} file(s)')
                .replace('{category}', categoryName)
                .replace('{count}', String(data.assigned ?? selected.length))
        );
        // The assignment was audit-logged server-side (FR-1.5).
        clearResultSelection();
    } catch (error) {
        console.error('Analyst categorization failed:', error);
        alert(tPage('analystActionError', 'Analyst categorization failed') + ': ' + error.message);
    }
}

// Remove analyst categories from the selected files (NFR-3 - reversibility).
// Only the manual layer is touched; smart categories are never modified.
async function removeAnalystCategoriesFromSelection() {
    if (!searchState.canCategorize) return;
    const selected = Array.from(searchState.selectedIds);
    if (selected.length === 0) {
        alert(tPage('selectFilesFirst', 'Select one or more files first'));
        return;
    }

    const confirmed = confirm(
        tPage('removeAllConfirm', 'Remove all analyst categories from {count} selected file(s)? They will return to "uncategorized" for analyst search scope. Smart categories are not affected.')
            .replace('{count}', String(selected.length)));
    if (!confirmed) return;

    try {
        const response = await fetch('/api/analyst/remove', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': analystCsrftoken()
            },
            body: JSON.stringify({
                path_ids: selected,
                source_query: searchState.query || ''
            })
        });
        const data = await response.json();
        if (!response.ok || !data.success) {
            throw new Error(data.error || 'Request failed');
        }

        searchState.results.forEach(result => {
            if (searchState.selectedIds.has(result.id)) {
                result.analyst_categories = [];
            }
        });
        displayResults(searchState.results, {
            total_pages: Math.ceil(searchState.totalResults / searchState.resultsPerPage) || 1
        });

        showAnalystToast(tPage('analystRemoveSuccess', 'Analyst categories removed'));
        clearResultSelection();
    } catch (error) {
        console.error('Analyst category removal failed:', error);
        alert(tPage('analystActionError', 'Analyst categorization failed') + ': ' + error.message);
    }
}

// Lightweight toast for categorization feedback
function showAnalystToast(message) {
    // One notification stack for the whole app: use it when the shell has it.
    if (window.Toast) {
        window.Toast.success(message);
        return;
    }
    let toast = document.getElementById('analystActionToast');
    if (!toast) {
        toast = document.createElement('div');
        toast.id = 'analystActionToast';
        toast.className = 'analyst-action-toast';
        document.body.appendChild(toast);
    }
    toast.textContent = message;
    toast.classList.add('visible');
    clearTimeout(toast._hideTimer);
    toast._hideTimer = setTimeout(() => toast.classList.remove('visible'), 4000);
}

// Escape and highlight query terms without ever treating source text as HTML.
function highlightQueryTerms(text, query, targetAbsoluteOffset = null, chunkOffset = 0,
    caseSensitive = false, wholeWord = false) {
    const source = String(text == null ? '' : text);
    const terms = parseQueryTerms(String(query || '')).filter(Boolean);
    if (!terms.length) return escapeHtml(source);

    const alternatives = [...new Set(terms)]
        .sort((a, b) => b.length - a.length)
        .map(term => term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
    const expression = `(?:${alternatives.join('|')})`;
    const matcherSource = wholeWord
        ? `(?<![\\p{L}\\p{N}_])${expression}(?![\\p{L}\\p{N}_])`
        : expression;
    const matcher = new RegExp(matcherSource, caseSensitive ? 'gu' : 'giu');
    let output = '';
    let lastIndex = 0;
    for (const match of source.matchAll(matcher)) {
        output += escapeHtml(source.slice(lastIndex, match.index));
        const isCurrent = targetAbsoluteOffset !== null
            && chunkOffset + match.index === targetAbsoluteOffset;
        output += `<mark${isCurrent ? ' class="review-current-match"' : ''}>${escapeHtml(match[0])}</mark>`;
        lastIndex = match.index + match[0].length;
    }
    return output + escapeHtml(source.slice(lastIndex));
}

// Parse positive terms and quoted phrases using the same simple operator
// grammar as the server; NOT operands are intentionally not highlighted.
function parseQueryTerms(query) {
    const terms = [];
    const quoted = String(query || '').match(/"([^"]+)"/g);
    const unquoted = String(query || '').replace(/"([^"]+)"/g, ' ').trim();

    if (quoted) {
        quoted.forEach(phrase => {
            const value = phrase.slice(1, -1).trim();
            if (value) terms.push(value);
        });
    }

    if (unquoted) {
        const parts = unquoted.split(/\s+(AND|OR|NOT)\s+/i);
        let operator = 'AND';
        for (const part of parts) {
            if (/^(AND|OR|NOT)$/i.test(part.trim())) {
                operator = part.trim().toUpperCase();
                continue;
            }
            if (operator === 'NOT') continue;
            const clean = part.replace(/[()]/g, ' ').trim();
            if (clean) terms.push(...clean.split(/\s+/).filter(Boolean));
            operator = 'AND';
        }
    }

    return [...new Set(terms)];
}

function sort_by_relevance_desc() {
    const sort = getAdvancedSortDefinition();
    return sort.sort_by === 'relevance' && String(sort.sort_order).toLowerCase() === 'desc';
}

// Update pagination
function updatePagination(pagination) {
    const paginationEl = document.getElementById('pagination');
    if (!paginationEl) return;
    
    import('../modules/rendering/unified-pagination.js').then(module => {
        module.renderUnifiedPagination({
            currentPage: pagination.page,
            totalPages: pagination.total_pages,
            containerId: 'pagination',
            onPageChange: (page) => {
                const prev = searchState.currentPage;
                const goingForward = page > prev;
                if (
                    goingForward
                    && searchState.nextCursor
                    && pagination?.cursor_supported
                    && sort_by_relevance_desc()
                ) {
                    searchState.fetchWithCursor = searchState.nextCursor;
                } else {
                    searchState.fetchWithCursor = null;
                }
                searchState.currentPage = page;
                resetResultsScroll();
                executeAdvancedSearch(true);
            },
            urlParams: {},
            showInfo: true,
            showJump: pagination.total_pages > 5
        });
    }).catch(err => {
        console.error('Error loading pagination:', err);
    });
}

// Show loading
function showLoading() {
    const overlay = document.getElementById('searchLoadingOverlay');
    if (overlay) overlay.style.display = 'flex';
}

// Hide loading
function hideLoading() {
    const overlay = document.getElementById('searchLoadingOverlay');
    if (overlay) overlay.style.display = 'none';
}

// Get active filters count
function getActiveFiltersCount() {
    let count = 0;
    count += document.getElementById('fileType').selectedOptions.length;
    count += document.getElementById('categoriesSelect').selectedOptions.length;
    count += document.getElementById('analystCategoriesFilter')?.selectedOptions.length || 0;
    count += document.getElementById('sourcesSelect').selectedOptions.length;
    count += document.getElementById('sidesSelect').selectedOptions.length;
    if (document.getElementById('dateFrom').value) count++;
    if (document.getElementById('dateTo').value) count++;
    if (document.getElementById('hideDuplicates')?.checked) count++;
    // Read-only is the default; both checked means all statuses (no filter).
    // An explicit unread-only or empty selection remains an active filter.
    if (!document.getElementById('statusRead').checked) count++;
    return count;
}

// Reset all filters
function resetAllFilters() {
    document.getElementById('mainSearchInput').value = '';
    searchState.query = '';
    clearAllFilters();
    document.getElementById('caseSensitive').checked = false;
    document.getElementById('wholeWord').checked = false;
    document.getElementById('useFuzzy').checked = true;
    document.getElementById('searchResultsSection').style.display = 'none';
    updateFilterChips();
}

// Feeling lucky (get first result) — opens in a NEW tab so the search
// page (and its results) stay intact.
async function feelingLucky() {
    const chosenPerPage = searchState.resultsPerPage;
    searchState.resultsPerPage = 1;
    await executeAdvancedSearch();
    if (searchState.results.length > 0) {
        window.open(fileDetailHref(searchState.results[0].id), '_blank', 'noopener');
    }
    searchState.resultsPerPage = chosenPerPage;
}

// Load search history
async function loadSearchHistory() {
    try {
        const response = await fetch('/api/search/history?limit=10');
        const data = await response.json();
        if (data.history) {
            searchState.searchHistory = data.history;
        }
    } catch (error) {
        console.error('Error loading search history:', error);
    }
}

// Save to search history
async function saveToSearchHistory(query, filters) {
    if (!query || !query.trim()) return; // Don't save empty queries
    
    try {
        // AUDIT-CSRF-01: search history was never saved without the token.
        const response = await fetch('/api/search/history', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': (window.CSRF && window.CSRF.getToken()) || document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '' },
            body: JSON.stringify({ 
                query: query.trim(),
                filters: filters || {},
                result_count: searchState.totalResults || 0
            })
        });
        
        if (!response.ok) {
            // Don't show error to user, just log it
            const errorData = await response.json().catch(() => ({}));
            console.warn('Could not save search history:', errorData.error || 'Unknown error');
        }
    } catch (error) {
        // Silently fail - history saving is not critical
        console.warn('Error saving search history:', error);
    }
}

// Sort results
function sortResults() {
    searchState.currentPage = 1;
    executeAdvancedSearch();
}

// Results per page (20 / 50 / 100 / 200 / 500). The choice is sent as per_page,
// so the page, the exports and the server's paging all use the same size.
const RESULTS_PAGE_SIZES = [20, 50, 100, 200, 500];

function changeResultsPerPage(value) {
    const size = Number(value);
    if (!RESULTS_PAGE_SIZES.includes(size)) return;
    searchState.resultsPerPage = size;
    // Cursors belong to the old page size; start again from page 1.
    searchState.currentPage = 1;
    searchState.nextCursor = null;
    searchState.fetchWithCursor = null;
    executeAdvancedSearch();
}

// The results list is the only scroll region on desktop; return it to the top
// whenever the content it shows is replaced (new search, page turn, sort).
function resetResultsScroll() {
    const container = document.getElementById('resultsContainer');
    if (container) container.scrollTop = 0;
}

function toggleDuplicateFilter(_enabled) {
    searchState.currentPage = 1;
    Toast.info(tPage('duplicateFilterChanged', 'Updating duplicate filter…'));
    executeAdvancedSearch();
}

// Export results in various formats
async function chooseDestinationForExport(filename) {
    try {
        return await chooseExportDestination(filename);
    } catch (error) {
        // A browser without an available native picker can still use its own
        // download manager; the data never leaves the normal same-origin flow.
        console.warn('Save location picker unavailable:', error);
        return null;
    }
}

/**
 * The filters as they stand on screen.
 *
 * Read in one place because two operations depend on them agreeing: the search
 * the reader is looking at, and the export of its result set. An export built
 * from a second reading of the same controls is an export of a different
 * query.
 */
function collectFilters() {
    const sourceIds = Array.from(document.getElementById('sourcesSelect').selectedOptions)
        .map(o => parseInt(o.value)).filter(id => !isNaN(id));
    const sideIds = Array.from(document.getElementById('sidesSelect').selectedOptions)
        .map(o => parseInt(o.value)).filter(id => !isNaN(id));
    const filters = {
        file_type: Array.from(document.getElementById('fileType').selectedOptions)
            .map(o => o.value).filter(v => v),
        category_id: Array.from(document.getElementById('categoriesSelect').selectedOptions)
            .map(o => parseInt(o.value)).filter(id => !isNaN(id)),
        analyst_category_id: Array.from(document.getElementById('analystCategoriesFilter')?.selectedOptions || [])
            .map(o => parseInt(o.value)).filter(v => !isNaN(v)),
        source_id: sourceIds,
        side_id: sideIds,
        date_from: document.getElementById('dateFrom').value || null,
        date_to: document.getElementById('dateTo').value || null,
        hide_duplicates: !!document.getElementById('hideDuplicates')?.checked,
        status: [],
    };
    if (document.getElementById('statusRead').checked) filters.status.push('Read');
    if (document.getElementById('statusUnread').checked) filters.status.push('Unread');
    return filters;
}

function escapeExportAttr(value) {
    return String(value == null ? '' : value)
        .replace(/&/g, '&amp;')
        .replace(/"/g, '&quot;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
}

function escapeExportText(value) {
    return String(value == null ? '' : value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
}

async function loadSearchExportColumns() {
    const list = document.getElementById('searchExportColumnList');
    if (!list) return;
    try {
        const response = await fetch('/api/search/export/columns', {
            credentials: 'same-origin',
            headers: { Accept: 'application/json' },
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.success || !Array.isArray(data.columns)) {
            throw new Error(data.error || 'Could not load export columns');
        }
        list.innerHTML = data.columns.map((column) => {
            const key = escapeExportAttr(column.key || '');
            const label = escapeExportText(column.label || column.key || '');
            const checked = column.default === false ? '' : ' checked';
            return `<label class="dropdown-item-text ut-export-column">`
                + `<input type="checkbox" class="form-check-input ut-export-column-check"`
                + ` data-export-column="${key}"${checked}>`
                + ` ${label}</label>`;
        }).join('');
    } catch (error) {
        console.warn('Search export columns unavailable:', error);
        list.innerHTML = `<div class="dropdown-item-text text-muted small px-3">`
            + `${escapeExportText(tPage('exportColumnsUnavailable', 'Could not load columns.'))}</div>`;
    }
}

function chosenSearchExportColumns() {
    return Array.from(document.querySelectorAll('#searchExportColumnList .ut-export-column-check:checked'))
        .map((box) => box.getAttribute('data-export-column'))
        .filter(Boolean);
}

function setSearchExportColumns(checked) {
    document.querySelectorAll('#searchExportColumnList .ut-export-column-check').forEach((box) => {
        box.checked = !!checked;
    });
}

function chosenSearchExportScope() {
    const checked = document.querySelector('#searchExportMenu .ut-export-scope-check:checked');
    return checked ? checked.value : 'filtered';
}

/**
 * POST/GET with exponential backoff on HTTP 429 (rate-limited search exports).
 */
async function fetchWithBackoff(url, options = {}, { retries = 3, baseDelayMs = 800 } = {}) {
    let response = null;
    for (let attempt = 0; attempt <= retries; attempt++) {
        response = await fetch(url, options);
        if (response.status !== 429 || attempt === retries) {
            return response;
        }
        const retryAfterHeader = response.headers.get('Retry-After');
        const retryAfterSec = Number(retryAfterHeader);
        const waitMs = Number.isFinite(retryAfterSec) && retryAfterSec > 0
            ? retryAfterSec * 1000
            : baseDelayMs * Math.pow(2, attempt);
        await new Promise((resolve) => setTimeout(resolve, waitMs));
    }
    return response;
}

function resolveSearchExportScope(scopeOverride = null) {
    // UI values: filtered | page | all. Server scopes: filtered | page | dataset.
    // "all" means the entire database: drop search terms and clear view filters.
    const uiScope = scopeOverride || chosenSearchExportScope();
    if (uiScope === 'page') return { exportScope: 'page', allData: false };
    if (uiScope === 'all' || uiScope === 'dataset') {
        return { exportScope: 'dataset', allData: true };
    }
    return { exportScope: 'filtered', allData: false };
}

async function exportResults(format = 'csv', scopeOverride = null) {
    if (searchState.results.length === 0 && chosenSearchExportScope() !== 'all'
            && scopeOverride !== 'all' && scopeOverride !== 'dataset') {
        Toast.info(tPage('nothingToExport', 'No results to export.'));
        return;
    }

    // The definition that produced the visible rows, not the current controls
    // and never the browser's result array. The server re-runs this query.
    const definition = searchState.lastDefinition;
    const { exportScope, allData } = resolveSearchExportScope(scopeOverride);
    if (!allData && !definition) {
        Toast.info(tPage('nothingToExport', 'No results to export.'));
        return;
    }
    const columns = chosenSearchExportColumns();
    if (!columns.length) {
        Toast.warning(tPage('exportSelectColumns', 'Please select at least one column to export.'));
        return;
    }
    const filters = allData ? {} : (definition.filters || {});
    const options = allData ? {} : (definition.options || {});
    const sort = getAdvancedSortDefinition(allData ? 'name' : definition.sort_by);
    const proposedName = allData
        ? `all_data_${new Date().toISOString().slice(0, 10)}`
        : `search_results_${new Date().toISOString().slice(0, 10)}`;
    const filename = await window.prompt(
        tPage('exportFilenamePrompt', 'Name your export (leave blank for an automatic name):'),
        proposedName);
    if (filename === null) return;
    const extension = format === 'excel' ? 'xlsx' : format;
    const suggestedExportName = ensureExportExtension(
        filename.trim() || proposedName, extension, proposedName);
    const destination = await chooseDestinationForExport(suggestedExportName);
    if (destination === false) return;
    const payload = {
        query: allData ? '' : (definition.query || ''),
        export_scope: exportScope,
        analyst_scope: allData ? 'all' : (definition.scope || 'uncategorized'),
        format,
        filename: filename.trim(),
        page: allData ? 1 : (definition.page || searchState.currentPage || 1),
        per_page: searchState.resultsPerPage,
        hide_duplicates: allData ? false : !!filters.hide_duplicates,
        sort_by: sort.sort_by,
        sort_order: sort.sort_order,
        source_ids: filters.source_id || [],
        side_ids: filters.side_id || [],
        category_ids: filters.category_id || [],
        analyst_category_ids: filters.analyst_category_id || [],
        file_type: filters.file_type || [],
        status: filters.status || [],
        date_from: filters.date_from || null,
        date_to: filters.date_to || null,
        use_advanced: true,
        use_fulltext: true,
        use_bm25: true,
        use_expansion: true,
        use_fuzzy: allData ? true : options.use_fuzzy !== false,
        case_sensitive: allData ? false : options.case_sensitive === true,
        whole_word: allData ? false : options.whole_word === true,
        columns,
    };

    try {
        const response = await fetchWithBackoff('/api/search/export', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '',
            },
            body: JSON.stringify(payload),
        });

        if (!response.ok) {
            const problem = await response.json().catch(() => ({}));
            Toast.error(problem.error || tPage('exportFailed', 'The export could not be produced.'));
            return;
        }

        const blob = await response.blob();
        const rows = response.headers.get('X-Export-Rows') || '';
        const truncated = response.headers.get('X-Export-Truncated') === 'true';
        const disposition = response.headers.get('Content-Disposition') || '';
        const named = disposition.match(/filename\*?=(?:UTF-8''|")?([^";]+)/i);
        const downloadName = named
            ? decodeURIComponent(named[1].replace(/"/g, ''))
            : `search_export_${new Date().toISOString().split('T')[0]}.${extension}`;
        await saveExportBlob(blob, downloadName, destination);

        // Say what was exported, and say it out loud when it was capped.
        if (truncated) {
            Toast.warning(tPage('exportTruncated',
                'The export reached the size limit; narrow the query to get everything.'),
                {detail: `${rows} rows`});
        } else {
            Toast.success(tPage('exportReady', 'Export ready.'), {detail: `${rows} rows`});
        }
    } catch (error) {
        Toast.error(tPage('exportFailed', 'The export could not be produced.'));
    }
}

async function exportMatchingFilenames(format = 'csv') {
    const { exportScope, allData } = resolveSearchExportScope();
    const definition = searchState.lastDefinition;
    if (!allData && (!definition || searchState.totalResults === 0)) {
        Toast.info(tPage('nothingToExport', 'No results to export.'));
        return;
    }
    const filters = allData ? {} : (definition.filters || {});
    const options = allData ? {} : (definition.options || {});
    const sort = getAdvancedSortDefinition(allData ? 'name' : definition.sort_by);
    const filename = await window.prompt(
        tPage('exportFilenamePrompt', 'Name your export (leave blank for an automatic name):'),
        allData
            ? `all_filenames_${new Date().toISOString().slice(0, 10)}`
            : `matching_${new Date().toISOString().slice(0, 10)}`);
    if (filename === null) return;
    const extension = format === 'excel' ? 'xlsx' : 'csv';
    const suggestedExportName = ensureExportExtension(
        `${filename.trim() || 'matching'}_filenames`, extension, 'matching_filenames');
    const destination = await chooseDestinationForExport(suggestedExportName);
    if (destination === false) return;

    const payload = {
        query: allData ? '' : (definition.query || ''),
        export_scope: exportScope === 'page' ? 'filtered' : exportScope,
        analyst_scope: allData ? 'all' : (definition.scope || 'uncategorized'),
        format: format === 'excel' ? 'excel' : 'csv',
        filename: filename.trim(),
        page: allData ? 1 : (definition.page || searchState.currentPage || 1),
        per_page: searchState.resultsPerPage,
        hide_duplicates: allData ? false : !!filters.hide_duplicates,
        sort_by: sort.sort_by,
        sort_order: sort.sort_order,
        source_ids: filters.source_id || [],
        side_ids: filters.side_id || [],
        category_ids: filters.category_id || [],
        analyst_category_ids: filters.analyst_category_id || [],
        file_type: filters.file_type || [],
        status: filters.status || [],
        date_from: filters.date_from || null,
        date_to: filters.date_to || null,
        use_advanced: true,
        use_fulltext: true,
        use_bm25: true,
        use_expansion: true,
        use_fuzzy: allData ? true : options.use_fuzzy !== false,
        case_sensitive: allData ? false : options.case_sensitive === true,
        whole_word: allData ? false : options.whole_word === true,
    };

    try {
        const response = await fetchWithBackoff('/api/search/export-filenames', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '',
            },
            body: JSON.stringify(payload),
        });
        if (!response.ok) {
            const problem = await response.json().catch(() => ({}));
            throw new Error(problem.error || `HTTP ${response.status}`);
        }
        const blob = await response.blob();
        const rows = response.headers.get('X-Export-Rows') || '';
        await saveExportBlob(blob, exportFilenameFromResponse(
            response,
            `matching_filenames.${extension}`), destination);
        if (response.headers.get('X-Export-Truncated') === 'true') {
            Toast.warning(tPage('filenameExportTruncated',
                'The filename export reached the 50,000-row limit; narrow the search to export everything.'),
                { detail: `${rows} rows` });
        } else {
            Toast.success(tPage('filenameExportReady', 'Matching filename export ready.'),
                { detail: `${rows} rows` });
        }
    } catch (error) {
        console.error('Matching filename export failed:', error);
        Toast.error(tPage('filenameExportFailed', 'Could not export the matching filenames.') + ` ${error.message}`);
    }
}

// Print results
function printResults() {
    if (searchState.results.length === 0) {
        alert('No results to print');
        return;
    }
    
    const query = searchState.lastDefinition?.query || 'Search Results';
    const printWindow = window.open('', '_blank');
    if (!printWindow) {
        Toast.error(tPage('printWindowBlocked', 'Allow pop-ups to print these results.'));
        return;
    }
    const safeQuery = escapeHtml(query);

    const printContent = `
<!DOCTYPE html>
<html>
<head>
    <title>Search Results - ${safeQuery}</title>
    <style>
        @media print {
            @page { margin: 1cm; }
            body { font-family: Arial, sans-serif; font-size: 10pt; }
            h1 { font-size: 18pt; margin-bottom: 10pt; }
            h2 { font-size: 14pt; margin-top: 15pt; margin-bottom: 8pt; }
            table { width: 100%; border-collapse: collapse; margin-top: 10pt; }
            th, td { border: 1px solid #ddd; padding: 6pt; text-align: left; }
            th { background-color: #f2f2f2; font-weight: bold; }
            tr:nth-child(even) { background-color: #f9f9f9; }
            .header-info { margin-bottom: 15pt; }
            .header-info p { margin: 3pt 0; }
            .no-print { display: none; }
        }
        body { font-family: Arial, sans-serif; font-size: 10pt; padding: 20px; }
        h1 { font-size: 18pt; margin-bottom: 10pt; }
        h2 { font-size: 14pt; margin-top: 15pt; margin-bottom: 8pt; }
        table { width: 100%; border-collapse: collapse; margin-top: 10pt; }
        th, td { border: 1px solid #ddd; padding: 6pt; text-align: left; }
        th { background-color: #f2f2f2; font-weight: bold; }
        tr:nth-child(even) { background-color: #f9f9f9; }
        .header-info { margin-bottom: 15pt; }
        .header-info p { margin: 3pt 0; }
    </style>
</head>
<body>
    <h1>Search Results: ${safeQuery}</h1>
    <div class="header-info">
        <p><strong>Results on this page:</strong> ${searchState.results.length.toLocaleString()}</p>
        <p><strong>Total Results:</strong> ${searchState.totalResults.toLocaleString()}</p>
        <p><strong>Search Time:</strong> ${searchState.searchTime} seconds</p>
        <p><strong>Date:</strong> ${new Date().toLocaleString()}</p>
    </div>
    <table>
        <thead>
            <tr>
                <th>#</th>
                <th>File Name</th>
                <th>Type</th>
                <th>Size</th>
                <th>Date</th>
                <th>Source</th>
                <th>Side</th>
                <th>Relevance</th>
            </tr>
        </thead>
        <tbody>
            ${searchState.results.map((result, index) => `
                <tr>
                    <td>${index + 1}</td>
                    <td>${escapeHtml(result.file_name || 'N/A')}</td>
                    <td>${escapeHtml(result.file_type || 'N/A')}</td>
                    <td>${formatFileSize(result.file_size || 0)}</td>
                    <td>${result.file_date ? new Date(result.file_date).toLocaleDateString() : 'N/A'}</td>
                    <td>${escapeHtml(result.source_name || 'N/A')}</td>
                    <td>${escapeHtml(result.side_name || 'N/A')}</td>
                    <td>${result.relevance_score ? (result.relevance_score * 100).toFixed(1) + '%' : 'N/A'}</td>
                </tr>
            `).join('')}
        </tbody>
    </table>
    <script>
        window.onload = function() {
            window.print();
        };
    </script>
</body>
</html>`;
    
    printWindow.document.write(printContent);
    printWindow.document.close();
}

// Helper function to escape CSV values

// Helper function to escape HTML

// Attribute-context escaper: escapeHtml (innerHTML text serialization)
// leaves quotes intact, which would let a crafted filename break out of
// title="/aria-label=" attributes. This escapes quotes as well.
function escapeAttr(text) {
    return String(text == null ? '' : text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

// Format file size (keep for backward compatibility)
function formatFileSize(bytes) {
    if (!bytes || bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round((bytes / Math.pow(k, i)) * 100) / 100 + ' ' + sizes[i];
}

// Helper function to sanitize filename

// ====================================================================
// In-page file preview (popup) — inspect a result without leaving the
// search page; the full file page stays one click away in a new tab.
// The search page itself is never navigated away from.
// ====================================================================

const previewModal = { el: null, bodyEl: null, titleEl: null, openBtn: null, currentFileId: null };

function ensurePreviewModal() {
    if (previewModal.el) return previewModal;
    const overlay = document.createElement('div');
    overlay.id = 'searchFilePreviewModal';
    overlay.className = 'sfp-overlay';
    overlay.innerHTML = `
        <div class="sfp-dialog" role="dialog" aria-modal="true"
             aria-label="${escapeAttr(tPage('preview', 'File preview'))}">
            <div class="sfp-header">
                <span class="sfp-title" id="sfpTitle"></span>
                <div class="sfp-header-actions">
                    <a class="sfp-open-full" id="sfpOpenFull" href="#" target="_blank" rel="noopener">
                        <i class="bi bi-box-arrow-up-right me-1" aria-hidden="true"></i>
                        <span>${escapeHtml(tPage('openInNewTab', 'Open in new tab'))}</span>
                    </a>
                    <button type="button" class="sfp-close" id="sfpClose"
                            title="${escapeAttr(tPage('closePreview', 'Close preview'))}"
                            aria-label="${escapeAttr(tPage('closePreview', 'Close preview'))}">
                        <i class="bi bi-x-lg" aria-hidden="true"></i>
                    </button>
                </div>
            </div>
            <div class="sfp-body" id="sfpBody"></div>
        </div>`;
    document.body.appendChild(overlay);
    previewModal.el = overlay;
    previewModal.bodyEl = overlay.querySelector('#sfpBody');
    previewModal.titleEl = overlay.querySelector('#sfpTitle');
    previewModal.openBtn = overlay.querySelector('#sfpOpenFull');
    // Backdrop click closes (mousedown so text-selection drags don't)
    overlay.addEventListener('mousedown', (e) => {
        if (e.target === overlay) hideFilePreview();
    });
    overlay.querySelector('#sfpClose').addEventListener('click', hideFilePreview);
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && previewModal.el.style.display !== 'none') {
            hideFilePreview();
        }
    });
    return previewModal;
}

/** Open the popup preview for a search result (no navigation). */
function showFilePreview(fileId) {
    const modal = ensurePreviewModal();
    previewModal.currentFileId = fileId;
    // The full page opens in a new tab and carries the originating search.
    modal.openBtn.href = fileDetailHref(fileId);
    modal.titleEl.textContent = tPage('previewLoading', 'Loading preview…');
    modal.bodyEl.innerHTML = `
        <div class="sfp-loading">
            <div class="loading-spinner" role="status" aria-live="polite"></div>
            <p>${escapeHtml(tPage('previewLoading', 'Loading preview…'))}</p>
        </div>`;
    modal.el.style.display = 'flex';
    document.body.classList.add('sfp-no-scroll');

    fetch(`/api/preview/${fileId}`)
        .then(response => {
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            return response.json();
        })
        .then(data => {
            if (previewModal.currentFileId !== fileId) return; // a newer request won
            renderPreviewPayload(data, fileId);
        })
        .catch(error => {
            if (previewModal.currentFileId !== fileId) return;
            console.error('Preview failed:', error);
            previewModal.bodyEl.innerHTML = `
                <div class="sfp-message">
                    <i class="bi bi-exclamation-triangle" aria-hidden="true"></i>
                    <p>${escapeHtml(tPage('previewFailed', 'Preview failed'))}: ${escapeHtml(error.message)}</p>
                    <p class="sfp-message-hint">${escapeHtml(tPage('openFullPageHint', 'Open the file in a new tab to view the full content.'))}</p>
                </div>`;
        });
}

function hideFilePreview() {
    if (!previewModal.el) return;
    previewModal.el.style.display = 'none';
    previewModal.currentFileId = null;
    previewModal.bodyEl.innerHTML = '';
    document.body.classList.remove('sfp-no-scroll');
}

function renderPreviewPayload(data, fileId) {
    const body = previewModal.bodyEl;
    const name = data.file_name || tPage('untitled', 'Untitled');
    previewModal.titleEl.textContent = name;
    const type = data.preview_type;

    if (type === 'image' && Number.isSafeInteger(Number(fileId)) && Number(fileId) > 0) {
        // Image bytes are fetched through the authenticated, opaque file-ID
        // endpoint; the preview response never contains a server path.
        const note = data.preview_kind === 'pdf_first_page'
            ? `<div class="sfp-note">${escapeHtml(tPage('pdfFirstPagePreview', 'First-page preview'))} · ${Number(data.page_count) || 1} ${escapeHtml(tPage('pages', 'pages'))}</div>`
            : '';
        const imageUrl = `/api/preview/${Number(fileId)}/image`;
        body.innerHTML = `
            ${note}
            <div class="sfp-image-wrap">
                <img class="sfp-image" src="${escapeAttr(imageUrl)}" alt="${escapeAttr(name)}">
            </div>`;
    } else if ((type === 'text' || type === 'document' || type === 'pdf') && data.data) {
        const note = type === 'pdf' && data.page_count
            ? `<div class="sfp-note">${escapeHtml(tPage('pdfFirstPage', 'First page text'))} · ${data.page_count} ${escapeHtml(tPage('pages', 'pages'))}</div>`
            : '';
        // The highlighter escapes source text and emits only its own <mark> tags.
        body.innerHTML = `${note}<pre class="sfp-text">${highlightQueryTerms(String(data.data), searchState.query)}</pre>`;
    } else if (type === 'unsupported') {
        body.innerHTML = `
            <div class="sfp-message">
                <i class="bi bi-file-earmark-lock" aria-hidden="true"></i>
                <p>${escapeHtml(data.message || tPage('previewUnavailable', 'Preview is not available for this file.'))}</p>
                <p class="sfp-message-hint">${escapeHtml(tPage('openFullPageHint', 'Open the file in a new tab to view the full content.'))}</p>
            </div>`;
    } else {
        const detail = data.error || tPage('previewUnavailable', 'Preview is not available for this file.');
        body.innerHTML = `
            <div class="sfp-message">
                <i class="bi bi-exclamation-circle" aria-hidden="true"></i>
                <p>${escapeHtml(detail)}</p>
                <p class="sfp-message-hint">${escapeHtml(tPage('openFullPageHint', 'Open the file in a new tab to view the full content.'))}</p>
            </div>`;
    }
}

// ====================================================================
// Shared search/review workspace. Reviewing a result does not issue another
// search or navigate away: the query, page, filters, sort, selection and result
// list remain in place while contextual text or the original is inspected here.
// ====================================================================

const reviewPaneState = {
    fileId: null,
    generation: 0,
    activeTab: 'text',
    query: '',
    matches: [],
    totalMatches: 0,
    matchIndex: 0,
    chunkText: '',
    chunkOffset: 0,
    totalLength: 0,
    chunkLimit: 12000,
    original: null,
    originalPromise: null,
};

/** Absolute character offset for a /file/<id>/search match (flat list uses ``start``). */
function reviewMatchAbsoluteStart(match) {
    if (!match || typeof match !== 'object') return null;
    const value = match.global_start ?? match.start;
    const offset = Number(value);
    return Number.isFinite(offset) && offset >= 0 ? offset : null;
}

function reviewQueryTerm() {
    // The endpoint parses the full query so multi-term/quoted searches keep
    // their positive match set while exclusions remain unhighlighted.
    return String(searchState.query || '').trim();
}

function safeReviewUrl(value) {
    try {
        const parsed = new URL(String(value || ''), window.location.origin);
        return parsed.origin === window.location.origin
            ? `${parsed.pathname}${parsed.search}${parsed.hash}`
            : null;
    } catch (_error) {
        return null;
    }
}

function reviewDesktopBridge() {
    return window.inforaxisDesktop || window.INFORAXIS_DESKTOP || null;
}

function configureReviewDesktopActions(original) {
    const bridge = reviewDesktopBridge();
    const canOpen = !!original?.available && !!bridge &&
        (typeof bridge.openDocumentForEdit === 'function' || typeof bridge.openDocument === 'function');
    const canBrowse = !!original?.available && !!bridge &&
        typeof bridge.openContainingFolder === 'function';
    const nativeButton = document.getElementById('reviewOpenNative');
    const folderButton = document.getElementById('reviewOpenContainingFolder');
    const note = document.getElementById('reviewDesktopIntegrationNote');
    if (nativeButton) nativeButton.disabled = !canOpen;
    if (folderButton) folderButton.disabled = !canBrowse;
    if (note) note.hidden = canOpen && canBrowse;
}

async function openReviewInNativeApplication() {
    const bridge = reviewDesktopBridge();
    const original = reviewPaneState.original;
    const fileId = reviewPaneState.fileId;
    const openDocument = bridge?.openDocumentForEdit || bridge?.openDocument;
    if (!fileId || !original?.available || typeof openDocument !== 'function') {
        Toast.info(tPage('desktopBridgeMissing',
            'Opening or editing a native file and opening its operating-system folder require the trusted local SYLTHARAE companion. Use the browser preview or download here.'));
        return;
    }
    try {
        await openDocument.call(bridge, {
            fileId,
            fileName: original.name,
            extension: original.extension,
            mimeType: original.mime_type,
            downloadUrl: safeReviewUrl(original.download_url),
            edit: typeof bridge.openDocumentForEdit === 'function',
        });
    } catch (error) {
        console.error('Native document action failed:', error);
        Toast.error(tPage('desktopBridgeFailed', 'The local SYLTHARAE companion could not complete this action.') + ` ${error.message}`);
    }
}

async function openReviewContainingFolder() {
    const bridge = reviewDesktopBridge();
    const original = reviewPaneState.original;
    const fileId = reviewPaneState.fileId;
    if (!fileId || !original?.available || typeof bridge?.openContainingFolder !== 'function') {
        Toast.info(tPage('desktopBridgeMissing',
            'Opening or editing a native file and opening its operating-system folder require the trusted local SYLTHARAE companion. Use the browser preview or download here.'));
        return;
    }
    try {
        await bridge.openContainingFolder({ fileId, fileName: original.name });
    } catch (error) {
        console.error('Open containing folder failed:', error);
        Toast.error(tPage('desktopBridgeFailed', 'The local SYLTHARAE companion could not complete this action.') + ` ${error.message}`);
    }
}

function setReviewTab(tab) {
    reviewPaneState.activeTab = tab;
    const extracted = document.getElementById('reviewExtractedTab');
    const original = document.getElementById('reviewOriginalTab');
    extracted?.classList.toggle('is-active', tab === 'text');
    extracted?.setAttribute('aria-selected', String(tab === 'text'));
    original?.classList.toggle('is-active', tab === 'original');
    original?.setAttribute('aria-selected', String(tab === 'original'));
    updateReviewMatchNavigation();
    updateReviewContentControls();
}

async function reviewResultInPane(fileId, event = null) {
    event?.preventDefault();
    event?.stopPropagation();
    const id = Number(fileId);
    if (!Number.isSafeInteger(id) || id < 1) return;

    const result = searchState.results.find(item => Number(item.id) === id);
    const fileName = result?.file_name || tPage('untitled', 'Untitled');
    const pane = document.getElementById('documentReviewPane');
    const grid = document.getElementById('searchWorkspaceGrid');
    if (!pane || !grid) return;

    reviewPaneState.generation += 1;
    const generation = reviewPaneState.generation;
    reviewPaneState.fileId = id;
    reviewPaneState.activeTab = 'text';
    reviewPaneState.query = reviewQueryTerm();
    reviewPaneState.matches = [];
    reviewPaneState.totalMatches = 0;
    reviewPaneState.matchIndex = 0;
    reviewPaneState.chunkText = '';
    reviewPaneState.chunkOffset = 0;
    reviewPaneState.totalLength = 0;
    reviewPaneState.original = null;
    reviewPaneState.originalPromise = null;
    configureReviewDesktopActions(null);

    pane.hidden = false;
    grid.classList.add('is-review-open');
    document.getElementById('reviewPaneTitle').textContent = fileName;
    document.getElementById('reviewOpenFull').href = fileDetailHref(id);
    const downloadLink = document.getElementById('reviewDownloadOriginal');
    downloadLink.href = `/api/file/${id}/original/content?download=1`;
    downloadLink.classList.add('is-pending');
    downloadLink.setAttribute('aria-disabled', 'true');
    setReviewTab('text');
    document.getElementById('reviewPaneStatus').textContent = '';
    document.getElementById('reviewPaneContent').innerHTML = `
        <div class="review-pane-loading" role="status">
            <span class="loading-spinner" aria-hidden="true"></span>
            <span>${escapeHtml(tPage('reviewLoading', 'Loading document context…'))}</span>
        </div>`;
    document.querySelectorAll('.result-item.review-active').forEach(item => item.classList.remove('review-active'));
    document.querySelector(`.result-item[data-file-id="${id}"]`)?.classList.add('review-active');

    // Read-only descriptor enables/disables the browser download without
    // exposing a server filesystem path to the page.
    reviewPaneState.originalPromise = fetch(`/api/file/${id}/original`)
        .then(async response => {
            const data = await response.json();
            if (!response.ok || !data.success) throw new Error(data.error || `HTTP ${response.status}`);
            return data.original;
        })
        .then(original => {
            if (reviewPaneState.generation !== generation) return null;
            reviewPaneState.original = original;
            configureReviewDesktopActions(original);
            const sourceUrl = original?.available ? safeReviewUrl(original.download_url) : null;
            if (sourceUrl) {
                downloadLink.href = sourceUrl;
                downloadLink.classList.remove('is-pending');
                downloadLink.removeAttribute('aria-disabled');
            } else {
                downloadLink.removeAttribute('href');
                downloadLink.classList.remove('is-pending');
                downloadLink.setAttribute('aria-disabled', 'true');
                downloadLink.title = original?.message || tPage('reviewOriginalUnavailable', 'The original file is not available.');
            }
            return original;
        })
        .catch(error => {
            console.warn('Could not describe original file:', error);
            return null;
        });

    try {
        if (reviewPaneState.query) {
            const params = new URLSearchParams({
                q: reviewPaneState.query,
                case_sensitive: String(!!searchState.options.caseSensitive),
                whole_word: String(!!searchState.options.wholeWord),
            });
            const response = await fetch(`/file/${id}/search?${params.toString()}`);
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
            if (reviewPaneState.generation !== generation) return;
            reviewPaneState.matches = Array.isArray(data.matches) ? data.matches.slice(0, 1000) : [];
            reviewPaneState.totalMatches = Number(data.total_matches) || 0;
        }
        if (reviewPaneState.generation !== generation) return;
        updateReviewMatchNavigation();
        if (reviewPaneState.matches.length) {
            await renderReviewChunk(reviewMatchAbsoluteStart(reviewPaneState.matches[0]), 0, generation);
        } else {
            await renderReviewChunk(null, 0, generation);
        }
    } catch (error) {
        if (reviewPaneState.generation !== generation) return;
        console.error('Review context failed:', error);
        document.getElementById('reviewPaneContent').innerHTML = `
            <div class="review-pane-message">${escapeHtml(tPage('reviewLoadFailed', 'Could not load this document context.'))}</div>`;
    }
}

function updateReviewMatchNavigation() {
    const navigation = document.getElementById('reviewMatchNavigation');
    const counter = document.getElementById('reviewMatchCount');
    if (!navigation || !counter) return;
    navigation.hidden = !reviewPaneState.query || reviewPaneState.activeTab !== 'text';
    if (navigation.hidden) return;
    if (!reviewPaneState.totalMatches) {
        counter.textContent = tPage('reviewNoMatches', 'No query matches in extracted text.');
        return;
    }
    counter.textContent = tPage('reviewMatches', 'Match {current} of {total}')
        .replace('{current}', String(reviewPaneState.matchIndex + 1))
        .replace('{total}', String(reviewPaneState.totalMatches));
    if (reviewPaneState.totalMatches > reviewPaneState.matches.length) {
        counter.title = tPage('reviewMoreMatches', 'Showing the first {count} matches.')
            .replace('{count}', String(reviewPaneState.matches.length));
    } else {
        counter.removeAttribute('title');
    }
}

async function renderReviewChunk(targetAbsoluteOffset = null, matchIndex = 0, generation = reviewPaneState.generation) {
    const fileId = reviewPaneState.fileId;
    if (!fileId || generation !== reviewPaneState.generation) return;
    const target = targetAbsoluteOffset === null || targetAbsoluteOffset === undefined
        ? null
        : Number(targetAbsoluteOffset);
    const safeTarget = Number.isFinite(target) && target >= 0 ? target : null;
    const chunkLimit = reviewPaneState.chunkLimit || 12000;
    const chunkOffset = safeTarget === null
        ? 0
        : Math.max(0, safeTarget - Math.floor(chunkLimit / 4));
    const params = new URLSearchParams({ offset: String(chunkOffset), limit: String(chunkLimit) });
    const response = await fetch(`/file/${fileId}/content?${params.toString()}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    if (generation !== reviewPaneState.generation || reviewPaneState.activeTab !== 'text') return;

    reviewPaneState.chunkText = String(data.content || '');
    reviewPaneState.chunkOffset = Number(data.offset) || 0;
    reviewPaneState.totalLength = Number(data.total_length) || reviewPaneState.chunkText.length;
    reviewPaneState.matchIndex = matchIndex;
    const content = document.getElementById('reviewPaneContent');
    if (!reviewPaneState.chunkText) {
        content.innerHTML = `<p class="review-pane-empty">${escapeHtml(tPage('reviewNoText', 'No extracted text is available for this document.'))}</p>`;
        document.getElementById('reviewPaneStatus').textContent = '';
        updateReviewContentControls();
        return;
    }

    content.innerHTML = buildReviewTextMarkup(reviewPaneState.chunkText, safeTarget);
    updateReviewStatusRange();
    updateReviewMatchNavigation();
    updateReviewContentControls();
    if (safeTarget !== null) {
        requestAnimationFrame(() => content.querySelector('.review-current-match')?.scrollIntoView({ block: 'center' }));
    }
}

function buildReviewTextMarkup(chunkText, targetAbsoluteOffset = null) {
    return `<pre class="review-pane-text">${highlightQueryTerms(
        chunkText, searchState.query, targetAbsoluteOffset,
        reviewPaneState.chunkOffset, !!searchState.options.caseSensitive,
        !!searchState.options.wholeWord)}</pre>`;
}

function updateReviewStatusRange() {
    const end = reviewPaneState.chunkOffset + reviewPaneState.chunkText.length;
    const total = reviewPaneState.totalLength || end;
    const status = document.getElementById('reviewPaneStatus');
    if (status) {
        status.textContent = `${reviewPaneState.chunkOffset + 1}–${end} / ${total}`;
    }
}

function updateReviewContentControls() {
    const wrap = document.getElementById('reviewContentControls');
    if (!wrap) return;
    const total = reviewPaneState.totalLength || 0;
    const start = reviewPaneState.chunkOffset || 0;
    const end = start + (reviewPaneState.chunkText?.length || 0);
    const hasEarlier = start > 0;
    const hasMore = total > 0 && end < total;
    wrap.hidden = reviewPaneState.activeTab !== 'text' || (!hasEarlier && !hasMore && !reviewPaneState.chunkText);
    const earlierBtn = document.getElementById('reviewShowEarlier');
    const moreBtn = document.getElementById('reviewShowMore');
    if (earlierBtn) {
        earlierBtn.disabled = !hasEarlier;
        earlierBtn.hidden = false;
    }
    if (moreBtn) {
        moreBtn.disabled = !hasMore;
        moreBtn.hidden = false;
    }
}

/**
 * Extend the extracted-text window earlier (prepend) or later (append).
 * @param {number} direction -1 = earlier, 1 = more
 */
async function loadMoreReviewContent(direction) {
    const fileId = reviewPaneState.fileId;
    if (!fileId || reviewPaneState.activeTab !== 'text') return;
    const generation = reviewPaneState.generation;
    const chunkLimit = reviewPaneState.chunkLimit || 12000;
    const total = reviewPaneState.totalLength || 0;
    const start = reviewPaneState.chunkOffset || 0;
    const end = start + (reviewPaneState.chunkText?.length || 0);
    const step = Number(direction) < 0 ? -1 : 1;

    let fetchOffset;
    let fetchLimit;
    if (step < 0) {
        if (start <= 0) return;
        fetchOffset = Math.max(0, start - chunkLimit);
        fetchLimit = start - fetchOffset;
    } else {
        if (total > 0 && end >= total) return;
        fetchOffset = end;
        fetchLimit = chunkLimit;
    }
    if (fetchLimit <= 0) return;

    const earlierBtn = document.getElementById('reviewShowEarlier');
    const moreBtn = document.getElementById('reviewShowMore');
    if (earlierBtn) earlierBtn.disabled = true;
    if (moreBtn) moreBtn.disabled = true;

    try {
        const params = new URLSearchParams({
            offset: String(fetchOffset),
            limit: String(fetchLimit),
        });
        const response = await fetch(`/file/${fileId}/content?${params.toString()}`);
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
        if (generation !== reviewPaneState.generation || reviewPaneState.activeTab !== 'text') return;

        const piece = String(data.content || '');
        if (!piece) {
            updateReviewContentControls();
            return;
        }

        const contentEl = document.getElementById('reviewPaneContent');
        const scrollTop = contentEl?.scrollTop || 0;
        const prevHeight = contentEl?.scrollHeight || 0;

        if (step < 0) {
            reviewPaneState.chunkOffset = Number(data.offset) || fetchOffset;
            reviewPaneState.chunkText = piece + reviewPaneState.chunkText;
        } else {
            reviewPaneState.chunkText += piece;
        }
        if (data.total_length != null) {
            reviewPaneState.totalLength = Number(data.total_length) || reviewPaneState.totalLength;
        }

        const currentMatch = reviewMatchAbsoluteStart(reviewPaneState.matches[reviewPaneState.matchIndex]);
        contentEl.innerHTML = buildReviewTextMarkup(reviewPaneState.chunkText, currentMatch);
        updateReviewStatusRange();
        updateReviewMatchNavigation();
        updateReviewContentControls();

        if (step < 0 && contentEl) {
            requestAnimationFrame(() => {
                contentEl.scrollTop = scrollTop + (contentEl.scrollHeight - prevHeight);
            });
        }
    } catch (error) {
        console.error('Could not load more review content:', error);
        Toast.error(tPage('reviewLoadFailed', 'Could not load this document context.'));
        updateReviewContentControls();
    }
}

function navigateReviewMatch(direction) {
    if (!reviewPaneState.matches.length) return;
    const total = reviewPaneState.matches.length;
    const nextIndex = (reviewPaneState.matchIndex + Number(direction) + total) % total;
    reviewPaneState.matchIndex = nextIndex;
    updateReviewMatchNavigation();
    const absoluteStart = reviewMatchAbsoluteStart(reviewPaneState.matches[nextIndex]);
    if (absoluteStart === null) {
        console.warn('Match is missing an absolute start offset:', reviewPaneState.matches[nextIndex]);
        return;
    }
    // If the match is already inside the loaded window, scroll to it without refetching.
    const windowStart = reviewPaneState.chunkOffset;
    const windowEnd = windowStart + reviewPaneState.chunkText.length;
    if (absoluteStart >= windowStart && absoluteStart < windowEnd) {
        const content = document.getElementById('reviewPaneContent');
        if (content) {
            content.innerHTML = buildReviewTextMarkup(reviewPaneState.chunkText, absoluteStart);
            requestAnimationFrame(() => content.querySelector('.review-current-match')?.scrollIntoView({ block: 'center' }));
        }
        return;
    }
    renderReviewChunk(absoluteStart, nextIndex).catch(error => {
        console.error('Could not navigate to the next match:', error);
    });
}

function showReviewExtracted() {
    if (!reviewPaneState.fileId) return;
    setReviewTab('text');
    const match = reviewPaneState.matches[reviewPaneState.matchIndex];
    renderReviewChunk(reviewMatchAbsoluteStart(match), reviewPaneState.matchIndex)
        .catch(error => console.error('Could not render extracted text:', error));
}

async function showReviewOriginal() {
    if (!reviewPaneState.fileId) return;
    const generation = reviewPaneState.generation;
    setReviewTab('original');
    const content = document.getElementById('reviewPaneContent');
    content.innerHTML = `<div class="review-pane-loading" role="status">${escapeHtml(tPage('reviewLoading', 'Loading document context…'))}</div>`;
    try {
        const original = reviewPaneState.originalPromise
            ? await reviewPaneState.originalPromise
            : reviewPaneState.original;
        if (generation !== reviewPaneState.generation || reviewPaneState.activeTab !== 'original') return;
        if (!original || !original.available) {
            content.innerHTML = `<div class="review-pane-message">${escapeHtml(original?.message || tPage('reviewOriginalUnavailable', 'The original file is not available.'))}</div>`;
            return;
        }
        const url = safeReviewUrl(original.serve_url);
        if (!url) throw new Error('The original preview URL is not same-origin.');
        const title = escapeAttr(original.name || tPage('untitled', 'Untitled'));
        const kind = String(original.kind || 'download');
        if (kind === 'pdf') {
            content.innerHTML = `<iframe class="review-pane-frame" src="${escapeAttr(`${url}#page=1`)}" title="${title} — page 1"></iframe>`;
        } else if (kind === 'image') {
            content.innerHTML = `<div class="review-pane-image-wrap"><img class="review-pane-image" src="${escapeAttr(url)}" alt="${title}"></div>`;
        } else if (kind === 'text') {
            content.innerHTML = `<iframe class="review-pane-frame" src="${escapeAttr(url)}" title="${title}"></iframe>`;
        } else if (kind === 'audio') {
            content.innerHTML = `<audio class="review-pane-media" controls src="${escapeAttr(url)}">${escapeHtml(tPage('reviewMediaUnsupported', 'Your browser cannot play this media.'))}</audio>`;
        } else if (kind === 'video') {
            content.innerHTML = `<video class="review-pane-media" controls src="${escapeAttr(url)}">${escapeHtml(tPage('reviewMediaUnsupported', 'Your browser cannot play this media.'))}</video>`;
        } else {
            const downloadUrl = safeReviewUrl(original.download_url) || '#';
            content.innerHTML = `<div class="review-pane-message"><p>${escapeHtml(tPage('reviewNoInlinePreview', 'This format cannot be displayed inline.'))}</p><a class="btn btn-sm btn-primary" href="${escapeAttr(downloadUrl)}" download>${escapeHtml(tPage('downloadOriginal', 'Download original'))}</a></div>`;
        }
    } catch (error) {
        if (generation !== reviewPaneState.generation) return;
        console.warn('Original preview failed:', error);
        content.innerHTML = `<div class="review-pane-message">${escapeHtml(tPage('reviewOriginalFailed', 'Could not load the original preview.'))}</div>`;
    }
}

async function downloadReviewOriginal(event = null) {
    event?.preventDefault();
    const original = reviewPaneState.original;
    if (!original?.available) {
        Toast.info(tPage('reviewOriginalUnavailable', 'The original file is not available.'));
        return;
    }
    const requestedName = await window.prompt(
        tPage('reviewOriginalFilenamePrompt', 'Choose a filename for this original document:'),
        original.name || `file_${reviewPaneState.fileId}`);
    if (requestedName === null) return;

    let filename = safeClientFilename(requestedName, original.name || `file_${reviewPaneState.fileId}`);
    const extension = String(original.extension || '').toLowerCase();
    if (extension && !filename.toLowerCase().endsWith(extension)) filename += extension;
    const destination = await chooseDestinationForExport(filename);
    if (destination === false) return;
    const downloadUrl = safeReviewUrl(original.download_url);
    if (!downloadUrl) {
        Toast.error(tPage('reviewOriginalDownloadFailed', 'Could not download the original document.'));
        return;
    }

    try {
        const response = await fetch(downloadUrl, { credentials: 'same-origin' });
        if (!response.ok) {
            const problem = await response.json().catch(() => ({}));
            throw new Error(problem.error || `HTTP ${response.status}`);
        }
        await saveExportBlob(await response.blob(), filename, destination);
        Toast.success(tPage('reviewOriginalDownloadReady', 'Original download ready.'));
    } catch (error) {
        console.error('Original file download failed:', error);
        Toast.error(tPage('reviewOriginalDownloadFailed', 'Could not download the original document.') + ` ${error.message}`);
    }
}

async function copyReviewSelection() {
    const selection = window.getSelection();
    const pane = document.getElementById('reviewPaneContent');
    const anchor = selection?.anchorNode;
    if (!selection || selection.isCollapsed || !anchor || !pane?.contains(anchor)) {
        Toast.info(tPage('reviewCopySelect', 'Select a passage in the document first.'));
        return;
    }
    try {
        await navigator.clipboard.writeText(selection.toString());
        Toast.success(tPage('reviewCopied', 'Passage copied.'));
    } catch (error) {
        console.warn('Clipboard access failed:', error);
        Toast.error(tPage('copyFailed', 'Could not copy the selected passage.'));
    }
}

function closeReviewPane() {
    const pane = document.getElementById('documentReviewPane');
    const grid = document.getElementById('searchWorkspaceGrid');
    if (pane) pane.hidden = true;
    grid?.classList.remove('is-review-open');
    document.querySelectorAll('.result-item.review-active').forEach(item => item.classList.remove('review-active'));
    reviewPaneState.generation += 1;
    reviewPaneState.fileId = null;
    reviewPaneState.originalPromise = null;
}

function openSearchInNewWindow() {
    const definition = searchState.lastDefinition || currentSearchDefinition();
    const params = serializeDefinitionToParams(definition);
    const target = `${window.location.pathname}${params.toString() ? `?${params.toString()}` : ''}`;
    const opened = window.open(target, '_blank', 'popup,width=1440,height=900,resizable=yes,scrollbars=yes');
    if (!opened) Toast.error(tPage('openWindowBlocked', 'Allow pop-ups to open this search in a new window.'));
}

function updateSimilarityButton() {
    const button = document.getElementById('groupSimilarBtn');
    if (!button) return;
    const active = Array.isArray(searchState.similarityGroups);
    const label = active ? tPage('clearGrouping', 'Clear groups') : tPage('groupSimilar', 'Group similar');
    button.innerHTML = `<i class="bi ${active ? 'bi-x-circle' : 'bi-diagram-3'} me-1" aria-hidden="true"></i>${escapeHtml(label)}`;
    button.setAttribute('aria-pressed', String(active));
}

async function toggleSimilarityGrouping() {
    if (Array.isArray(searchState.similarityGroups)) {
        searchState.similarityGroups = null;
        updateSimilarityButton();
        displayResults(searchState.results, searchState.pagination);
        return;
    }
    const fileIds = searchState.results.map(result => Number(result.id))
        .filter(id => Number.isSafeInteger(id) && id > 0);
    if (fileIds.length < 2) {
        Toast.info(tPage('groupNeedsResults', 'At least two results are needed to group similar files.'));
        return;
    }
    const button = document.getElementById('groupSimilarBtn');
    if (button) button.disabled = true;
    try {
        const response = await fetch('/api/search/group-similar', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': analystCsrftoken(),
            },
            body: JSON.stringify({
                file_ids: fileIds,
                threshold: Number(document.getElementById('similarityThreshold')?.value) || 0.32,
            }),
        });
        const data = await response.json();
        if (!response.ok || !data.success) throw new Error(data.error || `HTTP ${response.status}`);
        searchState.similarityGroups = Array.isArray(data.groups) ? data.groups : [];
        updateSimilarityButton();
        displayResults(searchState.results, searchState.pagination);
        Toast.info(tPage('groupingPageScope', 'Similarity groups apply to this results page only.'));
    } catch (error) {
        console.error('Similarity grouping failed:', error);
        Toast.error(tPage('groupingFailed', 'Could not group similar files.') + ` ${error.message}`);
    } finally {
        if (button) button.disabled = false;
    }
}

function selectedIdsForExport() {
    const fileIds = Array.from(searchState.selectedIds);
    if (!fileIds.length) {
        Toast.info(tPage('selectFilesFirst', 'Select one or more documents first.'));
        return null;
    }
    if (fileIds.length > 200) {
        Toast.error(tPage('selectedExportLimit', 'Select no more than 200 documents for one export.'));
        return null;
    }
    return fileIds;
}

async function selectedExportName() {
    // window.prompt is the Promise-based modal from alert-replacement.js.
    return await window.prompt(
        tPage('selectedExportPrompt', 'Name this selected-file export (leave blank for an automatic name):'),
        `selected_documents_${new Date().toISOString().slice(0, 10)}`);
}

function exportFilenameFromResponse(response, fallback) {
    const disposition = response.headers.get('Content-Disposition') || '';
    const match = disposition.match(/filename\*?=(?:UTF-8''|\")?([^\";]+)/i);
    if (!match) return fallback;
    try { return decodeURIComponent(match[1].replace(/\"/g, '')); }
    catch (_error) { return match[1].replace(/\"/g, ''); }
}

async function downloadSelectedExport(endpoint, payload, fallbackName, successMessage) {
    const extension = String(fallbackName).split('.').pop().toLowerCase();
    const suggestedName = ensureExportExtension(
        payload.filename || String(fallbackName).replace(/\.[^.]+$/, ''),
        extension,
        String(fallbackName).replace(/\.[^.]+$/, 'export'));
    const destination = await chooseDestinationForExport(suggestedName);
    if (destination === false) return;
    try {
        const response = await fetch(endpoint, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': analystCsrftoken(),
            },
            body: JSON.stringify(payload),
        });
        if (!response.ok) {
            const problem = await response.json().catch(() => ({}));
            throw new Error(problem.error || `HTTP ${response.status}`);
        }
        const blob = await response.blob();
        await saveExportBlob(blob, exportFilenameFromResponse(response, fallbackName), destination);
        const unavailableCount = Number(response.headers.get('X-Export-Unavailable')) || 0;
        if (unavailableCount > 0) {
            Toast.warning(tPage('exportUnavailableSections',
                'The export was saved, but some selected documents had no readable extracted text.'),
                { detail: `${unavailableCount}` });
        } else if (response.headers.get('X-Export-Empty') === 'true') {
            Toast.info(tPage('contactExportEmpty',
                'No email addresses or links were found; a headers-only report was saved.'));
        } else {
            Toast.success(successMessage);
        }
    } catch (error) {
        console.error('Selected-file export failed:', error);
        Toast.error(tPage('selectedExportFailed', 'Selected-file export failed.') + ` ${error.message}`);
    }
}

async function exportSingleFileText(fileId, event = null) {
    event?.preventDefault();
    event?.stopPropagation();
    const id = Number(fileId);
    if (!Number.isSafeInteger(id) || id < 1) return;
    const result = searchState.results.find(item => Number(item.id) === id);
    const suggested = `${String(result?.file_name || `file_${id}`).replace(/\.[^.]+$/, '')}_extracted_text`;
    const filename = await window.prompt(
        tPage('exportFilenamePromptShort', 'Name this download:'), suggested);
    if (filename === null) return;
    const requestedName = ensureExportExtension(filename.trim() || suggested, 'txt', suggested);
    const destination = await chooseDestinationForExport(requestedName);
    if (destination === false) return;
    try {
        const response = await fetch(`/api/files/${id}/export?filename=${encodeURIComponent(filename.trim())}`);
        if (!response.ok) {
            const problem = await response.json().catch(() => ({}));
            throw new Error(problem.error || `HTTP ${response.status}`);
        }
        const blob = await response.blob();
        await saveExportBlob(blob, exportFilenameFromResponse(response, `${suggested}.txt`), destination);
        Toast.success(tPage('singleTextExportReady', 'Extracted text download ready.'));
    } catch (error) {
        console.error('Extracted-text download failed:', error);
        Toast.error(tPage('selectedExportFailed', 'Selected-file export failed.') + ` ${error.message}`);
    }
}

async function exportSelectedFiles(mode = 'text') {
    const fileIds = selectedIdsForExport();
    if (!fileIds) return;
    const filename = await selectedExportName();
    if (filename === null) return;
    downloadSelectedExport('/files/export', {
        file_ids: fileIds,
        mode: mode === 'originals' ? 'originals' : 'text',
        filename: filename.trim(),
    }, `selected_${mode}.zip`, tPage('selectedExportReady', 'Selected-file export ready.'));
}

async function exportSelectedNames(format = 'csv') {
    const fileIds = selectedIdsForExport();
    if (!fileIds) return;
    const filename = await selectedExportName();
    if (filename === null) return;
    const outputFormat = format === 'excel' ? 'excel' : 'csv';
    const extension = outputFormat === 'excel' ? 'xlsx' : 'csv';
    downloadSelectedExport('/api/files/names/export', {
        scope: 'selected',
        file_ids: fileIds,
        format: outputFormat,
        filename: filename.trim(),
    }, `selected_filenames.${extension}`,
    tPage('selectedNamesReady', 'Selected filename export ready.'));
}

function safeClientFilename(value, fallback) {
    let name = String(value || '').split(/[\/\\]/).pop()
        .replace(/[\u0000-\u001f<>:"|?*\\]/g, '_')
        .replace(/^\.+$/, '')
        .trim();
    if (!name || name === '.' || name === '..') name = fallback;
    return name.slice(0, 160) || fallback;
}

async function exportSelectedToFolder(mode = 'text') {
    const fileIds = selectedIdsForExport();
    if (!fileIds) return;
    if (typeof window.showDirectoryPicker !== 'function') {
        Toast.info(tPage('folderPickerUnsupported', 'Folder export is not supported in this browser; downloading a ZIP instead.'));
        exportSelectedFiles(mode === 'originals' ? 'originals' : 'text');
        return;
    }

    let parent;
    try {
        // Invoke the picker before any await so the browser recognizes this as
        // a direct user gesture. The app never receives a local filesystem path.
        parent = await window.showDirectoryPicker({ mode: 'readwrite' });
    } catch (error) {
        if (error?.name !== 'AbortError') {
            console.warn('Folder picker failed:', error);
            Toast.error(tPage('folderPickerFailed', 'Could not write to the selected folder.'));
        }
        return;
    }

    const folderName = await window.prompt(
        tPage('folderExportSubfolderPrompt', 'Optional: enter a new subfolder name, or leave blank to use the selected folder.'),
        '');
    if (folderName === null) return;

    let targetDirectory = parent;
    const cleanedFolderName = String(folderName).trim()
        .replace(/[\u0000-\u001f<>:"\/\\|?*]/g, '_')
        .replace(/^\.+$/, '').slice(0, 80);
    if (cleanedFolderName) {
        try {
            targetDirectory = await parent.getDirectoryHandle(cleanedFolderName, { create: true });
        } catch (error) {
            console.error('Could not create selected subfolder:', error);
            Toast.error(tPage('folderPickerFailed', 'Could not write to the selected folder.') + ` ${error.message}`);
            return;
        }
    }

    const button = document.activeElement;
    if (button instanceof HTMLButtonElement) button.disabled = true;
    Toast.info(tPage('folderExportProgress', 'Writing selected documents to the chosen folder…'));
    const usedNames = new Set();
    const failures = [];
    let written = 0;

    for (const fileId of fileIds) {
        try {
            const descriptorResponse = await fetch(`/api/file/${fileId}/original`);
            const descriptorData = await descriptorResponse.json();
            if (!descriptorResponse.ok || !descriptorData.success) {
                throw new Error(descriptorData.error || `HTTP ${descriptorResponse.status}`);
            }
            const original = descriptorData.original || {};
            let url;
            let filename;
            if (mode === 'originals') {
                if (!original.available) throw new Error(original.message || 'Original file unavailable');
                url = safeReviewUrl(original.download_url);
                filename = safeClientFilename(original.name, `file_${fileId}`);
            } else {
                url = `/api/files/${fileId}/export`;
                const sourceName = safeClientFilename(original.name, `file_${fileId}`);
                const stem = sourceName.replace(/\.[^.]+$/, '') || `file_${fileId}`;
                filename = `${stem}_extracted.txt`;
            }
            if (!url) throw new Error('The download URL was not valid for this application.');

            const fileResponse = await fetch(url, { credentials: 'same-origin' });
            if (!fileResponse.ok) {
                const problem = await fileResponse.json().catch(() => ({}));
                throw new Error(problem.error || `HTTP ${fileResponse.status}`);
            }
            const dot = filename.lastIndexOf('.');
            const stem = dot > 0 ? filename.slice(0, dot) : filename;
            const extension = dot > 0 ? filename.slice(dot) : '';
            let uniqueName = filename;
            let suffix = 2;
            while (usedNames.has(uniqueName.toLocaleLowerCase())) {
                uniqueName = `${stem}_${suffix++}${extension}`;
            }
            usedNames.add(uniqueName.toLocaleLowerCase());

            const handle = await targetDirectory.getFileHandle(uniqueName, { create: true });
            const writable = await handle.createWritable();
            await writable.write(await fileResponse.blob());
            await writable.close();
            written += 1;
        } catch (error) {
            console.warn(`Could not export selected file ${fileId}:`, error);
            failures.push({ fileId, message: error.message });
        }
    }

    if (button instanceof HTMLButtonElement) button.disabled = false;
    if (failures.length) {
        const detail = `${written}/${fileIds.length}`;
        Toast.warning(tPage('folderExportPartial', 'Some documents could not be saved to the chosen folder.'), { detail });
    } else {
        Toast.success(tPage('folderExportDone', 'Documents saved to the chosen folder.'), { detail: String(written) });
    }
}

async function exportSelectedFirstPages(format = 'txt') {
    const fileIds = selectedIdsForExport();
    if (!fileIds) return;
    const filename = await selectedExportName();
    if (filename === null) return;
    const outputFormat = format === 'docx' ? 'docx' : 'txt';
    downloadSelectedExport('/api/files/first-pages/export', {
        file_ids: fileIds,
        format: outputFormat,
        filename: filename.trim(),
    }, `first_pages.${outputFormat}`,
    tPage('firstPagesReady', 'First-page text export ready.'));
}

async function exportSelectedContacts(format = 'csv') {
    const fileIds = selectedIdsForExport();
    if (!fileIds) return;
    const filename = await selectedExportName();
    if (filename === null) return;
    const outputFormat = format === 'xlsx' ? 'xlsx' : 'csv';
    downloadSelectedExport('/api/files/extract-contacts/export', {
        file_ids: fileIds,
        format: outputFormat,
        filename: filename.trim(),
    }, `emails_and_links.${outputFormat}`,
    tPage('contactsReady', 'Email and hyperlink export ready.'));
}

// ====================================================================
// Saved searches — store the full search definition (query, filters,
// scope, options, sort) server-side and re-run it any time without
// searching again.
// ====================================================================

let savedSearchesCache = null;

/** Save the current search (query + all filters + scope + options). */
async function saveCurrentSearch() {
    const def = currentSearchDefinition();
    if (!def.query && getActiveFiltersCount() === 0) {
        Toast.info(tPage('nothingToSave', 'Run a search first, then save it here.'));
        return;
    }

    const stamp = new Date().toISOString().split('T')[0];
    const suggested = def.query || `${tPage('savedSearch', 'Saved search')} ${stamp}`;
    const name = await prompt(tPage('saveSearchPrompt', 'Name this search:'), suggested);
    if (name === null) return; // cancelled
    const trimmed = (name.trim() || suggested);

    try {
        const response = await fetch('/api/search/saved', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': analystCsrftoken()
            },
            body: JSON.stringify({
                name: trimmed,
                query: def.query,
                filters: {
                    ...def.filters,
                    scope: def.scope,
                    sort_by: def.sort_by,
                    similarity_threshold: def.similarity_threshold,
                    options: def.options
                }
            })
        });
        const data = await response.json();
        if (!response.ok || !data.success) {
            throw new Error(data.error || `HTTP ${response.status}`);
        }
        savedSearchesCache = null; // re-fetch next time the menu opens
        Toast.success(tPage('searchSaved', 'Search saved. Find it under Saved searches.'));
    } catch (error) {
        console.error('Saving the search failed:', error);
        Toast.error(tPage('saveFailed', 'Could not save this search.') + ' ' + error.message);
    }
}

/** Open/close the saved-searches dropdown (fetches lazily). */
async function toggleSavedSearchMenu() {
    const menu = document.getElementById('savedSearchMenu');
    if (!menu) return;
    if (menu.style.display === 'block') {
        menu.style.display = 'none';
        return;
    }
    menu.style.display = 'block';
    await renderSavedSearchMenu();
}

// Close the dropdown when clicking anywhere outside it
document.addEventListener('click', (e) => {
    const menu = document.getElementById('savedSearchMenu');
    if (menu && menu.style.display === 'block' &&
        !menu.contains(e.target) && !e.target.closest('.saved-search-container')) {
        menu.style.display = 'none';
    }
});

async function renderSavedSearchMenu() {
    const list = document.getElementById('savedSearchMenuList');
    if (!list) return;
    list.innerHTML = `<div class="ssm-status">${escapeHtml(tPage('loadingSaved', 'Loading saved searches…'))}</div>`;
    try {
        if (!savedSearchesCache) {
            const response = await fetch('/api/search/saved');
            const data = await response.json();
            savedSearchesCache = data.searches || [];
        }
        const searches = savedSearchesCache;
        if (searches.length === 0) {
            list.innerHTML = `<div class="ssm-status">${escapeHtml(tPage('noSavedSearches', 'No saved searches yet. Run a search and press Save.'))}</div>`;
            return;
        }
        list.innerHTML = searches.map(s => `
            <div class="ssm-item">
                <button type="button" class="ssm-run" data-on-click="applySavedSearchById(${s.id})"
                        title="${escapeAttr(tPage('runSavedSearch', 'Run this search'))}">
                    <span class="ssm-name">${escapeHtml(s.name)}</span>
                    ${s.query ? `<span class="ssm-query">${escapeHtml(s.query)}</span>` : ''}
                </button>
                <button type="button" class="ssm-delete" data-on-click="deleteSavedSearchById(${s.id}, event)"
                        title="${escapeAttr(tPage('deleteSearch', 'Delete'))}"
                        aria-label="${escapeAttr(tPage('deleteSearch', 'Delete'))}">
                    <i class="bi bi-trash" aria-hidden="true"></i>
                </button>
            </div>`).join('');
    } catch (error) {
        console.error('Loading saved searches failed:', error);
        list.innerHTML = `<div class="ssm-status">${escapeHtml(tPage('loadSavedFailed', 'Could not load saved searches.'))}</div>`;
    }
}

/** Re-run a saved search: restore every control, then search. */
async function applySavedSearchById(id) {
    try {
        // Fetching the single search also marks it used (server-side).
        const response = await fetch(`/api/search/saved/${id}`);
        const data = await response.json();
        if (!response.ok || !data.search) {
            throw new Error(data.error || `HTTP ${response.status}`);
        }
        applySavedSearch(data.search);
    } catch (error) {
        console.error('Applying the saved search failed:', error);
        Toast.error(tPage('applyFailed', 'Could not run this saved search.'));
    }
}

function applySavedSearch(search) {
    const f = search.filters || {};
    const def = {
        query: search.query || '',
        scope: f.scope || 'uncategorized',
        sort_by: f.sort_by || 'relevance',
        page: 1,
        similarity_threshold: Number(f.similarity_threshold) || 0.32,
        options: f.options || { case_sensitive: false, whole_word: false, use_fuzzy: true },
        filters: {
            file_type: f.file_type || [],
            category_id: f.category_id || [],
            analyst_category_id: f.analyst_category_id || [],
            source_id: f.source_id || [],
            side_id: f.side_id || [],
            date_from: f.date_from || null,
            date_to: f.date_to || null,
            status: Array.isArray(f.status) ? f.status : undefined,
            hide_duplicates: !!f.hide_duplicates
        }
    };
    applyDefinitionToControls(def);
    searchState.currentPage = 1;
    // A different search invalidates the previous result selection (FR-1.2)
    searchState.selectedIds = new Set();
    updateSelectionBar();
    updateFilterChips();

    const menu = document.getElementById('savedSearchMenu');
    if (menu) menu.style.display = 'none';

    executeAdvancedSearch();
    Toast.success(
        tPage('searchApplied', 'Saved search applied')
            .replace('{name}', search.name || '')
    );
}

/** Delete a saved search from the dropdown. */
async function deleteSavedSearchById(id, evt) {
    if (evt) {
        evt.preventDefault();
        evt.stopPropagation();
    }
    if (!confirm(tPage('deleteSearchConfirm', 'Are you sure you want to delete this saved search?'))) {
        return;
    }
    try {
        const response = await fetch(`/api/search/saved/${id}`, {
            method: 'DELETE',
            headers: { 'X-CSRFToken': analystCsrftoken() }
        });
        const data = await response.json();
        if (!response.ok || !data.success) {
            throw new Error(data.error || `HTTP ${response.status}`);
        }
        savedSearchesCache = null;
        await renderSavedSearchMenu();
        Toast.success(tPage('searchDeleted', 'Saved search deleted'));
    } catch (error) {
        console.error('Deleting the saved search failed:', error);
        Toast.error(tPage('deleteFailed', 'Could not delete this saved search.'));
    }
}

// Open a search result in a NEW tab: the search page keeps its results,
// query and filters untouched (the core "search is lost" fix).
function openResultInNewTab(event, fileId) {
    if (event) {
        event.preventDefault();
        event.stopPropagation();
    }
    window.open(fileDetailHref(fileId), '_blank', 'noopener');
}

// Expose functions globally
if (typeof window !== 'undefined') {
    window.executeAdvancedSearch = executeAdvancedSearch;
    window.resetAllFilters = resetAllFilters;
    window.clearAllFilters = clearAllFilters;
    window.toggleFiltersPanel = toggleFiltersPanel;
    window.feelingLucky = feelingLucky;
    window.sortResults = sortResults;
    window.changeResultsPerPage = changeResultsPerPage;
    window.exportResults = exportResults;
    window.exportMatchingFilenames = exportMatchingFilenames;
    window.setSearchExportColumns = setSearchExportColumns;
    window.printResults = printResults;
    // Analyst manual-categorization actions (FR-1.2 / FR-1.3 / NFR-3)
    window.toggleResultSelection = toggleResultSelection;
    window.toggleSelectAllResults = toggleSelectAllResults;
    window.clearResultSelection = clearResultSelection;
    window.assignAnalystCategory = assignAnalystCategory;
    window.removeAnalystCategoriesFromSelection = removeAnalystCategoriesFromSelection;
    // Result opening (new tab / popup preview) and saved searches
    window.openResultInNewTab = openResultInNewTab;
    window.showFilePreview = showFilePreview;
    window.hideFilePreview = hideFilePreview;
    window.reviewResultInPane = reviewResultInPane;
    window.closeReviewPane = closeReviewPane;
    window.downloadReviewOriginal = downloadReviewOriginal;
    window.openReviewInNativeApplication = openReviewInNativeApplication;
    window.openReviewContainingFolder = openReviewContainingFolder;
    window.showReviewExtracted = showReviewExtracted;
    window.showReviewOriginal = showReviewOriginal;
    window.navigateReviewMatch = navigateReviewMatch;
    window.loadMoreReviewContent = loadMoreReviewContent;
    window.copyReviewSelection = copyReviewSelection;
    window.openSearchInNewWindow = openSearchInNewWindow;
    window.toggleDuplicateFilter = toggleDuplicateFilter;
    window.toggleSimilarityGrouping = toggleSimilarityGrouping;
    window.exportSelectedFiles = exportSelectedFiles;
    window.exportSelectedNames = exportSelectedNames;
    window.exportSingleFileText = exportSingleFileText;
    window.exportSelectedToFolder = exportSelectedToFolder;
    window.exportSelectedFirstPages = exportSelectedFirstPages;
    window.exportSelectedContacts = exportSelectedContacts;
    window.saveCurrentSearch = saveCurrentSearch;
    window.toggleSavedSearchMenu = toggleSavedSearchMenu;
    window.applySavedSearchById = applySavedSearchById;
    window.deleteSavedSearchById = deleteSavedSearchById;
    console.log('Advanced Search functions exposed globally');
}
