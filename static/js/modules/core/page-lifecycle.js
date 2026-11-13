/**
 * Page lifecycle for soft navigation and full loads.
 *
 * Without teardown, cached ES modules re-run init() against a new DOM while
 * old listeners, popstate hooks, and AbortControllers still target the prior
 * route — the classic "works after refresh" failure mode.
 */

let activePageType = null;
/** @type {Map<string, Array<() => void | Promise<void>>>} */
const teardownByType = new Map();

/**
 * Register a teardown callback for a page type (idempotent per navigation).
 * @param {string} pageType
 * @param {() => void | Promise<void>} fn
 */
export function onPageTeardown(pageType, fn) {
    if (!pageType || typeof fn !== 'function') return;
    if (!teardownByType.has(pageType)) teardownByType.set(pageType, []);
    teardownByType.get(pageType).push(fn);
}

export function getActivePageType() {
    return activePageType;
}

/**
 * Run teardown for the currently active page (sync; await inside handlers if needed).
 */
export async function deactivateActivePage() {
    if (!activePageType) return;
    const type = activePageType;
    const fns = teardownByType.get(type) || [];
    activePageType = null;
    for (const fn of fns) {
        try {
            await fn();
        } catch (error) {
            console.error('Page teardown failed:', type, error);
        }
    }
    try {
        document.dispatchEvent(new CustomEvent('syltharae:page-unmount', {
            detail: { pageType: type, path: window.location.pathname },
        }));
    } catch (_error) { /* CustomEvent unavailable */ }
}

/**
 * Mark a page type active after mount completes.
 * @param {string} pageType
 */
export function markPageActive(pageType) {
    activePageType = pageType || null;
}

if (typeof window !== 'undefined') {
    window.__syltharaePageLifecycle = {
        onPageTeardown,
        deactivateActivePage,
        markPageActive,
        getActivePageType,
    };
}
