/**
 * Pagination Controls Renderer
 * Extracted from the legacy file-management-system.js
 * Updated to use unified pagination component
 */

import { navigationState } from '../core/state.js';
import { translations } from '../core/config.js';
import { formatFileSize } from '../core/utils.js';
import { renderUnifiedPagination } from './unified-pagination.js';

export function updateNavItemCount(startItem, endItem, total) {
    const navItemCount = document.getElementById('navItemCount');
    const navItemCountText = document.getElementById('navItemCountText');
    
    // Update the text element if it exists, otherwise update the container
    const targetElement = navItemCountText || navItemCount;
    if (!targetElement) return;
    
    // Format the count display accurately
    if (total === 0) {
        targetElement.textContent = '0 / 0';
    } else if (startItem === 0 && endItem === 0) {
        targetElement.textContent = `0 / ${total}`;
    } else {
        targetElement.textContent = `${startItem}-${endItem} / ${total}`;
    }
    
    // Show the count container if it was hidden
    if (navItemCount && navItemCount.style.display === 'none') {
        navItemCount.style.display = '';
    }
}

export function renderSectionPaginationControls(section) {
    const pag = navigationState.sectionPagination;
    
    // Hide pagination if no items or only one page
    if (!pag.total || pag.total === 0 || pag.totalPages <= 1) return '';
    
    // Return a container div that will be used to render pagination after DOM insertion
    const containerId = `section-pagination-${section}`;
    return `<div id="${containerId}" class="section-pagination-container"></div>`;
}

/**
 * Initialize pagination controls after DOM insertion
 * This must be called after the HTML is inserted into the DOM
 */
export function initializeSectionPaginationControls(section) {
    const pag = navigationState.sectionPagination;
    
    // Hide pagination if no items or only one page
    if (!pag.total || pag.total === 0 || pag.totalPages <= 1) return;
    
    const containerId = `section-pagination-${section}`;
    const container = document.getElementById(containerId);
    if (!container) return;
    
    // Render unified pagination directly into the container
    renderUnifiedPagination({
        currentPage: pag.currentPage,
        totalPages: pag.totalPages,
        containerId: containerId,
        onPageChange: (targetPage) => {
            if (typeof window.loadSectionPage === 'function') {
                window.loadSectionPage(section, targetPage);
            }
        },
        urlParams: {},
        showInfo: true,
        showJump: pag.totalPages > 5,
        baseUrl: window.location.pathname
    });
}

export function renderFilePaginationControls() {
    const pag = navigationState.filePagination;
    const startItem = (pag.currentPage - 1) * pag.perPage + 1;
    const endItem = Math.min(pag.currentPage * pag.perPage, pag.total);
    updateNavItemCount(startItem, endItem, pag.total);

    // Hide pagination if there's only one page or no pages
    if (!pag.totalPages || pag.totalPages <= 1) {
        return '';
    }

    // Return a container div that will be used to render pagination after DOM insertion
    const containerId = 'file-pagination-container';
    return `<div id="${containerId}" class="file-pagination-container"></div>`;
}

/**
 * Initialize file pagination controls after DOM insertion
 * This must be called after the HTML is inserted into the DOM
 */
/** The id the file list's Load More control is bound to (one control per list). */
export const FILE_LIST_LOAD_MORE_ID = 'fmasFilesContainer';

export function initializeFilePaginationControls() {
    const pag = navigationState.filePagination;
    const startItem = (pag.currentPage - 1) * pag.perPage + 1;
    const endItem = Math.min(pag.currentPage * pag.perPage, pag.total);
    updateNavItemCount(startItem, endItem, pag.total);

    const containerId = 'file-pagination-container';
    const container = document.getElementById(containerId);
    
    // If no container, pagination wasn't rendered (only one page or no files)
    if (!container) {
        console.debug('File pagination container not found - may have only one page');
        return;
    }

    // Hide pagination if there's only one page or no pages
    if (!pag.totalPages || pag.totalPages <= 1) {
        container.innerHTML = '';
        return;
    }
    
    // Render unified pagination directly into the container
    try {
        // One control: the list's Load More is rendered by this pager and
        // handled by the page's loader (the file list is cards, not a table).
        renderUnifiedPagination({
            currentPage: pag.currentPage,
            totalPages: pag.totalPages,
            containerId: containerId,
            onPageChange: (targetPage) => {
                if (typeof window.loadFilePage === 'function') {
                    window.loadFilePage(targetPage);
                }
            },
            urlParams: {},
            showInfo: true,
            showJump: pag.totalPages > 5,
            baseUrl: window.location.pathname,
            tableId: FILE_LIST_LOAD_MORE_ID,
            totalCount: pag.total || 0,
            rowsShown: (navigationState.loadedFiles || []).length,
        });
        if (window.UnifiedTable && typeof window.UnifiedTable.onLoadMore === 'function') {
            window.UnifiedTable.onLoadMore(FILE_LIST_LOAD_MORE_ID, () => {
                if (typeof window.loadMoreFiles === 'function') window.loadMoreFiles();
            });
        }
    } catch (error) {
        console.error('Error rendering file pagination:', error);
    }
}

