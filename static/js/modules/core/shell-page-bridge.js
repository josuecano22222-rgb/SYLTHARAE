/**
 * Shared shell preparation on every route (full load or soft-nav).
 * Ensures declarative handlers and FMAS/file globals exist before page init.
 */

import { attachWindowGlobals } from '../../managers/function-manager.js';
import { initDocumentActions } from '../documents/document-actions.js';

/**
 * @param {string} pageType - from universal-initializer detectPageType()
 */
export async function preparePageShell(pageType) {
    attachWindowGlobals();
    initDocumentActions();

    const needsFileModal =
        pageType === 'archives'
        || pageType === 'files-list'
        || pageType === 'file-detail'
        || pageType === 'search-advanced'
        || pageType === 'search-enhanced'
        || pageType === 'search';

    if (needsFileModal && typeof window.showFileDetails !== 'function') {
        const bust = window.__swapReinit ? '?swap=' + window.__swapReinit : '';
        try {
            await import('../../file-management-system.js' + bust);
        } catch (error) {
            console.warn('Shell bridge: file-management-system unavailable', error);
        }
        attachWindowGlobals();
    }

    // applyFilters/resetFilters are published by the page that owns the filter
    // bar it filters: files-list-page.js, charts-dashboard-page.js and the rest
    // assign their own, and the archives page uses the registry's suffixed
    // applyFiltersShared/resetFiltersShared. Wiring them here as well made the
    // winner depend on whether the page published at module scope or in init().
}
