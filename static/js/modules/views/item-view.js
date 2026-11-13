/**
 * Item View Loader
 * Extracted from the legacy file-management-system.js
 */

import { navigationState } from '../core/state.js';
import { sectionLabels } from '../core/config.js';
import { endpoints } from '../api/endpoints.js';
import { apiGet } from '../api/api-client.js';
import { renderFilesView, appendFilesToView } from './file-view.js';
import { translations } from '../core/config.js';
import { loadSourceCategoriesKeywordsView } from './source-categories-keywords-view.js';
import { selectedTypesParam } from '../file-operations/selectable-files-ops.js';

/**
 * Build query params for /api/archives/files from navigation state.
 */
function buildFileQueryParams(page = 1) {
    const perPage = navigationState.filePagination?.perPage || 50;
    const filters = navigationState.fileListFilters || {};
    const params = {
        page,
        limit: perPage,
        sort_by: filters.sortBy || 'file_date',
        sort_dir: filters.sortDir || 'desc'
    };
    const types = Array.isArray(filters.fileType)
        ? filters.fileType.filter(Boolean).join(',')
        : String(filters.fileType || '').trim();
    if (types) params.file_type = types;
    if (filters.search) params.search = filters.search;
    if (navigationState.currentSourceFilter?.source_id) {
        params.source_id = navigationState.currentSourceFilter.source_id;
    }
    if (navigationState.currentSideFilter?.side_id) {
        params.side_id = navigationState.currentSideFilter.side_id;
    }
    return params;
}

export async function loadItemView(section, itemId, itemName = null, page = 1, options = {}) {
    const contentView = document.getElementById('unifiedContentView');
    if (!contentView) return;
    
    // For geolocation, each item is a file - open file modal directly
    if (section === 'geolocation') {
        if (window.fms?.fileOperations?.fileDetails?.showFileDetails) {
            window.fms.fileOperations.fileDetails.showFileDetails(itemId, itemName || 'File');
        } else if (window.showFileDetails) {
            window.showFileDetails(itemId, itemName || 'File');
        }
        return;
    }
    
    // For sources and sides, show categories and keywords first instead of files
    if (section === 'sources' || section === 'sides') {
        await loadSourceCategoriesKeywordsView(section, itemId, itemName, page);
        return;
    }

    // Browse by type: keep the toolbar type filter aligned with the selected type
    if (section === 'filetypes') {
        const typeKey = String(itemId || '').replace(/^\./, '').toLowerCase();
        if (!navigationState.fileListFilters) {
            navigationState.fileListFilters = { fileType: '', sortBy: 'file_date', sortDir: 'desc', search: '' };
        }
        if (!options.append) {
            navigationState.fileListFilters.fileType = typeKey ? `.${typeKey}` : '';
            navigationState.typeFilterFromBrowse = true;
        }
    } else if (!options.append && navigationState.typeFilterFromBrowse) {
        // Do not carry a File-Types browse lock into category/keyword/etc. lists
        if (navigationState.fileListFilters) {
            navigationState.fileListFilters.fileType = '';
        }
        navigationState.typeFilterFromBrowse = false;
    }

    const append = !!options.append;
    if (!append) {
        contentView.innerHTML = `<div class="content-loading"><i class="bi bi-arrow-repeat"></i><div>${translations.loadingFiles || 'Loading files...'}</div></div>`;
        navigationState.loadedFiles = [];
    }

    try {
        const params = buildFileQueryParams(page);
        const apiUrl = endpoints.itemFiles(section, itemId, params);
        console.log('Loading files from:', apiUrl);
        
        const filesData = await apiGet(apiUrl);
        console.log('Files data received:', filesData);

        navigationState.currentFileSection = section;
        navigationState.currentFileItemId = itemId;

        const incoming = filesData.files || [];
        if (append) {
            const seen = new Set((navigationState.loadedFiles || []).map((f) => f.id));
            const unique = incoming.filter((f) => !seen.has(f.id));
            navigationState.loadedFiles = (navigationState.loadedFiles || []).concat(unique);
            appendFilesToView(unique, section, itemName || sectionLabels[section] || 'Item', filesData.pagination);
        } else {
            navigationState.loadedFiles = incoming.slice();
            renderFilesView(incoming, section, itemName || sectionLabels[section] || 'Item', filesData.pagination);
        }
    } catch (error) {
        console.error('Error loading files:', error);
        console.error('Error details:', { section, itemId, itemName, page });
        if (!append) {
            contentView.innerHTML = `<div class="empty-state">${translations.errorLoadingFiles || 'Error loading files'}: ${error.message}</div>`;
        } else if (window.showError) {
            window.showError(error.message || 'Failed to load more files');
        }
    }
}

export async function loadGroupedTitleFiles(groupId, page = 1) {
    const contentView = document.getElementById('unifiedContentView');
    if (!contentView) return;
    contentView.innerHTML = `<div class="content-loading"><i class="bi bi-arrow-repeat"></i><div>${translations.loadingFiles || 'Loading files...'}</div></div>`;

    try {
        const apiUrl = `/api/archives/titles/grouped/${groupId}?page=${page}`;
        const data = await apiGet(apiUrl);
        navigationState.loadedFiles = (data.files || []).slice();
        renderFilesView(data.files || [], 'titles', translations.titles || 'Titles', data.pagination);
    } catch (error) {
        contentView.innerHTML = `<div class="empty-state">${translations.errorLoadingFiles || 'Error loading files'}: ${error.message}</div>`;
    }
}

/**
 * Load files with combined filters (e.g., source + keyword, source + category)
 */
export async function loadItemFilesWithFilters(section, itemId, itemName = null, sourceFilter = null, sideFilter = null, page = 1, options = {}) {
    const contentView = document.getElementById('unifiedContentView');
    if (!contentView) return;

    const append = !!options.append;
    if (!append) {
        contentView.innerHTML = `<div class="content-loading"><i class="bi bi-arrow-repeat"></i><div>${translations.loadingFiles || 'Loading files...'}</div></div>`;
        navigationState.loadedFiles = [];
    }

    try {
        navigationState.currentSourceFilter = sourceFilter;
        navigationState.currentSideFilter = sideFilter;
        const params = buildFileQueryParams(page);
        if (sourceFilter?.source_id) params.source_id = sourceFilter.source_id;
        if (sideFilter?.side_id) params.side_id = sideFilter.side_id;

        const apiUrl = endpoints.itemFilesWithFilters(section, itemId, params);
        console.log('Loading files with filters from:', apiUrl);
        
        const filesData = await apiGet(apiUrl);
        console.log('Files data received:', filesData);
        
        if (!filesData.success) {
            throw new Error(filesData.error || 'Failed to load files');
        }

        const state = { type: 'item', section, itemId, itemName, sourceFilter, sideFilter };
        navigationState.currentFileSection = section;
        navigationState.currentFileItemId = itemId;

        // Update breadcrumb to show the full path
        if (!append && window.fms?.navigation?.breadcrumb?.updateBreadcrumb) {
            const breadcrumbPath = [
                { name: translations.home || 'Home', state: { type: 'root' } }
            ];
            
            if (sourceFilter?.source_id && navigationState.currentSourceItemName) {
                breadcrumbPath.push({
                    name: sectionLabels.sources || 'Sources',
                    state: { type: 'section', section: 'sources' }
                });
                breadcrumbPath.push({
                    name: navigationState.currentSourceItemName,
                    state: { type: 'item', section: 'sources', itemId: sourceFilter.source_id, itemName: navigationState.currentSourceItemName }
                });
            } else if (sideFilter?.side_id && navigationState.currentSourceItemName) {
                breadcrumbPath.push({
                    name: sectionLabels.sides || 'Sides',
                    state: { type: 'section', section: 'sides' }
                });
                breadcrumbPath.push({
                    name: navigationState.currentSourceItemName,
                    state: { type: 'item', section: 'sides', itemId: sideFilter.side_id, itemName: navigationState.currentSourceItemName }
                });
            } else {
                breadcrumbPath.push({
                    name: sectionLabels[section] || section,
                    state: { type: 'section', section: section }
                });
            }
            
            breadcrumbPath.push({
                name: itemName || sectionLabels[section] || 'Item',
                state: state
            });
            
            window.fms.navigation.breadcrumb.updateBreadcrumb(breadcrumbPath);
        }

        if (!append && window.fms?.navigation?.history?.addToHistory) {
            window.fms.navigation.history.addToHistory(state);
        }

        const incoming = filesData.files || [];
        if (append) {
            const seen = new Set((navigationState.loadedFiles || []).map((f) => f.id));
            const unique = incoming.filter((f) => !seen.has(f.id));
            navigationState.loadedFiles = (navigationState.loadedFiles || []).concat(unique);
            appendFilesToView(unique, section, itemName || sectionLabels[section] || 'Item', filesData.pagination);
        } else {
            navigationState.loadedFiles = incoming.slice();
            renderFilesView(incoming, section, itemName || sectionLabels[section] || 'Item', filesData.pagination);
        }
    } catch (error) {
        console.error('Error loading files:', error);
        console.error('Error details:', { section, itemId, itemName, page, sourceFilter, sideFilter });
        if (!append) {
            contentView.innerHTML = `<div class="empty-state">${translations.errorLoadingFiles || 'Error loading files'}: ${error.message}</div>`;
        }
    }
}

// Support pagination controls callbacks
export async function loadFilePage(page) {
    const { currentFileSection, currentFileItemId, currentSourceFilter, currentSideFilter } = navigationState;
    if (!currentFileSection || !currentFileItemId) {
        console.warn('Cannot load file page: missing section or item ID');
        return;
    }
    
    const currentState = navigationState.history?.[navigationState.currentIndex];
    const itemName = currentState?.itemName || null;
    
    if (currentSourceFilter || currentSideFilter) {
        await loadItemFilesWithFilters(currentFileSection, currentFileItemId, itemName, currentSourceFilter, currentSideFilter, page);
    } else {
        await loadItemView(currentFileSection, currentFileItemId, itemName, page);
    }
}

/**
 * Append the next page of files (Show More) without replacing the list.
 */
export async function loadMoreFiles() {
    const pag = navigationState.filePagination || {};
    const shown = (navigationState.loadedFiles || []).length;
    const hasMore = !!pag.has_next || (shown > 0 && shown < (pag.total || 0));
    if (!hasMore) return;
    const nextPage = (pag.currentPage || 1) + 1;
    const { currentFileSection, currentFileItemId, currentSourceFilter, currentSideFilter } = navigationState;
    if (!currentFileSection || !currentFileItemId) return;

    const block = document.querySelector('[data-ut-load-more][data-table-id="fmasFilesContainer"]');
    const button = block?.querySelector('button');
    if (button) button.disabled = true;

    try {
        const currentState = navigationState.history?.[navigationState.currentIndex];
        const itemName = currentState?.itemName || null;
        if (currentSourceFilter || currentSideFilter) {
            await loadItemFilesWithFilters(
                currentFileSection, currentFileItemId, itemName,
                currentSourceFilter, currentSideFilter, nextPage, { append: true }
            );
        } else {
            await loadItemView(currentFileSection, currentFileItemId, itemName, nextPage, { append: true });
        }
    } catch (error) {
        // The shared control shows its own retry message; the next click retries.
        const failed = block?.querySelector('[data-ut-load-error]');
        if (failed) failed.hidden = false;
    } finally {
        if (button) button.disabled = false;
    }
}

/**
 * Apply file-list toolbar filters (type / sort / search) and reload from page 1.
 */
export function applyFileListControls() {
    const typeEl = document.getElementById('fileTypeFilter');
    const sortEl = document.getElementById('fileSortBy');
    const searchEl = document.getElementById('fileSearchInput');
    if (!navigationState.fileListFilters) {
        navigationState.fileListFilters = { fileType: '', sortBy: 'file_date', sortDir: 'desc', search: '' };
    }
    navigationState.fileListFilters.fileType = selectedTypesParam(typeEl);
    navigationState.fileListFilters.search = (searchEl?.value || '').trim();
    // Manual toolbar changes leave browse-lock mode
    navigationState.typeFilterFromBrowse = false;

    const sortVal = sortEl?.value || 'file_date_desc';
    const parts = sortVal.split('_');
    const dir = parts.pop();
    const field = parts.join('_') || 'file_date';
    navigationState.fileListFilters.sortBy = field;
    navigationState.fileListFilters.sortDir = dir === 'asc' ? 'asc' : 'desc';

    navigationState.filePagination.currentPage = 1;
    loadFilePage(1);
}

// Expose for pagination onclicks
if (typeof window !== 'undefined') {
    window.loadFilePage = loadFilePage;
    window.loadMoreFiles = loadMoreFiles;
    window.applyFileListControls = applyFileListControls;
}
