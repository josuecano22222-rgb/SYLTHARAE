/**
 * Archives / File Management Analysis System Page Handler
 * Handles the main archives page with SYLTHARAE system
 */

import { getPageData } from './data-helper.js';
import { initArchivesChromeLayout } from '../modules/ui/archives-chrome-layout.js';

export default async function initArchivesPage() {
    console.log('Archives page: Initializing...');
    
    // Get page data from JSON script tag
    const data = getPageData('page-data');
    
    // Fallback to window.appData for backward compatibility
    const appData = data || window.appData || {};
    if (appData && typeof appData === 'object') {
        window.appData = Object.assign({}, window.appData || {}, appData);
    }
    console.log('Archives page: Page data loaded', appData);
    
    // Load Select2 dynamically if needed
    loadSelect2();
    
    // Initialize any page-specific functionality
    initializePageFeatures();
    
    // Soft-nav loads this handler via universal-initializer and skips other
    // page <script type="module"> tags (file-management-system.js). Without
    // that module, window.fms / navigateToSection stay undefined and every
    // explorer card click is a no-op. Import it (cache-busted on swap) so
    // globals exist, then ensure navigation binds the fresh content view.
    await ensureFileManagementSystem();
    resetArchivesTransientState();
    ensureNavigationInitialized();
}

/**
 * Soft-nav reuses cached state modules. Wipe leftover search/filters so a
 * prior query cannot empty every section on the next Archives visit.
 */
function resetArchivesTransientState() {
    const ns = window.fms?.core?.state?.navigationState;
    if (ns) {
        ns.searchQuery = '';
        ns.activeFilters = {};
        ns.sectionPagination.currentPage = 1;
        ns.filePagination.currentPage = 1;
        ns.currentSection = null;
        ns.currentFileSection = null;
        ns.currentFileItemId = null;
        ns.loadedFiles = [];
        ns.fileListFilters = { fileType: '', sortBy: 'file_date', sortDir: 'desc', search: '' };
    }
    const searchInput = document.getElementById('globalSearch');
    if (searchInput) searchInput.value = '';
}

/**
 * Load the FMAS entry module so window.fms and navigate* globals exist.
 */
async function ensureFileManagementSystem() {
    const bust = window.__swapReinit ? '?swap=' + window.__swapReinit : '';
    try {
        await import('../file-management-system.js' + bust);
    } catch (error) {
        console.error('Archives page: failed to load file-management-system', error);
    }
}

/**
 * Load Select2 library dynamically
 */
function loadSelect2() {
    if (typeof jQuery !== 'undefined' && typeof jQuery.fn.select2 === 'undefined') {
        const script = document.createElement('script');
        script.src = document.querySelector('link[href*="select2.min.css"]')?.href.replace('css/select2.min.css', 'js/select2.min.js') || '/static/dist/js/select2.min.js';
        script.onerror = function() {
            console.error('Failed to load Select2 library');
        };
        document.head.appendChild(script);
    } else if (typeof jQuery === 'undefined') {
        // jQuery not ready yet, try again
        setTimeout(loadSelect2, 50);
    }
}

/**
 * Initialize page-specific features
 */
function initializePageFeatures() {
    console.log('Initializing archives page features...');
    initArchivesChromeLayout();
}

/**
 * Ensure navigation is initialized against the current content view.
 * Idempotent: initNavigation marks #unifiedContentView so a prior FMS
 * self-init (or this call) cannot double-fetch the root.
 */
function ensureNavigationInitialized() {
    import('../modules/navigation/navigator.js').then((nav) => {
        if (nav && typeof nav.initNavigation === 'function') {
            nav.initNavigation();
        }
    }).catch((error) => {
        console.error('Archives page: navigation init failed', error);
    });
}

