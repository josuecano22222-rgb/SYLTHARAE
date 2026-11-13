/**
 * Search Enhanced Page JavaScript
 * Extracted from search_enhanced.html
 * Enhanced with advanced search features
 */

import { initializeSearch, loadSearchHistory, loadSavedSearches } from '../modules/search/global-search.js';
import advancedSearch from '../modules/search/advanced-search.js';
import { enhanceMultiSelect, iconForFileType } from '../modules/ui/multi-select-dropdown.js';

// Clear search history function
window.enhancedSearch = window.enhancedSearch || {};

window.enhancedSearch.clearHistory = async function() {
    const confirmMsg = window.appTranslations?.['Clear all search history?']
        || 'Clear all search history?';
    if (!window.confirm(confirmMsg)) return;

    const button = document.getElementById('clearSearchHistoryBtn');
    if (button?.disabled) return;
    const originalChildren = button
        ? Array.from(button.childNodes, node => node.cloneNode(true))
        : [];
    if (button) {
        button.disabled = true;
        button.setAttribute('aria-busy', 'true');
        const spinner = document.createElement('span');
        spinner.className = 'spinner-border spinner-border-sm';
        spinner.setAttribute('aria-hidden', 'true');
        button.replaceChildren(spinner);
    }

    try {
        const response = await fetch('/api/search/history', {
            method: 'DELETE',
            headers: {
                'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || ''
            }
        });
        if (!response.ok) throw new Error('History request failed');

        const searchHistoryEl = document.getElementById('searchHistory');
        if (searchHistoryEl) {
            const message = document.createElement('p');
            message.className = 'text-muted small';
            message.textContent = searchHistoryEl.dataset.emptyMessage
                || window.appTranslations?.['No search history']
                || 'No search history';
            searchHistoryEl.replaceChildren(message);
        }
    } catch (error) {
        console.error('Error clearing history:', error);
        const errorMessage = window.appTranslations?.['Error clearing history'] || 'Error clearing history';
        if (typeof window.showError === 'function') window.showError(errorMessage);
        else window.alert(errorMessage);
    } finally {
        if (button?.isConnected) {
            button.replaceChildren(...originalChildren);
            button.disabled = false;
            button.removeAttribute('aria-busy');
        }
    }
};

/**
 * Default initialization function for universal-initializer
 */
export default async function init() {
    document.getElementById('clearSearchHistoryBtn')?.addEventListener('click', () => {
        window.enhancedSearch.clearHistory();
    });
    const typeFilter = document.getElementById('filterFileType');
    if (typeFilter) {
        enhanceMultiSelect(typeFilter, {
            placeholder: window.appTranslations?.['All types'] || 'All types',
            compact: true,
            iconForOption: (value) => iconForFileType(value),
        });
    }
    initializeSearch();
    advancedSearch.initializeAdvancedSearch();
    loadSearchHistory();
    loadSavedSearches();
}

