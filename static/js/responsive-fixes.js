/**
 * JavaScript fixes for responsive design issues
 * Prevents duplicate pagination and fixes layout problems
 */

(function() {
    'use strict';
    
    /**
     * Remove duplicate pagination containers
     */
    function removeDuplicatePagination() {
        // Find all pagination containers
        const paginationContainers = document.querySelectorAll('.unified-pagination-container');
        
        if (paginationContainers.length > 1) {
            // Keep only the first one, remove the rest
            for (let i = 1; i < paginationContainers.length; i++) {
                paginationContainers[i].remove();
            }
        }
        
        // Also check for old pagination containers
        const oldPaginationContainers = document.querySelectorAll('.pagination-container');
        if (oldPaginationContainers.length > 1) {
            for (let i = 1; i < oldPaginationContainers.length; i++) {
                oldPaginationContainers[i].remove();
            }
        }
    }
    
    /**
     * Fix table responsiveness on small screens
     */
    function fixTableResponsiveness() {
        const tables = document.querySelectorAll('table');
        
        tables.forEach(table => {
            // Add data-label attributes to table cells for mobile view
            if (window.innerWidth <= 767) {
                const headers = table.querySelectorAll('thead th');
                const rows = table.querySelectorAll('tbody tr');
                
                rows.forEach(row => {
                    const cells = row.querySelectorAll('td');
                    cells.forEach((cell, index) => {
                        if (headers[index]) {
                            const label = headers[index].textContent.trim();
                            cell.setAttribute('data-label', label);
                        }
                    });
                });
            }
        });
    }
    
    /**
     * Fix button groups on small screens
     */
    function fixButtonGroups() {
        const buttonGroups = document.querySelectorAll('.btn-group');
        
        buttonGroups.forEach(group => {
            if (window.innerWidth <= 575) {
                group.style.flexDirection = 'column';
                group.style.width = '100%';
                
                const buttons = group.querySelectorAll('.btn');
                buttons.forEach(btn => {
                    btn.style.width = '100%';
                    btn.style.marginBottom = '0.25rem';
                });
            } else {
                group.style.flexDirection = 'row';
                group.style.width = 'auto';
                
                const buttons = group.querySelectorAll('.btn');
                buttons.forEach(btn => {
                    btn.style.width = 'auto';
                    btn.style.marginBottom = '0';
                });
            }
        });
    }
    
    /**
     * Fix filter controls grid on small screens
     */
    function fixFilterControls() {
        const filterGrids = document.querySelectorAll('.filter-controls-grid');
        
        filterGrids.forEach(grid => {
            if (window.innerWidth <= 575) {
                grid.style.gridTemplateColumns = '1fr';
            } else if (window.innerWidth <= 767) {
                grid.style.gridTemplateColumns = 'repeat(2, 1fr)';
            } else if (window.innerWidth <= 991) {
                grid.style.gridTemplateColumns = 'repeat(3, 1fr)';
            } else {
                grid.style.gridTemplateColumns = 'repeat(auto-fit, minmax(200px, 1fr))';
            }
        });
    }
    
    /**
     * Fix action bar on small screens
     */
    function fixActionBar() {
        const actionBars = document.querySelectorAll('.action-bar');
        
        actionBars.forEach(bar => {
            if (window.innerWidth <= 575) {
                bar.style.flexDirection = 'column';
                bar.style.alignItems = 'stretch';
                
                const actionGroups = bar.querySelectorAll('.action-group');
                actionGroups.forEach(group => {
                    group.style.flexDirection = 'column';
                    group.style.width = '100%';
                    
                    const buttons = group.querySelectorAll('.btn-action');
                    buttons.forEach(btn => {
                        btn.style.width = '100%';
                    });
                });
            } else {
                bar.style.flexDirection = 'row';
                bar.style.alignItems = 'center';
            }
        });
    }
    
    /**
     * Fix page header on small screens
     */
    function fixPageHeader() {
        const pageHeaders = document.querySelectorAll('.page-header');
        
        pageHeaders.forEach(header => {
            if (window.innerWidth <= 575) {
                header.style.flexDirection = 'column';
                header.style.alignItems = 'stretch';
                
                const buttonContainer = header.querySelector('.d-flex.gap-2');
                if (buttonContainer) {
                    buttonContainer.style.width = '100%';
                    buttonContainer.style.flexDirection = 'column';
                    
                    const buttons = buttonContainer.querySelectorAll('.btn');
                    buttons.forEach(btn => {
                        btn.style.width = '100%';
                        btn.style.marginBottom = '0.5rem';
                    });
                }
            } else {
                header.style.flexDirection = 'row';
                header.style.alignItems = 'flex-start';
            }
        });
    }
    
    /**
     * Initialize all fixes
     */
    function initResponsiveFixes() {
        removeDuplicatePagination();
        fixTableResponsiveness();
        fixButtonGroups();
        fixFilterControls();
        fixActionBar();
        fixPageHeader();
    }
    
    // Run on DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initResponsiveFixes);
    } else {
        initResponsiveFixes();
    }
    
    // Run on window resize with debounce
    let resizeTimeout;
    window.addEventListener('resize', function() {
        clearTimeout(resizeTimeout);
        resizeTimeout = setTimeout(function() {
            initResponsiveFixes();
        }, 250);
    });
    
    // Also run after a short delay to catch dynamically loaded content
    setTimeout(initResponsiveFixes, 500);
    setTimeout(initResponsiveFixes, 1000);
    
})();

