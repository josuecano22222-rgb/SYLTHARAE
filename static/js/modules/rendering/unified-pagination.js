/**
 * Unified Pagination Module
 * Provides consistent pagination rendering across all pages
 */

/**
 * Render unified pagination controls
 * @param {Object} options - Pagination options
 * @param {number} options.currentPage - Current page number (1-based)
 * @param {number} options.totalPages - Total number of pages
 * @param {string} options.containerId - ID of container element
 * @param {Function} options.onPageChange - Callback function when page changes
 * @param {Object} options.urlParams - URL parameters to preserve
 * @param {boolean} options.showInfo - Whether to show pagination info
 * @param {boolean} options.showJump - Whether to show jump to page input
 * @param {string} options.endpoint - Flask endpoint name (for URL building)
 * @param {string} options.baseUrl - Base URL (alternative to endpoint)
 */
export function renderUnifiedPagination(options) {
    const {
        currentPage = 1,
        totalPages = 1,
        containerId = 'paginationContainer',
        onPageChange = null,
        urlParams = {},
        showInfo = true,
        showJump = true,
        endpoint = null,
        baseUrl = null,
        // Rows per page: the table's page size, so the range and the Show More
        // batch agree with the Per Page selector.
        pageSize = 0,
        // Load More: when a table is bound, the table's control is rendered
        // here, beside the numbered pages (the same markup the server renders).
        tableId = null,
        totalCount = null,
        rowsShown = null,
        loadMoreLabel = null
    } = options;

    const container = document.getElementById(containerId);
    if (!container) {
        console.warn(`Pagination container not found: ${containerId}`);
        return;
    }

    // Validate and clamp page numbers
    const totalPagesValid = Math.max(1, Math.ceil(totalPages || 1));
    const pageValid = Math.max(1, Math.min(Math.max(1, currentPage || 1), totalPagesValid));
    
    // Don't show pagination if only one page or no pages
    if (totalPagesValid <= 1) {
        container.innerHTML = '';
        return;
    }

    // Use validated values
    const page = pageValid;
    const totalPagesFinal = totalPagesValid;
    const windowSize = 2;
    const startPage = Math.max(1, page - windowSize);
    const endPage = Math.min(totalPagesFinal, page + windowSize);

    // Check if container is already a unified-pagination-container
    const isAlreadyUnifiedContainer = container.classList.contains('unified-pagination-container');
    
    // If container is not already a unified-pagination-container, wrap in one
    // Otherwise, use the container directly
    let html = '';
    if (!isAlreadyUnifiedContainer) {
        html = '<div class="unified-pagination-container"';
        html += ` data-current-page="${page}" data-total-pages="${totalPagesFinal}"`;
        if (endpoint) html += ` data-endpoint="${endpoint}"`;
        html += '>';
    } else {
        // Update data attributes on existing container
        container.setAttribute('data-current-page', page);
        container.setAttribute('data-total-pages', totalPagesFinal);
        if (endpoint) container.setAttribute('data-endpoint', endpoint);
    }

    // Pagination info
    if (showInfo) {
        html += '<div class="unified-pagination-info">';
        html += '<span class="pagination-text">';
        html += '<i class="bi bi-info-circle me-1"></i>';
        html += `Page ${page} of ${totalPagesFinal}`;
        html += '</span>';
        html += '</div>';
    }

    // Pagination controls
    html += '<div class="unified-pagination-controls">';
    // Show More belongs to the window this page shows: it is offered while rows
    // remain after the window, whatever the page number. Its batch is the page
    // size, and its first row is this page's first row.
    const size = pageSize || rowsShown || 0;
    const firstRow = ((page - 1) * size) + 1;
    const remainingAfterWindow = totalCount == null
        ? null
        : totalCount - (firstRow - 1) - (rowsShown || 0);
    const offerLoadMore = tableId && (remainingAfterWindow == null ? page < totalPagesFinal : remainingAfterWindow > 0);
    if (offerLoadMore) {
        html += loadMoreBlockHtml({
            tableId, page, totalPages: totalPagesFinal,
            total: totalCount, shown: rowsShown, label: loadMoreLabel,
            pageSize: size, firstRow,
        });
    }
    html += '<nav aria-label="Page navigation">';
    html += '<ul class="unified-pagination-list">';

    // First button
    html += '<li class="unified-pagination-item">';
    html += `<a class="unified-pagination-link ${page <= 1 ? 'disabled' : ''}" `;
    html += `href="${page > 1 ? buildPageUrl(1, endpoint, baseUrl, urlParams, onPageChange) : '#'}" `;
    html += `aria-label="First Page" `;
    if (page <= 1) html += 'aria-disabled="true" tabindex="-1"';
    html += '>';
    html += '<i class="bi bi-chevron-double-left"></i>';
    html += '<span class="d-none d-sm-inline ms-1">First</span>';
    html += '</a></li>';

    // Previous button
    html += '<li class="unified-pagination-item">';
    html += `<a class="unified-pagination-link ${page <= 1 ? 'disabled' : ''}" `;
    html += `href="${page > 1 ? buildPageUrl(page - 1, endpoint, baseUrl, urlParams, onPageChange) : '#'}" `;
    html += `aria-label="Previous Page" `;
    if (page <= 1) html += 'aria-disabled="true" tabindex="-1"';
    html += '>';
    html += '<i class="bi bi-chevron-left"></i>';
    html += '<span class="d-none d-sm-inline ms-1">Previous</span>';
    html += '</a></li>';

    // First page and ellipsis
    if (startPage > 1) {
        html += '<li class="unified-pagination-item">';
        html += `<a class="unified-pagination-link" href="${buildPageUrl(1, endpoint, baseUrl, urlParams, onPageChange)}">1</a>`;
        html += '</li>';
        if (startPage > 2) {
            html += '<li class="unified-pagination-item disabled">';
            html += '<span class="unified-pagination-ellipsis">...</span>';
            html += '</li>';
        }
    }

    // Page numbers
    for (let p = startPage; p <= endPage; p++) {
        html += '<li class="unified-pagination-item';
        if (p === page) html += ' active';
        html += '">';
        if (p === page) {
            html += `<span class="unified-pagination-link active" aria-current="page">${p}</span>`;
        } else {
            html += `<a class="unified-pagination-link" href="${buildPageUrl(p, endpoint, baseUrl, urlParams, onPageChange)}">${p}</a>`;
        }
        html += '</li>';
    }

    // Last page and ellipsis
    if (endPage < totalPagesFinal) {
        if (endPage < totalPagesFinal - 1) {
            html += '<li class="unified-pagination-item disabled">';
            html += '<span class="unified-pagination-ellipsis">...</span>';
            html += '</li>';
        }
        html += '<li class="unified-pagination-item">';
        html += `<a class="unified-pagination-link" href="${buildPageUrl(totalPagesFinal, endpoint, baseUrl, urlParams, onPageChange)}">${totalPagesFinal}</a>`;
        html += '</li>';
    }

    // Next button
    html += '<li class="unified-pagination-item">';
    html += `<a class="unified-pagination-link ${page >= totalPagesFinal ? 'disabled' : ''}" `;
    html += `href="${page < totalPagesFinal ? buildPageUrl(page + 1, endpoint, baseUrl, urlParams, onPageChange) : '#'}" `;
    html += `aria-label="Next Page" `;
    if (page >= totalPagesFinal) html += 'aria-disabled="true" tabindex="-1"';
    html += '>';
    html += '<span class="d-none d-sm-inline me-1">Next</span>';
    html += '<i class="bi bi-chevron-right"></i>';
    html += '</a></li>';

    // Last button
    html += '<li class="unified-pagination-item">';
    html += `<a class="unified-pagination-link ${page >= totalPagesFinal ? 'disabled' : ''}" `;
    html += `href="${page < totalPagesFinal ? buildPageUrl(totalPagesFinal, endpoint, baseUrl, urlParams, onPageChange) : '#'}" `;
    html += `aria-label="Last Page" `;
    if (page >= totalPagesFinal) html += 'aria-disabled="true" tabindex="-1"';
    html += '>';
    html += '<span class="d-none d-sm-inline me-1">Last</span>';
    html += '<i class="bi bi-chevron-double-right"></i>';
    html += '</a></li>';

    html += '</ul></nav>';

    // Jump to page
    if (showJump && totalPagesFinal > 5) {
        const jumpId = `jumpToPageInput-${containerId}`;
        html += '<div class="unified-pagination-jump">';
        html += `<label for="${jumpId}" class="visually-hidden">Jump to page</label>`;
        html += `<input type="number" id="${jumpId}" class="form-control form-control-sm unified-pagination-jump-input" `;
        html += `min="1" max="${totalPagesFinal}" value="${page}" placeholder="Page" style="width: 70px;" `;
        html += `data-total-pages="${totalPagesFinal}">`;
        html += '<button type="button" class="btn btn-sm btn-outline-primary unified-pagination-jump-btn" ';
        html += `data-container-id="${containerId}" aria-label="Go to page">`;
        html += '<i class="bi bi-arrow-right"></i>';
        html += '</button>';
        html += '</div>';
    }

    html += '</div>';
    if (!isAlreadyUnifiedContainer) {
        html += '</div>';
    }

    if (isAlreadyUnifiedContainer) {
        // If container is already unified-pagination-container, replace only the inner content
        // Clear existing content first
        container.innerHTML = '';
        // Then add the new content
        container.insertAdjacentHTML('beforeend', html);
        // Update data attributes
        container.setAttribute('data-current-page', page);
        container.setAttribute('data-total-pages', totalPagesFinal);
        if (endpoint) container.setAttribute('data-endpoint', endpoint);
    } else {
        container.innerHTML = html;
    }
    
    // Mark container to prevent duplicate listeners
    if (!container.dataset.paginationInitialized) {
        container.dataset.paginationInitialized = 'true';
    }

    // Attach event listeners after a brief delay to ensure DOM is ready
    setTimeout(() => {
        attachPaginationListeners(container, onPageChange, endpoint, baseUrl, urlParams);
    }, 0);
}

/**
 * Build URL for a specific page
 */
/** "Showing first-last of total", the shape the server renders (components/unified_pagination.html,
 *  macro showing_range). An empty list reads 0-0. */
export function rangeHtml(firstRow, shown, total) {
    const first = shown > 0 ? firstRow : 0;
    const last = shown > 0 ? firstRow + shown - 1 : 0;
    return `Showing <span data-ut-first>${first}</span>&ndash;<span data-ut-last>${last}</span>`
        + ` of <span data-ut-total>${total}</span>`;
}

/** The one Load More block, as the server renders it (components/unified_pagination.html,
 *  macro load_more_control). The table engine (ui/unified-table.js) owns its behaviour. */
export function loadMoreBlockHtml({ tableId, page, totalPages, total, shown, label, pageSize, firstRow }) {
    const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (ch) => (
        { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
    const n = (value) => Math.max(0, parseInt(value, 10) || 0);
    return '<div class="ut-load-more" data-ut-load-more'
        + ` data-table-id="${esc(tableId)}" data-page="${n(page)}"`
        + ` data-total-pages="${n(totalPages)}" data-total="${n(total)}" data-shown="${n(shown)}"`
        + ` data-page-size="${n(pageSize)}" data-first-row="${Math.max(1, n(firstRow))}">`
        + '<button type="button" class="btn btn-outline-primary btn-sm">'
        + '<i class="bi bi-arrow-down-circle me-2" aria-hidden="true"></i>'
        + `${esc(label || 'Show More')}</button>`
        + '<span class="ut-load-more-info" aria-live="polite">'
        + rangeHtml(Math.max(1, n(firstRow)), n(shown), n(total)) + '</span>'
        + '<span class="ut-load-more-error text-danger" data-ut-load-error hidden role="alert">'
        + 'Could not load more. Click to try again.</span>'
        + '</div>';
}

function buildPageUrl(page, endpoint, baseUrl, urlParams, onPageChange) {
    if (onPageChange) {
        // Use callback function
        return `#page-${page}`;
    }

    if (endpoint) {
        // Build URL using endpoint
        const params = new URLSearchParams();
        params.set('page', page);
        Object.keys(urlParams).forEach(key => {
            if (urlParams[key] !== null && urlParams[key] !== undefined && urlParams[key] !== '') {
                params.set(key, urlParams[key]);
            }
        });
        // Preserve current URL params
        const currentParams = new URLSearchParams(window.location.search);
        currentParams.forEach((value, key) => {
            if (key !== 'page' && !(key in urlParams)) {
                params.set(key, value);
            }
        });
        return `?${params.toString()}`;
    }

    if (baseUrl) {
        // Build URL using base URL
        const url = new URL(baseUrl, window.location.origin);
        url.searchParams.set('page', page);
        Object.keys(urlParams).forEach(key => {
            if (urlParams[key] !== null && urlParams[key] !== undefined && urlParams[key] !== '') {
                url.searchParams.set(key, urlParams[key]);
            }
        });
        return url.pathname + url.search;
    }

    // Fallback: use current URL
    const url = new URL(window.location.href);
    url.searchParams.set('page', page);
    Object.keys(urlParams).forEach(key => {
        if (urlParams[key] !== null && urlParams[key] !== undefined && urlParams[key] !== '') {
            url.searchParams.set(key, urlParams[key]);
        }
    });
    return url.pathname + url.search;
}

/**
 * Attach event listeners to pagination
 */
function attachPaginationListeners(container, onPageChange, endpoint, baseUrl, urlParams) {
    // Store the callback in the container's dataset for event delegation
    if (onPageChange && typeof onPageChange === 'function') {
        container._paginationCallback = onPageChange;
    }
    
    // Use event delegation on the container for better reliability
    // Only attach once per container
    if (!container._paginationDelegationAttached) {
        container.addEventListener('click', (e) => {
            // Find the closest pagination link
            const link = e.target.closest('.unified-pagination-link');
            if (!link) return;
            
            e.preventDefault();
            e.stopPropagation();
            
            // Don't process if disabled or active
            if (link.classList.contains('disabled') || link.classList.contains('active')) {
                return;
            }
            
            const href = link.getAttribute('href');
            if (href && href.startsWith('#page-')) {
                const page = parseInt(href.replace('#page-', ''));
                if (!isNaN(page) && container._paginationCallback && typeof container._paginationCallback === 'function') {
                    container._paginationCallback(page);
                }
            } else if (href && href !== '#') {
                // Navigate to URL (for server-side pagination) through the
                // content swap when the shell provides it: paging must not
                // redraw the sidebar / shell, only the main content.
                if (window.swapNavigate) { window.swapNavigate(href); }
                else { window.location.href = href; }
            }
        });
        container._paginationDelegationAttached = true;
    } else {
        // Update the callback if it changed
        if (onPageChange && typeof onPageChange === 'function') {
            container._paginationCallback = onPageChange;
        }
    }
    
    // Direct listeners are not needed since event delegation handles everything
    // The delegation listener persists even when innerHTML is replaced

    // Handle jump to page
    const jumpInput = container.querySelector('.unified-pagination-jump-input');
    const jumpBtn = container.querySelector('.unified-pagination-jump-btn');
    
    if (jumpInput && jumpBtn) {
        const handleJump = () => {
            let targetPage = parseInt(jumpInput.value);
            const totalPages = parseInt(jumpInput.getAttribute('data-total-pages')) || 1;
            
            if (isNaN(targetPage) || targetPage < 1) {
                targetPage = 1;
            } else if (targetPage > totalPages) {
                targetPage = totalPages;
            }
            
            if (onPageChange && typeof onPageChange === 'function') {
                onPageChange(targetPage);
            } else {
                const url = buildPageUrl(targetPage, endpoint, baseUrl, urlParams, onPageChange);
                if (url.startsWith('#')) {
                    // Callback-based, trigger callback
                    if (onPageChange) onPageChange(targetPage);
                } else if (window.swapNavigate) {
                    window.swapNavigate(url);
                } else {
                    window.location.href = url;
                }
            }
        };

        jumpBtn.addEventListener('click', handleJump);
        jumpInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                handleJump();
            }
        });
    }
}

// Make available globally
window.renderUnifiedPagination = renderUnifiedPagination;

