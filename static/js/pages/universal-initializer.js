/**
 * Universal Page Initializer
 * Automatically detects page type and initializes appropriate handlers
 * NO INLINE JAVASCRIPT NEEDED - Everything is external!
 */

import { initBasePage } from './base-page-handler.js';
import {
    deactivateActivePage,
    markPageActive,
} from '../modules/core/page-lifecycle.js';
import { preparePageShell } from '../modules/core/shell-page-bridge.js';

// Page type detection patterns
const pageDetectors = [
    // Specific page types (checked first)
    { selector: '[data-page-type]', getType: (el) => el.dataset.pageType },
    { selector: 'body[data-page-type]', getType: (el) => el.dataset.pageType },
    
    // Route-based detection (fallback)
    { selector: 'body', getType: (el) => {
        const path = window.location.pathname;
        const endpoint = el.dataset.currentEndpoint || '';
        
        // Map routes to page types
        if (path.includes('/files') && !path.match(/\/file\/\d+/)) return 'files-list';
        if (path.match(/\/file\/\d+/)) return 'file-detail';
        // Detail routes are the plural resource path with a trailing id
        // (/keywords/18, /words/21, /sources/62, /sides/42). Anchored, so a
        // sub-page such as /keywords/18/documents or /keywords/add keeps its
        // list-page mapping exactly as before.
        if (path.match(/\/keywords\/\d+\/?$/)) return 'keyword-detail';
        if (path.includes('/keywords') && !path.match(/\/keywords\/\d+\/?$/)) return 'keywords-list';
        if (path.match(/\/categories\/\d+\/words/)) return 'category-words';  // Must be before general /words check
        if (path.match(/\/words\/\d+\/?$/)) return 'word-detail';
        if (path.includes('/words') && !path.match(/\/words\/\d+\/?$/)) return 'words-list';
        if (path.match(/\/sources\/\d+\/?$/)) return 'source-detail';
        if (path.includes('/sources') && !path.match(/\/sources\/\d+\/?$/)) return 'sources-list';
        if (path.match(/\/sides\/\d+\/?$/)) return 'side-detail';
        if (path.includes('/sides') && !path.match(/\/sides\/\d+\/?$/)) return 'sides-list';
        if (path === '/search/enhanced') return 'search-enhanced';
        if (path === '/search/advanced') return 'search-advanced';
        if (path === '/search') return 'search';
        if (path === '/' || path === '/dashboard') return 'dashboard';
        if (path.includes('/dashboard/charts')) return 'charts-dashboard';
        if (path.includes('/dashboard/comprehensive')) return 'comprehensive-dashboard';
        if (path.includes('/notifications')) return 'notifications';
        if (path.includes('/import') || path.includes('/export')) return 'import-export';
        if (path.includes('/analysis/batch')) return 'analysis-batch';
        if (path.includes('/email-words')) return 'email-words';
        if (path.includes('/archives')) return 'archives';
        if (path.includes('/analytics/map-viewer')) return 'map-viewer';
        if (path.includes('/analytics/path') || path.includes('/analysis/path')) return 'path-analysis';
        if (document.getElementById('mapViewerPage')) return 'map-viewer';
        if (path.includes('/analysis/classification') || path.includes('/file-classification')) return 'file-classification';
        if (path.includes('/translations')) return 'translation-management';
        if (path === '/signals' || path.startsWith('/signals?')) return 'signals';
        
        return 'default';
    }}
];

function moduleImportBust() {
    if (window.__swapReinit) {
        return '?swap=' + window.__swapReinit;
    }
    const meta = document.querySelector('meta[name="asset-version"]');
    const ver = meta && meta.getAttribute('content');
    if (ver) return '?v=' + encodeURIComponent(ver);
    return '';
}

// Page handler registry — every entry accepts cache-bust on soft-nav.
// These detail pages load their own module from the page template (a plain
// <script type="module">). Importing it here as well would create a second
// module instance (the asset-version query makes a different URL), and each
// instance binds its own DOMContentLoaded init. The handler therefore loads
// nothing and lets the template's module run once.
function templateOwnsModule() {
    return Promise.resolve(null);
}

const pageHandlers = {
    'files-list': (bust) => import('./files-list-page.js' + (bust || '')),
    'file-types': (bust) => import('./file-types-page.js' + (bust || '')),
    'file-detail': (bust) => import('./file-detail-page.js' + (bust || '')),
    'keywords-list': (bust) => import('./keywords-list-page.js' + (bust || '')),
    'keyword-detail': templateOwnsModule,
    'category-words': (bust) => import('./category-words-page.js' + (bust || '')),
    'words-list': (bust) => import('./words-list-page.js' + (bust || '')),
    'word-detail': templateOwnsModule,
    'sources-list': (bust) => import('./sources-list-page.js' + (bust || '')),
    'source-detail': templateOwnsModule,
    'sides-list': (bust) => import('./sides-list-page.js' + (bust || '')),
    'side-detail': templateOwnsModule,
    'search': (bust) => import('./search-page.js' + (bust || '')),
    'search-enhanced': (bust) => import('./search-enhanced-page.js' + (bust || '')),
    'search-advanced': (bust) => import('./search-advanced-page.js' + (bust || '')),
    'dashboard': (bust) => import('./dashboard-page.js' + (bust || '')),
    'charts-dashboard': (bust) => import('./charts-dashboard-page.js' + (bust || '')),
    'comprehensive-dashboard': (bust) => import('./comprehensive-dashboard-page.js' + (bust || '')),
    'notifications': (bust) => import('./notifications-page.js' + (bust || '')),
    'import-export': (bust) => import('./import-export-page.js' + (bust || '')),
    'analysis-batch': (bust) => import('./analysis-batch-page.js' + (bust || '')),
    'email-words': (bust) => import('./email-words-page.js' + (bust || '')),
    'archives': (bust) => import('./archives-page.js' + (bust || '')),
    'path-analysis': (bust) => import('./path-analysis-page.js' + (bust || '')),
    'map-viewer': (bust) => import('./map-viewer-page.js' + (bust || '')),
    'file-classification': (bust) => import('./file-classification-page.js' + (bust || '')),
    'translation-management': (bust) => import('./translation-management-page.js' + (bust || '')),
    'signals': (bust) => import('./signals-page.js' + (bust || '')),
    'default': () => Promise.resolve({ default: () => {} }) // No-op for default
};

/**
 * The /static/js/pages module this initializer imports for a page type, or
 * null when it imports none (default and template-owned pages). Soft
 * navigation skips only this module when it imports the page's own scripts.
 */
export function handlerModulePath(pageType) {
    const handler = pageHandlers[pageType];
    if (!handler || pageType === 'default' || handler === templateOwnsModule) return null;
    return '/static/js/pages/' + pageType + '-page.js';
}

/**
 * Detect page type
 */
export function detectPageType() {
    for (const detector of pageDetectors) {
        const element = document.querySelector(detector.selector);
        if (element) {
            const pageType = detector.getType(element);
            if (pageType && pageType !== 'default') {
                return pageType;
            }
        }
    }
    return 'default';
}

/**
 * Initialize page
 */
export async function initializePage() {
    initBasePage();

    await deactivateActivePage();

    const pageType = detectPageType();

    await preparePageShell(pageType);

    if (pageType === 'default') {
        markPageActive(null);
        return;
    }

    const handler = pageHandlers[pageType];
    if (!handler) {
        console.warn(`No handler found for page type: ${pageType}`);
        markPageActive(pageType);
        return;
    }

    try {
        const bust = moduleImportBust();
        const pageModule = await handler(bust);
        if (pageModule && pageModule.default && typeof pageModule.default === 'function') {
            await pageModule.default();
        } else if (pageModule && typeof pageModule.init === 'function') {
            await pageModule.init();
        }
        markPageActive(pageType);
    } catch (error) {
        console.error(`Error initializing page ${pageType}:`, error);
        markPageActive(pageType);
    }
}

// Auto-initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initializePage);
} else {
    initializePage();
}

// Export for manual initialization if needed
export default { initializePage, detectPageType, handlerModulePath };
