/**
 * Core Navigation Functions
 * Handles navigation between root, sections, and items
 */

import { navigationState, sectionCursorState } from '../core/state.js';
import { translations, sectionLabels } from '../core/config.js';
import { addToHistory, updateNavButtons } from './history.js';
import { updateBreadcrumb } from './breadcrumb.js';
import { loadRootView } from '../views/root-view.js';
import { loadSectionView } from '../views/section-view.js';
import { loadItemView, loadItemFilesWithFilters } from '../views/item-view.js';
import { updateSidebarActiveState } from '../ui/sidebar.js';
import { initEventDelegation } from './event-delegation.js';

/**
 * Initialize navigation
 */
export function initNavigation() {
    console.log('Initializing navigation...');
    // One initialization per content view: the module's own self-run and
    // the archives page handler can both call this on a full load, and a
    // swap revisit hands it a fresh (empty) view. The marker lives on the
    // element, so a swapped-in view is initialized exactly once and a
    // full load can never double-fetch the root data.
    const initView = document.getElementById('unifiedContentView');
    if (initView) {
        if (initView.dataset.navInitialized) {
            console.log('Navigation: this content view is already initialized');
            return;
        }
        initView.dataset.navInitialized = '1';
    }

    // Soft-nav reuses the cached state module across visits. Clear
    // transient search/filters so a leftover query from the previous
    // archives session cannot empty every section on the next open.
    navigationState.searchQuery = '';
    navigationState.activeFilters = {};
    navigationState.sectionPagination.currentPage = 1;
    navigationState.filePagination.currentPage = 1;
    navigationState.currentSection = null;
    navigationState.currentFileSection = null;
    navigationState.currentFileItemId = null;
    const searchInput = document.getElementById('globalSearch');
    if (searchInput) searchInput.value = '';

    // Initialize global event delegation first
    initEventDelegation();
    
    // Initialize perPage selector with current value
    const perPageSelect = document.getElementById('perPageSection');
    if (perPageSelect) {
        perPageSelect.value = navigationState.sectionPagination.perPage || 10;
    }
    
    // Wait a bit to ensure DOM is fully ready
    setTimeout(() => {
        const contentView = document.getElementById('unifiedContentView');
        if (contentView) {
            console.log('unifiedContentView found, navigating to root...');
            navigateToRoot();
        } else {
            // Only log warning if we're actually supposed to be on archives page
            // This function should only be called on archives pages, but check anyway
            const isArchivesPage = document.querySelector('.unified-content-view, #unifiedContentView') !== null;
            if (isArchivesPage) {
                console.warn('unifiedContentView not found! Retrying in 200ms...');
                // Retry after a longer delay in case DOM isn't ready
                setTimeout(() => {
                    const retryContentView = document.getElementById('unifiedContentView');
                    if (retryContentView) {
                        console.log('unifiedContentView found on retry, navigating to root...');
                        navigateToRoot();
                    } else {
                        console.warn('unifiedContentView still not found after retry - navigation may not work correctly');
                    }
                }, 200);
            } else {
                // Not on archives page - this is expected, don't log errors
                console.log('Navigation: Not on archives page, skipping unifiedContentView initialization');
            }
        }
    }, 100);
}

/**
 * Navigate to root (home)
 */
export function navigateToRoot() {
    const state = { type: 'root', section: null, itemId: null, itemName: null };
    addToHistory(state);
    updateBreadcrumb([{ name: translations.home || 'Home', state: state }]);
    loadRootView();
    updateNavButtons();
    updateSidebarActiveState(null); // Clear active state when at root
}

/**
 * Navigate to a section
 * @param {string} section - Section name (category, keywords, titles, sources, sides, hash)
 */
export function navigateToSection(section) {
    console.log('Navigating to section:', section);
    
    if (!section) {
        console.error('Section parameter is missing');
        return;
    }
    
    // Reset pagination when navigating to a new section
    navigationState.sectionPagination.currentPage = 1;
    
    const state = { type: 'section', section: section, itemId: null, itemName: null };
    addToHistory(state);
    updateBreadcrumb([
        { name: translations.home || 'Home', state: { type: 'root' } },
        { name: sectionLabels[section] || section, state: state }
    ]);
    loadSectionView(section, 1);
    updateNavButtons();
    updateSidebarActiveState(section);
}

/**
 * Navigate to an item within a section
 * @param {string} section - Section name
 * @param {number|string} itemId - Item ID
 * @param {string} itemName - Item name (optional)
 */
export function navigateToItem(section, itemId, itemName = null) {
    console.log('Navigating to item:', { section, itemId, itemName });
    
    if (!section || !itemId) {
        console.error('Section or itemId parameter is missing');
        if (window.showError) {
            window.showError(translations.invalidNavigation || 'Invalid navigation parameters');
        }
        return;
    }
    
    // Reset file pagination when navigating to a new item
    navigationState.filePagination.currentPage = 1;
    
    const state = { type: 'item', section: section, itemId: itemId, itemName: itemName };
    addToHistory(state);
    
    // Build breadcrumb path
    const sectionState = { type: 'section', section: section };
    const breadcrumbPath = [
        { name: translations.home || 'Home', state: { type: 'root' } },
        { name: sectionLabels[section] || section, state: sectionState },
        { name: itemName || `${translations.items} ${itemId}`, state: state }
    ];
    updateBreadcrumb(breadcrumbPath);
    
    loadItemView(section, itemId, itemName, 1);
    updateNavButtons();
    updateSidebarActiveState(section);
}

/**
 * Handle sort change
 */
export function handleSortChange() {
    const sortSelect = document.getElementById('sortBy');
    if (!sortSelect) return;
    
    const newSort = sortSelect.value;
    if (navigationState.sortBy !== newSort) {
        navigationState.sortBy = newSort;
        
        // Reset to page 1 when sort changes and clear cursor state
        navigationState.sectionPagination.currentPage = 1;
        Object.keys(sectionCursorState).forEach(section => {
            const state = sectionCursorState[section];
            if (state) {
                state.pageToCursor.clear();
                state.cursorToPage.clear();
                state.pageToCursor.set(1, null);
                state.cursorToPage.set(null, 1);
            }
        });
        
        // Reload current section with new sort
        if (navigationState.currentSection) {
            loadSectionView(navigationState.currentSection, 1);
        } else {
            // If at root, reload root view
            loadRootView();
        }
    }
}

/**
 * Handle per page change
 */
export function handlePerPageChange() {
    const perPageSelect = document.getElementById('perPageSection');
    if (!perPageSelect) return;
    
    const newPerPage = parseInt(perPageSelect.value) || 50;
    if (navigationState.sectionPagination.perPage !== newPerPage) {
        navigationState.sectionPagination.perPage = newPerPage;
        
        // Reset to page 1 when perPage changes and clear cursor state
        navigationState.sectionPagination.currentPage = 1;
        Object.keys(sectionCursorState).forEach(section => {
            const state = sectionCursorState[section];
            if (state) {
                state.pageToCursor.clear();
                state.cursorToPage.clear();
                state.pageToCursor.set(1, null);
                state.cursorToPage.set(null, 1);
            }
        });
        
        // Reload current section with new perPage
        if (navigationState.currentSection) {
            loadSectionView(navigationState.currentSection, 1);
        } else {
            // If at root, reload root view
            loadRootView();
        }
    }
}

/**
 * Handle file per page change
 */
export function handleFilePerPageChange() {
    const perPageSelect = document.getElementById('perPageFiles');
    if (!perPageSelect) return;
    
    const newPerPage = parseInt(perPageSelect.value) || 50;
    if (navigationState.filePagination.perPage !== newPerPage) {
        navigationState.filePagination.perPage = newPerPage;
        
        // Reset to page 1 when perPage changes
        navigationState.filePagination.currentPage = 1;
        
        // Reload current file view with new perPage
        if (navigationState.currentFileSection && navigationState.currentFileItemId) {
            // Get current state from history to preserve item name
            const currentState = navigationState.history?.[navigationState.currentIndex];
            const itemName = currentState?.itemName || null;
            
            // Check if we have filters applied
            if (navigationState.currentSourceFilter || navigationState.currentSideFilter) {
                // Use loadItemFilesWithFilters for filtered views
                loadItemFilesWithFilters(
                    navigationState.currentFileSection,
                    navigationState.currentFileItemId,
                    itemName,
                    navigationState.currentSourceFilter,
                    navigationState.currentSideFilter,
                    1  // Reset to page 1
                );
            } else {
                // Reload files with new per page setting
                loadItemView(
                    navigationState.currentFileSection,
                    navigationState.currentFileItemId,
                    itemName,
                    1  // Reset to page 1
                );
            }
        }
    }
}

// Expose handleSortChange globally for HTML onclick
window.handleSortChange = handleSortChange;
// Expose handlePerPageChange globally for HTML onclick
window.handlePerPageChange = handlePerPageChange;
// Expose handleFilePerPageChange globally for HTML onclick
window.handleFilePerPageChange = handleFilePerPageChange;

