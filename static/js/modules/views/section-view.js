/**
 * Section View Loader
 * Extracted from the legacy file-management-system.js
 */

import { navigationState, sectionCursorState } from '../core/state.js';
import { translations } from '../core/config.js';
import { getSectionEndpoint } from '../api/endpoints.js';
import { apiGet } from '../api/api-client.js';
import { renderGridView, renderListView } from '../rendering/grid-renderer.js';
import { geolocationExportParamsFromApi } from '../ui/section-export-menu.js';
import { renderSectionPaginationControls, updateNavItemCount, initializeSectionPaginationControls } from '../rendering/pagination.js';
import { addToHistory, updateNavButtons } from '../navigation/history.js';
import { updateSidebarCount } from '../ui/sidebar.js';

// RACE CONDITION FIX: Track request sequence and pending requests
let currentRequestSequence = 0;
let pendingRequestController = null;

/**
 * Load section view (category, keywords, titles, sources, sides, hash)
 */
export async function loadSectionView(section, page = 1) {
    const contentView = document.getElementById('unifiedContentView');
    if (!contentView) return;

    // Reset pagination state when switching sections (page 1) or ensure proper state
    const wasDifferentSection = navigationState.currentSection !== section;
    navigationState.currentSection = section;
    
    // If switching sections, reset to page 1 and clear cursor state
    if (wasDifferentSection && page === 1) {
        navigationState.sectionPagination.currentPage = 1;
        const state = sectionCursorState[section];
        if (state) {
            state.pageToCursor.clear();
            state.cursorToPage.clear();
            state.pageToCursor.set(1, null);
            state.cursorToPage.set(null, 1);
        }
    } else {
        navigationState.sectionPagination.currentPage = page;
    }
    
    contentView.innerHTML = `<div class="content-loading"><i class="bi bi-arrow-repeat"></i><div>${translations.loading || 'Loading...'}</div></div>`;

    const endpointFn = getSectionEndpoint(section);
    if (!endpointFn) {
        contentView.innerHTML = `<div class="empty-state">${translations.errorLoadingItems || 'Error loading items'}</div>`;
        return;
    }

    const perPage = navigationState.sectionPagination.perPage;
    const search = navigationState.searchQuery || '';
    const params = { limit: perPage };
    if (search) params.search = search;

    // Add filter parameters if filters are active
    const activeFilters = navigationState.activeFilters || {};
    if (activeFilters.category) {
        params.category_id = activeFilters.category;
    }
    if (activeFilters.source) {
        params.source_id = activeFilters.source;
    }
    if (activeFilters.side) {
        params.side_id = activeFilters.side;
    }
    if (activeFilters.dateFrom) {
        params.date_from = activeFilters.dateFrom;
    }
    if (activeFilters.dateTo) {
        params.date_to = activeFilters.dateTo;
    }

    const sortBy = navigationState.sortBy || 'file_count_desc';
    const sortParts = sortBy.split('_');
    const sortField = sortParts.slice(0, -1).join('_');
    const sortDir = sortParts[sortParts.length - 1];
    
    // Always pass sort parameters to API for server-side sorting
    if (sortField === 'file_count') {
        params.sort_by = 'file_count';
        params.sort_dir = sortDir;
    } else {
        params.sort_by = sortField;
        params.sort_dir = sortDir;
    }
    // if (section === 'titles') {
    //     params.group_similar = 'true';
    //     params.similarity_threshold = '0.8';
    // }

    // cursor handling
    let cursor = null;
    const state = sectionCursorState[section];
    if (page === 1 && state) {
        state.pageToCursor.clear();
        state.cursorToPage.clear();
        state.pageToCursor.set(1, null);
        state.cursorToPage.set(null, 1);
    } else if (state && state.pageToCursor.has(page)) {
        cursor = state.pageToCursor.get(page);
    } else if (page > 1) {
        await loadSectionPageSequentially(section, page, endpointFn, params, perPage, search);
        return;
    }
    if (cursor !== null) params.cursor = cursor;

    const sectionExportQuery = section === 'geolocation'
        ? geolocationExportParamsFromApi(params)
        : '';

    // RACE CONDITION FIX: Cancel previous request and track sequence
    if (pendingRequestController) {
        pendingRequestController.abort();
    }
    
    const sequence = ++currentRequestSequence;
    pendingRequestController = new AbortController();
    
    // Add abort signal to request options
    const requestOptions = {
        signal: pendingRequestController.signal
    };

    try {
        const data = await apiGet(endpointFn(params), {}, requestOptions);
        
        // RACE CONDITION FIX: Only process if this is still the latest request
        if (sequence !== currentRequestSequence) {
            console.log(`Ignoring stale response for section ${section}, page ${page} (sequence ${sequence}, current ${currentRequestSequence})`);
            return;
        }
        
        // Clear pending request since this one completed
        pendingRequestController = null;
        
        // apiGet already throws if data.success === false

        let items = data.data || [];
        if (data.grouped && section === 'titles') {
            items = items.map(item => {
                if (item.is_group) {
                    item.name_display = `${item.name_display} (${item.group_count} ${item.is_identical ? 'identical' : 'similar'})`;
                }
                return item;
            });
        }

        // Client-side sort as fallback if server didn't sort (shouldn't be needed if API sorts correctly)
        // Only apply if items are not already sorted by the server
        // Note: Server-side sorting is preferred, so this is mainly a fallback

        if (state) {
            state.total = data.total_estimated || 0;
            state.currentPage = page;
            state.pageToCursor.set(page, cursor);
            state.cursorToPage.set(cursor, page);
            if (data.next_cursor !== null && data.next_cursor !== undefined) {
                state.pageToCursor.set(page + 1, data.next_cursor);
                state.cursorToPage.set(data.next_cursor, page + 1);
            }
            if (data.prev_cursor !== null && data.prev_cursor !== undefined) {
                const prevPage = Math.max(1, page - 1);
                state.pageToCursor.set(prevPage, data.prev_cursor);
                state.cursorToPage.set(data.prev_cursor, prevPage);
            }
        }

        // Handle empty results - still render the section with header and Add button
        if (items.length === 0) {
            updateNavItemCount(0, 0, 0);
            navigationState.sectionPagination = { currentPage: 1, perPage, total: 0, totalPages: 0, has_prev: false, has_next: false };
            
            // Still render the section view with header and Add button, even when empty
            let html = '';
            if (navigationState.currentView === 'grid') {
                html = renderGridView(items, section, false, sectionExportQuery);
            } else {
                html = renderListView(items, section, false, sectionExportQuery);
            }
            
            requestAnimationFrame(() => {
                contentView.innerHTML = html;
            });
            return;
        }

        // Calculate accurate totals and pagination
        const total = data.total_estimated !== undefined && data.total_estimated !== null 
            ? data.total_estimated 
            : items.length;
        
        // Calculate total pages based on actual total
        let totalPages = total > 0 ? Math.ceil(total / perPage) : 1;
        
        // If we don't have a next page, ensure totalPages doesn't exceed current page
        // This prevents showing pages that don't exist
        if (!data.has_next && items.length > 0) {
            // We're on the last page, so totalPages should be at most the current page
            totalPages = Math.min(totalPages, page);
        }
        
        const startIndex = items.length > 0 ? ((page - 1) * perPage + 1) : 0;
        const endIndex = items.length > 0 ? Math.min(startIndex + items.length - 1, total) : 0;

        navigationState.sectionPagination = {
            currentPage: page,
            perPage,
            total,
            totalPages,
            has_prev: data.has_prev || false,
            has_next: data.has_next || false
        };
        updateNavItemCount(startIndex, endIndex, total);
        
        // Update sidebar count for this section
        if (data.total_estimated !== undefined && data.total_estimated !== null) {
            updateSidebarCount(section, data.total_estimated);
        }

        let html = '';
        if (navigationState.currentView === 'grid') {
            html = renderGridView(items, section, false, sectionExportQuery);
        } else {
            html = renderListView(items, section, false, sectionExportQuery);
        }
        // Only add pagination if there are items and more than one page
        if (items.length > 0 && totalPages > 1) {
            html += renderSectionPaginationControls(section);
        }
        
        // Use requestAnimationFrame for smoother rendering
        requestAnimationFrame(() => {
            contentView.innerHTML = html;
            // Attach event listeners to items after rendering
            requestAnimationFrame(() => {
                attachItemEventListeners(contentView, section);
                // Initialize pagination controls after DOM is ready
                if (items.length > 0 && totalPages > 1) {
                    initializeSectionPaginationControls(section);
                }
            });
        });
    } catch (error) {
        // RACE CONDITION FIX: Only show error if this is still the latest request
        if (sequence !== currentRequestSequence && error.name === 'AbortError') {
            // This was cancelled by a newer request - ignore silently
            return;
        }
        
        // Clear pending request on error
        if (sequence === currentRequestSequence) {
            pendingRequestController = null;
        }
        
        console.error(`Error loading section ${section}:`, error);
        const errorMsg = error.name === 'AbortError' 
            ? (translations.requestCancelled || 'Request cancelled')
            : error.message;
        contentView.innerHTML = `<div class="empty-state">${translations.errorLoadingItems || 'Error loading items'}: ${errorMsg}</div>`;
        updateNavItemCount(0, 0, 0);
    }
}

/**
 * Attach event listeners to explorer items and file-row items
 * Note: Global event delegation handles most clicks, but we keep this as a fallback
 * and for keyboard navigation support
 */
function attachItemEventListeners(container, section) {
    // Since we have global event delegation, we only need to attach keyboard handlers
    // and ensure items are focusable for accessibility
    
    // Make explorer-item elements focusable and add keyboard handlers
    container.querySelectorAll('.explorer-item[data-item-id]').forEach(item => {
        if (!item.hasAttribute('tabindex')) {
            item.setAttribute('tabindex', '0');
        }
        
        // Only add keyboard handler (clicks are handled by global delegation)
        item.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                e.stopPropagation();
                item.click(); // Trigger click which will be handled by global delegation
            }
        }, { once: false });
    });
    
    // Make file-row-item elements focusable and add keyboard handlers
    container.querySelectorAll('.file-row-item[data-item-id]').forEach(item => {
        if (!item.hasAttribute('tabindex')) {
            item.setAttribute('tabindex', '0');
        }
        
        // Only add keyboard handler (clicks are handled by global delegation)
        item.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                e.stopPropagation();
                item.click(); // Trigger click which will be handled by global delegation
            }
        }, { once: false });
    });
}

export async function loadSectionPage(section, page) {
    if (page < 1) page = 1;
    
    // Update history and navigation buttons (like old file did)
    const state = { type: 'section', section: section, itemId: null, itemName: null, page: page };
    addToHistory(state);
    await loadSectionView(section, page);
    updateNavButtons();
}

async function loadSectionPageSequentially(section, targetPage, endpointFn, baseParams, perPage, search = '') {
    const state = sectionCursorState[section];
    const contentView = document.getElementById('unifiedContentView');
    if (!state || !contentView) return;

    try {
        let currentPage = 1;
        let currentCursor = null;

        while (currentPage < targetPage) {
            const params = { ...baseParams };
            // Add filter parameters if filters are active
            const activeFilters = navigationState.activeFilters || {};
            if (activeFilters.category) {
                params.category_id = activeFilters.category;
            }
            if (activeFilters.source) {
                params.source_id = activeFilters.source;
            }
            if (activeFilters.side) {
                params.side_id = activeFilters.side;
            }
            if (activeFilters.dateFrom) {
                params.date_from = activeFilters.dateFrom;
            }
            if (activeFilters.dateTo) {
                params.date_to = activeFilters.dateTo;
            }
            
            // Add sort parameters
            const sortBy = navigationState.sortBy || 'file_count_desc';
            const sortParts = sortBy.split('_');
            const sortField = sortParts.slice(0, -1).join('_');
            const sortDir = sortParts[sortParts.length - 1];
            if (sortField === 'file_count') {
                params.sort_by = 'file_count';
                params.sort_dir = sortDir;
            } else {
                params.sort_by = sortField;
                params.sort_dir = sortDir;
            }
            
            if (currentCursor !== null) params.cursor = currentCursor;
            const data = await apiGet(endpointFn(params));
            // apiGet already throws if data.success === false

            state.pageToCursor.set(currentPage, currentCursor);
            state.cursorToPage.set(currentCursor, currentPage);
            
            // Check if there's a next page
            if (!data.has_next || !data.next_cursor) {
                // We've reached the end, navigate to the last available page instead
                console.warn(`Cannot navigate to page ${targetPage}: reached end at page ${currentPage}. Navigating to page ${currentPage} instead.`);
                targetPage = currentPage;
                currentCursor = null; // Use the current page's cursor (which is already set)
                break;
            }
            
            currentCursor = data.next_cursor;
            currentPage++;
        }

        const params = { ...baseParams };
        // Add filter parameters if filters are active
        const activeFilters = navigationState.activeFilters || {};
        if (activeFilters.category) {
            params.category_id = activeFilters.category;
        }
        if (activeFilters.source) {
            params.source_id = activeFilters.source;
        }
        if (activeFilters.side) {
            params.side_id = activeFilters.side;
        }
        if (activeFilters.dateFrom) {
            params.date_from = activeFilters.dateFrom;
        }
        if (activeFilters.dateTo) {
            params.date_to = activeFilters.dateTo;
        }
        
        // Add sort parameters
        const sortBy = navigationState.sortBy || 'file_count_desc';
        const sortParts = sortBy.split('_');
        const sortField = sortParts.slice(0, -1).join('_');
        const sortDir = sortParts[sortParts.length - 1];
        if (sortField === 'file_count') {
            params.sort_by = 'file_count';
            params.sort_dir = sortDir;
        } else {
            params.sort_by = sortField;
            params.sort_dir = sortDir;
        }
        
        if (currentCursor !== null) params.cursor = currentCursor;

        const sectionExportQuery = section === 'geolocation'
            ? geolocationExportParamsFromApi(params)
            : '';
        
        // Only set cursor mapping if we have a cursor
        if (currentCursor !== null) {
            state.pageToCursor.set(targetPage, currentCursor);
            state.cursorToPage.set(currentCursor, targetPage);
        }

        const data = await apiGet(endpointFn(params));
        // apiGet already throws if data.success === false

        const items = data.data || [];
        if (items.length === 0) {
            updateNavItemCount(0, 0, 0);
            navigationState.sectionPagination = { currentPage: targetPage, perPage, total: 0, totalPages: 0, has_prev: false, has_next: false };
            
            // Still render the section view with header and Add button, even when empty
            let html = navigationState.currentView === 'grid'
                ? renderGridView(items, section, false, sectionExportQuery)
                : renderListView(items, section, false, sectionExportQuery);
            
            requestAnimationFrame(() => {
                contentView.innerHTML = html;
            });
            return;
        }

        // Calculate accurate totals and pagination
        const total = data.total_estimated !== undefined && data.total_estimated !== null 
            ? data.total_estimated 
            : items.length;
        
        // Calculate total pages based on actual total
        let totalPages = total > 0 ? Math.ceil(total / perPage) : 1;
        
        // If we don't have a next page, ensure totalPages doesn't exceed current page
        // This prevents showing pages that don't exist
        if (!data.has_next && items.length > 0) {
            // We're on the last page, so totalPages should be at most the current page
            totalPages = Math.min(totalPages, targetPage);
        }
        
        const startIndex = items.length > 0 ? ((targetPage - 1) * perPage + 1) : 0;
        const endIndex = items.length > 0 ? Math.min(startIndex + items.length - 1, total) : 0;

        navigationState.sectionPagination = {
            currentPage: targetPage,
            perPage,
            total,
            totalPages,
            has_prev: data.has_prev || false,
            has_next: data.has_next || false
        };
        updateNavItemCount(startIndex, endIndex, total);
        
        // Update sidebar count for this section
        if (data.total_estimated !== undefined && data.total_estimated !== null) {
            updateSidebarCount(section, data.total_estimated);
        }

        let html = navigationState.currentView === 'grid'
            ? renderGridView(items, section, false, sectionExportQuery)
            : renderListView(items, section, false, sectionExportQuery);
        // Only add pagination if there are items and more than one page
        if (items.length > 0 && totalPages > 1) {
            html += renderSectionPaginationControls(section);
        }
        
        // Optimize rendering: show content immediately
        contentView.innerHTML = html;
        
        // Attach event listeners and initialize pagination in next frame
        requestAnimationFrame(() => {
            attachItemEventListeners(contentView, section);
            // Initialize pagination controls after DOM is ready
            if (items.length > 0 && totalPages > 1) {
                initializeSectionPaginationControls(section);
            }
        });
    } catch (error) {
        console.error(`Error in sequential loading for section ${section}:`, error);
        contentView.innerHTML = `<div class="empty-state">${translations.errorLoadingItems || 'Error loading items'}: ${error.message}</div>`;
        updateNavItemCount(0, 0, 0);
    }
}

