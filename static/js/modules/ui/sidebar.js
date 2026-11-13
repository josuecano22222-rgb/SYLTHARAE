/**
 * Sidebar Management
 * Handles sidebar active state and keyboard navigation
 */

/**
 * Update sidebar active state based on current section
 * @param {string|null} activeSection - Active section name or null for root
 */
export function updateSidebarActiveState(activeSection) {
    const sidebarItems = document.querySelectorAll('.sidebar-item');
    sidebarItems.forEach(item => {
        const section = item.getAttribute('data-section');
        if (section === activeSection) {
            item.classList.add('active');
        } else {
            item.classList.remove('active');
        }
    });
}

/**
 * Update sidebar count for a specific section
 * @param {string} section - Section name (category, keywords, titles, etc.)
 * @param {number} count - New count value
 */
export function updateSidebarCount(section, count) {
    const idMap = {
        category: 'sidebarCategoryCount',
        keywords: 'sidebarKeywordsCount',
        titles: 'sidebarTitlesCount',
        sources: 'sidebarSourcesCount',
        sides: 'sidebarSidesCount',
        hash: 'sidebarHashCount',
        geolocation: 'sidebarGeolocationCount',
        filetypes: 'sidebarFiletypesCount'
    };
    const countElement = document.getElementById(idMap[section]
        || `sidebar${section.charAt(0).toUpperCase() + section.slice(1)}Count`);
    if (countElement) {
        const itemsText = window.appData?.translations?.items || 'items';
        countElement.textContent = `${count.toLocaleString()} ${itemsText}`;
    }
}

/**
 * Update all sidebar counts from stats data
 * @param {Object} stats - Stats object with total counts
 */
export function updateAllSidebarCounts(stats) {
    if (!stats) return;
    
    const sectionMap = {
        category: stats.totalCategories || stats.total_categories,
        keywords: stats.totalKeywords || stats.total_keywords,
        titles: stats.totalTitles || stats.total_titles,
        sources: stats.totalSources || stats.total_sources,
        sides: stats.totalSides || stats.total_sides,
        hash: stats.totalHashes || stats.total_hashes,
        geolocation: stats.totalGeolocation || stats.total_geolocation,
        filetypes: stats.totalFiletypes || stats.total_filetypes
    };
    
    Object.entries(sectionMap).forEach(([section, count]) => {
        if (count !== undefined && count !== null) {
            updateSidebarCount(section, count);
        }
    });
}

/**
 * Setup sidebar keyboard navigation
 */
export function setupSidebarKeyboardNavigation() {
    const sidebar = document.getElementById('fileManagerSidebar');
    if (!sidebar) return;
    // Marker lives on the element: a re-run of the bootstrap (new module
    // instance after a soft-nav swap) must not stack a second keydown handler.
    if (sidebar.dataset.keyboardNavWired) return;
    sidebar.dataset.keyboardNavWired = '1';
    
    sidebar.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
            const section = e.target.getAttribute('data-section');
            if (section) {
                e.preventDefault();
                if (window.fms && window.fms.navigation && window.fms.navigation.navigateToSection) {
                    window.fms.navigation.navigateToSection(section);
                }
            }
        }
    });
}

