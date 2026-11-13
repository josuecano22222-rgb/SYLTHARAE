/**
 * Filters Module
 * Handles filter panel visibility and filter application
 */

import { navigationState } from '../core/state.js';
import { loadSectionView } from '../views/section-view.js';
import notificationSystem from './notifications.js';
import { translations } from '../core/config.js';

let globalSearchTimeout = null;
let lastGlobalSearchQuery = '';

/**
 * Hide filters panel
 */
export function hideFilters() {
    const filtersPanel = document.getElementById('filtersPanel');
    if (filtersPanel) {
        filtersPanel.style.display = 'none';
    }
}

/**
 * Show filters panel
 */
export function showFilters() {
    const filtersPanel = document.getElementById('filtersPanel');
    if (filtersPanel) {
        filtersPanel.style.display = 'block';
    }
}

/**
 * Apply filters to current view - FIXED VERSION: Reloads from server
 */
export function applyFilters() {
    const category = document.getElementById('filterCategory')?.value || '';
    const source = document.getElementById('filterSource')?.value || '';
    const side = document.getElementById('filterSide')?.value || '';
    const dateFrom = document.getElementById('filterDateFrom')?.value || '';
    const dateTo = document.getElementById('filterDateTo')?.value || '';
    
    // VALIDATION FIX: Validate date range
    if (dateFrom && dateTo && dateFrom > dateTo) {
        notificationSystem.error('Start date must be before or equal to end date');
        return;
    }
    
    // Store active filters in navigation state
    if (!navigationState.activeFilters) {
        navigationState.activeFilters = {};
    }
    
    navigationState.activeFilters = {
        category: category || null,
        source: source || null,
        side: side || null,
        dateFrom: dateFrom || null,
        dateTo: dateTo || null
    };
    
    // FILTER FIX: Reload current view from server with filters applied
    const currentState = navigationState.history?.[navigationState.currentIndex];
    
    if (currentState) {
        if (currentState.type === 'section') {
            // Reload section with filters
            loadSectionView(currentState.section, 1);
        } else if (currentState.type === 'item') {
            // Reload item view with filters
            import('../views/item-view.js').then(module => {
                module.loadItemView(currentState.section, currentState.itemId, currentState.itemName, 1);
            });
        } else if (currentState.type === 'root') {
            // Reload root view
            if (window.fms?.views?.root?.loadRootView) {
                window.fms.views.root.loadRootView();
            }
        }
    } else {
        // If no current state, reload root view
        if (window.fms?.views?.root?.loadRootView) {
            window.fms.views.root.loadRootView();
        }
    }
    
    notificationSystem.success(translations.filtersApplied || 'Filters applied!');
}

/**
 * Apply filters to displayed items (client-side filtering)
 */
function applyFiltersToDisplayedItems() {
    const filters = navigationState.activeFilters || {};
    const categoryId = filters.category;
    const sourceId = filters.source;
    const sideId = filters.side;
    const dateFrom = filters.dateFrom;
    const dateTo = filters.dateTo;
    
    // Filter file rows
    const fileRows = document.querySelectorAll('.file-row-item[data-file-id]');
    fileRows.forEach(row => {
        let matches = true;
        
        if (categoryId) {
            const rowCategory = row.getAttribute('data-file-category');
            if (rowCategory !== categoryId) matches = false;
        }
        
        if (sourceId && matches) {
            const rowSource = row.getAttribute('data-file-source-id');
            if (rowSource !== sourceId) matches = false;
        }
        
        if (sideId && matches) {
            const rowSide = row.getAttribute('data-file-side-id');
            if (rowSide !== sideId) matches = false;
        }
        
        if (dateFrom && matches) {
            const rowDate = row.getAttribute('data-file-date');
            if (rowDate && rowDate < dateFrom) matches = false;
        }
        
        if (dateTo && matches) {
            const rowDate = row.getAttribute('data-file-date');
            if (rowDate && rowDate > dateTo) matches = false;
        }
        
        row.style.display = matches ? '' : 'none';
    });
    
    updateSearchResultsCount();
}

/**
 * Reset all filters
 */
export function resetFilters() {
    const filterCategory = document.getElementById('filterCategory');
    const filterSource = document.getElementById('filterSource');
    const filterSide = document.getElementById('filterSide');
    const filterDateFrom = document.getElementById('filterDateFrom');
    const filterDateTo = document.getElementById('filterDateTo');
    
    if (filterCategory) filterCategory.value = '';
    if (filterSource) filterSource.value = '';
    if (filterSide) filterSide.value = '';
    if (filterDateFrom) filterDateFrom.value = '';
    if (filterDateTo) filterDateTo.value = '';
    
    // Clear active filters
    navigationState.activeFilters = {};
    
    // Show all items
    document.querySelectorAll('.file-row-item[data-file-id], .file-row-item[data-item-id]').forEach(item => {
        item.style.display = '';
    });
    
    // Reload current view without filters
    const currentState = navigationState.history?.[navigationState.currentIndex];
    if (currentState && currentState.type === 'section') {
        loadSectionView(currentState.section, false);
    }
    
    updateSearchResultsCount();
}

/**
 * Update search results count display
 */
function updateSearchResultsCount() {
    const contentView = document.getElementById('unifiedContentView');
    if (!contentView) return;
    
    // Count visible files
    const visibleFiles = contentView.querySelectorAll('.file-card[data-file-id]:not([style*="display: none"]), .file-grid-item[data-file-id]:not([style*="display: none"]), .file-row-item[data-file-id]:not([style*="display: none"])').length;
    const totalFiles = contentView.querySelectorAll('.file-card[data-file-id], .file-grid-item[data-file-id], .file-row-item[data-file-id]').length;
    
    // Count visible section items
    const visibleItems = contentView.querySelectorAll('.explorer-item[data-item-id]:not([style*="display: none"]), .file-row-item[data-item-id]:not([style*="display: none"]), [data-item-id]:not([style*="display: none"])').length;
    const totalItems = contentView.querySelectorAll('.explorer-item[data-item-id], .file-row-item[data-item-id], [data-item-id]').length;
    
    // Update count display if it exists
    const countElement = document.getElementById('navItemCountText');
    if (countElement) {
        if (totalFiles > 0) {
            countElement.textContent = `${visibleFiles} / ${totalFiles}`;
            const navItemCount = document.getElementById('navItemCount');
            if (navItemCount) navItemCount.style.display = '';
        } else if (totalItems > 0) {
            countElement.textContent = `${visibleItems} / ${totalItems}`;
            const navItemCount = document.getElementById('navItemCount');
            if (navItemCount) navItemCount.style.display = '';
        } else {
            const navItemCount = document.getElementById('navItemCount');
            if (navItemCount) navItemCount.style.display = 'none';
        }
    }
}

/**
 * Handle global search input
 */
export function handleGlobalSearch(query) {
    if (!navigationState) {
        navigationState.searchQuery = query;
    }
    
    const trimmedQuery = query.trim();
    
    // Clear previous timeout
    if (globalSearchTimeout) {
        clearTimeout(globalSearchTimeout);
    }
    
    // If query is empty, clear search and restore view
    if (!trimmedQuery) {
        clearGlobalSearch();
        return;
    }
    
    // Debounce search for better performance (300ms delay)
    globalSearchTimeout = setTimeout(() => {
        performGlobalSearch(trimmedQuery);
    }, 300);
}

/**
 * Perform global search - searches all paginated data via API
 */
async function performGlobalSearch(query) {
    // Avoid duplicate searches
    if (query === lastGlobalSearchQuery) {
        return;
    }
    lastGlobalSearchQuery = query;
    
    const contentView = document.getElementById('unifiedContentView');
    if (!contentView) {
        // Not on archives page, use enhanced search page logic
        if (window.fms?.search?.global?.performSearch) {
            const searchInput = document.getElementById('searchQuery');
            if (searchInput) {
                searchInput.value = query;
            }
            window.fms.search.global.performSearch();
        }
        return;
    }
    
    // Get current navigation state
    const currentState = navigationState.history?.[navigationState.currentIndex];
    
    // Check if files are displayed (item view)
    const fileElements = contentView.querySelectorAll('.file-card[data-file-id], .file-grid-item[data-file-id], .file-row-item[data-file-id]');
    if (fileElements.length > 0 && currentState?.type === 'item') {
        // Search all files for this item via API
        await performItemFilesSearch(query, currentState);
        return;
    }
    
    // Check if section items are displayed
    if (currentState?.type === 'section') {
        // Search all items in this section via API
        await performSectionSearch(query, currentState.section);
        return;
    }
    
    // If at root or no items displayed, show message
    if (currentState?.type === 'root' || !currentState) {
        contentView.innerHTML = `
            <div class="section">
                <div class="empty-state">
                    <i class="bi bi-info-circle display-4 d-block mb-3"></i>
                    <p>${translations.searchWorksOnDisplayedData || 'Search works on displayed data. Please navigate to a section or item to search.'}</p>
                </div>
            </div>
        `;
        return;
    }
    
    // Fallback: try section search
    if (currentState?.section) {
        await performSectionSearch(query, currentState.section);
    }
}

/**
 * Perform comprehensive search (fallback)
 */
async function performComprehensiveSearch(query) {
    const contentView = document.getElementById('unifiedContentView');
    if (!contentView) return;
    
    if (!query || query.trim() === '') {
        // If query is empty, restore normal view
        clearGlobalSearch();
        return;
    }
    
    try {
        // Show loading state
        contentView.innerHTML = `<div class="content-loading"><i class="bi bi-arrow-repeat"></i><div>${translations.loading || 'Loading...'}</div></div>`;
        
        // Use the search API endpoint
        const { apiGet } = await import('../api/api-client.js');
        const { endpoints } = await import('../api/endpoints.js');
        const { escapeHtml } = await import('../core/utils.js');
        
        const params = {
            query: query,
            page: 1,
            per_page: 50,
            search_all_data: 'true'
        };
        
        const apiUrl = endpoints.search(params);
        const data = await apiGet(apiUrl);
        
        // Display results in unifiedContentView
        if (data.results && data.results.length > 0) {
            let html = `
                <div class="section">
                    <div class="section-header">
                        <div class="section-label">
                            <i class="bi bi-search"></i> ${translations.searchResults || 'Search Results'}
                        </div>
                        <div style="color: var(--text-light); font-size: 0.875rem;">
                            ${data.pagination?.total || 0} ${translations.resultsFound || 'results found'} for "${escapeHtml(query)}"
                        </div>
                    </div>
                </div>
            `;
            
            // Group results by type
            const resultsByType = {};
            data.results.forEach(result => {
                const resultType = result.result_type || 'file';
                if (!resultsByType[resultType]) {
                    resultsByType[resultType] = [];
                }
                resultsByType[resultType].push(result);
            });
            
            // Display results grouped by type
            const typeLabels = {
                'file': { icon: 'bi-file-earmark', label: translations.files || 'Files', color: 'primary' },
                'category': { icon: 'bi-tags', label: translations.category || 'Categories', color: 'success' },
                'keyword': { icon: 'bi-key', label: translations.keywords || 'Keywords', color: 'warning' },
                'source': { icon: 'bi-building', label: translations.sources || 'Sources', color: 'info' },
                'side': { icon: 'bi-diagram-3', label: translations.sides || 'Sides', color: 'secondary' },
                'word': { icon: 'bi-text-paragraph', label: translations.words || 'Words', color: 'dark' },
                'title': { icon: 'bi-heading', label: translations.titles || 'Titles', color: 'primary' }
            };
            
            Object.keys(resultsByType).forEach(resultType => {
                const typeInfo = typeLabels[resultType] || { icon: 'bi-circle', label: resultType, color: 'secondary' };
                const typeResults = resultsByType[resultType];
                
                html += `
                    <div class="section">
                        <div class="section-header">
                            <div class="section-label">
                                <i class="bi ${typeInfo.icon}"></i> ${typeInfo.label} (${typeResults.length})
                            </div>
                        </div>
                        <div class="section-content">
                `;
                
                typeResults.forEach(result => {
                    let resultName = result.name || result.file_name || 'Unknown';
                    let resultLink = '#';
                    let resultDetails = '';
                    
                    switch(resultType) {
                        case 'file':
                            resultLink = `/file/${result.id}`;
                            resultDetails = `
                                <div style="font-size: 0.875rem; color: var(--text-light); margin-top: 0.25rem;">
                                    <span style="margin-right: 1rem;"><i class="bi bi-building"></i> ${escapeHtml(result.source_name || 'Unknown')}</span>
                                    <span style="margin-right: 1rem;"><i class="bi bi-diagram-3"></i> ${escapeHtml(result.side_name || 'Unknown')}</span>
                                    <span><i class="bi bi-calendar"></i> ${result.date || 'N/A'}</span>
                                </div>
                            `;
                            break;
                        case 'category':
                            resultLink = `#category/${result.id}`;
                            resultDetails = `<div style="font-size: 0.875rem; color: var(--text-light); margin-top: 0.25rem;"><i class="bi bi-file-earmark"></i> ${result.file_count || 0} files</div>`;
                            break;
                        case 'keyword':
                            resultLink = `#keywords/${result.id}`;
                            resultDetails = `<div style="font-size: 0.875rem; color: var(--text-light); margin-top: 0.25rem;"><i class="bi bi-file-earmark"></i> Used in ${result.usage_count || 0} files</div>`;
                            break;
                        case 'source':
                            resultLink = `#sources/${result.id}`;
                            resultDetails = `<div style="font-size: 0.875rem; color: var(--text-light); margin-top: 0.25rem;"><i class="bi bi-briefcase"></i> ${escapeHtml(result.job || 'N/A')}</div>`;
                            break;
                        case 'side':
                            resultLink = `#sides/${result.id}`;
                            resultDetails = `<div style="font-size: 0.875rem; color: var(--text-light); margin-top: 0.25rem;"><i class="bi bi-hash"></i> ${result.hash_count || 0} hashes</div>`;
                            break;
                        case 'word':
                            resultLink = `#word/${result.id}`;
                            resultDetails = `<div style="font-size: 0.875rem; color: var(--text-light); margin-top: 0.25rem;"><i class="bi bi-file-earmark"></i> ${result.file_count || 0} files</div>`;
                            break;
                        case 'title':
                            resultLink = result.path_id ? `/file/${result.path_id}` : '#';
                            resultDetails = `<div style="font-size: 0.875rem; color: var(--text-light); margin-top: 0.25rem;">${result.file_name ? escapeHtml(result.file_name) : ''}</div>`;
                            break;
                    }
                    
                    html += `
                        <div class="file-card" style="border: 1px solid #e2e8f0; border-radius: 0.5rem; padding: 1rem; margin-bottom: 0.75rem; cursor: pointer;" data-on-click="window.location.assign('${resultLink}')">
                            <div style="font-weight: 600; margin-bottom: 0.25rem;">
                                <i class="bi ${typeInfo.icon} me-2"></i>
                                ${escapeHtml(resultName)}
                            </div>
                            ${resultDetails}
                        </div>
                    `;
                });
                
                html += `
                        </div>
                    </div>
                `;
            });
            
            // Add pagination if needed
            if (data.pagination && data.pagination.total_pages > 1) {
                html += `
                    <div class="section">
                        <div style="display: flex; justify-content: center; gap: 0.5rem; margin-top: 1rem;">
                `;
                if (data.pagination.has_prev) {
                    html += `<button class="btn btn-outline-primary" data-on-click="performGlobalSearchPage('${escapeHtml(query)}', ${data.pagination.page - 1})">${translations.previous || 'Previous'}</button>`;
                }
                html += `<span style="display: flex; align-items: center; padding: 0 1rem;">${translations.currentPage || 'Page'} ${data.pagination.page} ${translations.of || 'of'} ${data.pagination.total_pages}</span>`;
                if (data.pagination.has_next) {
                    html += `<button class="btn btn-outline-primary" data-on-click="performGlobalSearchPage('${escapeHtml(query)}', ${data.pagination.page + 1})">${translations.next || 'Next'}</button>`;
                }
                html += `
                        </div>
                    </div>
                `;
            }
            
            contentView.innerHTML = html;
        } else {
            contentView.innerHTML = `
                <div class="section">
                    <div class="empty-state">
                        <i class="bi bi-search display-4 d-block mb-3"></i>
                        <p>${translations.noResultsFound || 'No results found'} for "${escapeHtml(query)}"</p>
                    </div>
                </div>
            `;
        }
    } catch (error) {
        console.error('Error performing comprehensive search:', error);
        notificationSystem.error(translations.errorPerformingSearch || 'Error performing search');
        contentView.innerHTML = `
            <div class="section">
                <div class="empty-state">
                    <p>${translations.errorPerformingSearch || 'Error performing search'}: ${error.message}</p>
                </div>
            </div>
        `;
    }
}

/**
 * Perform section search - searches all paginated data in a section
 * Fetches all data across all pages and filters client-side
 */
async function performSectionSearch(query, section) {
    const contentView = document.getElementById('unifiedContentView');
    if (!contentView || !section) return;
    
    try {
        // Show loading state
        contentView.innerHTML = `<div class="content-loading"><i class="bi bi-arrow-repeat"></i><div>${translations.loading || 'Loading...'}</div></div>`;
        
        // Import required modules
        const { apiGet } = await import('../api/api-client.js');
        const { getSectionEndpoint } = await import('../api/endpoints.js');
        const { escapeHtml } = await import('../core/utils.js');
        
        // Get the section endpoint function
        const endpointFn = getSectionEndpoint(section);
        if (!endpointFn) {
            throw new Error(`No endpoint found for section: ${section}`);
        }
        
        // Fetch all data by using a very large limit
        // We'll paginate through all results if needed
        let allItems = [];
        let page = 1;
        const perPage = 1000; // Large page size to minimize API calls
        let hasMore = true;
        
        while (hasMore) {
            const params = {
                limit: perPage,
                cursor: page === 1 ? null : undefined // Start from beginning
            };
            
            const data = await apiGet(endpointFn(params));
            
            if (!data.success && data.error) {
                throw new Error(data.error);
            }
            
            const items = data.data || [];
            allItems = allItems.concat(items);
            
            // Check if there are more pages
            // If we got fewer items than requested, we've reached the end
            hasMore = items.length === perPage && (data.pagination?.has_next !== false);
            page++;
            
            // Safety limit - don't fetch more than 10,000 items
            if (allItems.length >= 10000) {
                break;
            }
        }
        
        // Filter items client-side by search query
        const lowerQuery = query.toLowerCase();
        const keywords = query.split(/\s+/).filter(k => k.length > 0);
        
        let filteredItems = allItems;
        if (query) {
            filteredItems = allItems.filter(item => {
                const itemName = (item.name || item.name_display || '').toLowerCase();
                const itemId = String(item.id || '');
                const itemDetails = (item.details || '').toLowerCase();
                
                if (keywords.length > 1) {
                    // All keywords must be found (AND logic)
                    return keywords.every(keyword => 
                        itemName.includes(keyword) || 
                        itemId.includes(keyword) ||
                        itemDetails.includes(keyword)
                    );
                } else {
                    // Single keyword or phrase match
                    return itemName.includes(lowerQuery) || 
                           itemId.includes(lowerQuery) ||
                           itemDetails.includes(lowerQuery);
                }
            });
        }
        
        // Store search query in navigation state
        navigationState.searchQuery = query;
        
        // Render filtered results using section view renderer
        const { renderGridView, renderListView } = await import('../rendering/grid-renderer.js');
        
        const viewMode = navigationState.currentView || 'grid';
        
        let html = '';
        if (viewMode === 'list') {
            html = renderListView(filteredItems, section, false);
        } else {
            html = renderGridView(filteredItems, section, false);
        }
        
        // Add search info header
        const searchInfo = `
            <div class="section" style="margin-bottom: 1rem;">
                <div class="section-header">
                    <div class="section-label">
                        <i class="bi bi-search"></i> ${translations.searchResults || 'Search Results'}
                    </div>
                    <div style="color: var(--text-light); font-size: 0.875rem;">
                        ${filteredItems.length} ${translations.resultsFound || 'results found'} ${allItems.length !== filteredItems.length ? `(${allItems.length} total items searched)` : ''} for "${escapeHtml(query)}"
                    </div>
                </div>
            </div>
        `;
        
        contentView.innerHTML = searchInfo + html;
        
        // Re-initialize event delegation for the new content
        if (window.fms?.navigation?.eventDelegation?.initEventDelegation) {
            window.fms.navigation.eventDelegation.initEventDelegation();
        }
        
    } catch (error) {
        console.error('Error performing section search:', error);
        notificationSystem.error(translations.errorPerformingSearch || 'Error performing search');
        contentView.innerHTML = `
            <div class="section">
                <div class="empty-state">
                    <p>${translations.errorPerformingSearch || 'Error performing search'}: ${error.message}</p>
                </div>
            </div>
        `;
    }
}

/**
 * Perform item files search - searches all paginated files for an item
 * Fetches all files across all pages and filters client-side
 */
async function performItemFilesSearch(query, currentState) {
    const contentView = document.getElementById('unifiedContentView');
    if (!contentView || !currentState || currentState.type !== 'item') return;
    
    try {
        // Show loading state
        contentView.innerHTML = `<div class="content-loading"><i class="bi bi-arrow-repeat"></i><div>${translations.loading || 'Loading...'}</div></div>`;
        
        // Import required modules
        const { apiGet } = await import('../api/api-client.js');
        const { endpoints } = await import('../api/endpoints.js');
        const { renderFilesView } = await import('../views/file-view.js');
        const { escapeHtml } = await import('../core/utils.js');
        
        // Fetch all files by paginating through all pages
        let allFiles = [];
        let page = 1;
        const perPage = 1000; // Large page size to minimize API calls
        let hasMore = true;
        let totalFiles = 0;
        
        while (hasMore) {
            const apiUrl = endpoints.itemFiles(currentState.section, currentState.itemId, { 
                page: page, 
                limit: perPage
            });
            
            const filesData = await apiGet(apiUrl);
            
            if (!filesData.success && filesData.error) {
                throw new Error(filesData.error);
            }
            
            const files = filesData.files || [];
            allFiles = allFiles.concat(files);
            
            // Get total from pagination if available
            if (filesData.pagination) {
                totalFiles = filesData.pagination.total || allFiles.length;
                hasMore = filesData.pagination.has_next === true && files.length === perPage;
            } else {
                // If no pagination info, stop when we get fewer files than requested
                hasMore = files.length === perPage;
            }
            
            page++;
            
            // Safety limit - don't fetch more than 10,000 files
            if (allFiles.length >= 10000) {
                break;
            }
        }
        
        // Filter files client-side by search query
        const lowerQuery = query.toLowerCase();
        const keywords = query.split(/\s+/).filter(k => k.length > 0);
        
        let filteredFiles = allFiles;
        if (query) {
            filteredFiles = allFiles.filter(file => {
                const fileName = (file.name || '').toLowerCase();
                const fileType = (file.type || file.file_type || '').toLowerCase();
                const fileSource = (file.source || file.source_name || '').toLowerCase();
                const fileSide = (file.side || file.side_name || '').toLowerCase();
                const fileDate = (file.file_date || file.date || '').toLowerCase();
                
                if (keywords.length > 1) {
                    // All keywords must be found (AND logic)
                    return keywords.every(keyword => 
                        fileName.includes(keyword) || 
                        fileType.includes(keyword) || 
                        fileSource.includes(keyword) || 
                        fileSide.includes(keyword) ||
                        fileDate.includes(keyword)
                    );
                } else {
                    // Single keyword or phrase match
                    return fileName.includes(lowerQuery) || 
                           fileType.includes(lowerQuery) || 
                           fileSource.includes(lowerQuery) || 
                           fileSide.includes(lowerQuery) ||
                           fileDate.includes(lowerQuery);
                }
            });
        }
        
        // Create pagination object for filtered results
        const pagination = {
            page: 1,
            per_page: filteredFiles.length,
            total: filteredFiles.length,
            total_pages: 1,
            has_prev: false,
            has_next: false,
            total_size: filteredFiles.reduce((sum, f) => sum + (f.size || 0), 0)
        };
        
        // Add search info before rendering
        const searchInfo = `
            <div class="section" style="margin-bottom: 1rem;">
                <div class="section-header">
                    <div class="section-label">
                        <i class="bi bi-search"></i> ${translations.searchResults || 'Search Results'}
                    </div>
                    <div style="color: var(--text-light); font-size: 0.875rem;">
                        ${filteredFiles.length} ${translations.resultsFound || 'results found'} ${allFiles.length !== filteredFiles.length ? `(${allFiles.length} total files searched)` : ''} for "${escapeHtml(query)}"
                    </div>
                </div>
            </div>
        `;
        
        // Render files view with filtered results
        renderFilesView(filteredFiles, currentState.section, currentState.itemName || currentState.section, pagination);
        
        // Prepend search info to content
        const renderedContent = contentView.innerHTML;
        contentView.innerHTML = searchInfo + renderedContent;
        
    } catch (error) {
        console.error('Error performing item files search:', error);
        notificationSystem.error(translations.errorPerformingSearch || 'Error performing search');
        contentView.innerHTML = `
            <div class="section">
                <div class="empty-state">
                    <p>${translations.errorPerformingSearch || 'Error performing search'}: ${error.message}</p>
                </div>
            </div>
        `;
    }
}

/**
 * Filter displayed section items by search query (client-side only - kept for compatibility)
 */
async function filterDisplayedSectionItems(searchQuery) {
    const query = (searchQuery || '').toLowerCase().trim();
    const contentView = document.getElementById('unifiedContentView');
    if (!contentView) return;
    
    // Find all section items (explorer items, file-row items, etc.)
    const sectionItems = contentView.querySelectorAll('.explorer-item[data-item-id], .file-row-item[data-item-id], [data-item-id]');
    
    if (!query) {
        // Show all if no query
        sectionItems.forEach((el) => {
            el.style.display = '';
        });
        updateSearchResultsCount();
        return;
    }
    
    // Split query into keywords for better matching
    const keywords = query.split(/\s+/).filter(k => k.length > 0);
    const lowerQuery = query.toLowerCase();
    
    let visibleCount = 0;
    sectionItems.forEach(element => {
        const itemName = (element.getAttribute('data-item-name') || element.textContent || '').toLowerCase();
        const itemId = element.getAttribute('data-item-id') || '';
        const itemMeta = element.textContent.toLowerCase();
        
        // Check if all keywords match (AND logic) or any part matches
        let matches = false;
        if (keywords.length > 1) {
            // All keywords must be found (AND logic)
            matches = keywords.every(keyword => 
                itemName.includes(keyword) || 
                itemId.includes(keyword) ||
                itemMeta.includes(keyword)
            );
        } else {
            // Single keyword or phrase match
            matches = itemName.includes(lowerQuery) || 
                     itemId.includes(lowerQuery) ||
                     itemMeta.includes(lowerQuery);
        }
        
        if (matches) {
            element.style.display = '';
            visibleCount++;
        } else {
            element.style.display = 'none';
        }
    });
    
    updateSearchResultsCount();
    
    // Show message if no results
    if (visibleCount === 0) {
        const { escapeHtml } = await import('../core/utils.js');
        const noResultsMsg = document.createElement('div');
        noResultsMsg.className = 'empty-state';
        noResultsMsg.style.marginTop = '1rem';
        noResultsMsg.innerHTML = `
            <i class="bi bi-search"></i>
            <p>${translations.noResultsFound || 'No results found'} for "${escapeHtml(searchQuery)}"</p>
        `;
        
        // Remove existing no-results message if any
        const existingMsg = contentView.querySelector('.empty-state[data-search-message]');
        if (existingMsg) {
            existingMsg.remove();
        }
        
        noResultsMsg.setAttribute('data-search-message', 'true');
        contentView.appendChild(noResultsMsg);
    } else {
        // Remove no-results message if results found
        const existingMsg = contentView.querySelector('.empty-state[data-search-message]');
        if (existingMsg) {
            existingMsg.remove();
        }
    }
}

/**
 * Clear global search
 */
async function clearGlobalSearch() {
    lastGlobalSearchQuery = '';
    if (navigationState) {
        navigationState.searchQuery = '';
    }
    
    // Clear search input
    const globalSearchInput = document.getElementById('globalSearch');
    if (globalSearchInput) {
        globalSearchInput.value = '';
    }
    
    // Remove search filter from displayed items
    const contentView = document.getElementById('unifiedContentView');
    if (contentView) {
        // Show all files
        const fileElements = contentView.querySelectorAll('.file-card[data-file-id], .file-grid-item[data-file-id], .file-row-item[data-file-id]');
        fileElements.forEach(el => el.style.display = '');
        
        // Show all section items
        const sectionItems = contentView.querySelectorAll('.explorer-item[data-item-id], .file-row-item[data-item-id], [data-item-id]');
        sectionItems.forEach(el => el.style.display = '');
        
        // Remove no-results message
        const noResultsMsg = contentView.querySelector('.empty-state[data-search-message]');
        if (noResultsMsg) {
            noResultsMsg.remove();
        }
        
        updateSearchResultsCount();
    }
    
    // Restore normal view if needed
    const currentState = navigationState.history?.[navigationState.currentIndex];
    if (currentState) {
        if (currentState.type === 'root') {
            window.fms?.views?.root?.loadRootView?.();
        } else if (currentState.type === 'section') {
            loadSectionView(currentState.section);
        } else if (currentState.type === 'item') {
            const { loadItemView } = await import('../views/item-view.js');
            await loadItemView(currentState.section, currentState.itemId, currentState.itemName);
        }
    } else {
        // If no history, load root view
        window.fms?.views?.root?.loadRootView?.();
    }
}

/**
 * Perform global search with pagination
 */
async function performGlobalSearchPage(query, page) {
    const contentView = document.getElementById('unifiedContentView');
    if (!contentView) return;
    
    try {
        // Show loading state
        contentView.innerHTML = `<div class="content-loading"><i class="bi bi-arrow-repeat"></i><div>${translations.loading || 'Loading...'}</div></div>`;
        
        // Use the search API endpoint
        const { apiGet } = await import('../api/api-client.js');
        const { endpoints } = await import('../api/endpoints.js');
        const { escapeHtml } = await import('../core/utils.js');
        
        const params = {
            query: query,
            page: page,
            per_page: 50,
            search_all_data: 'true'
        };
        
        const apiUrl = endpoints.search(params);
        const data = await apiGet(apiUrl);
        
        // Reuse the same display logic as performComprehensiveSearch
        // (This is a simplified version - in production, you'd extract the display logic)
        performComprehensiveSearch(query);
    } catch (error) {
        console.error('Error performing paginated search:', error);
        notificationSystem.error(translations.errorPerformingSearch || 'Error performing search');
    }
}

// Expose for pagination
if (typeof window !== 'undefined') {
    window.performGlobalSearchPage = performGlobalSearchPage;
}

