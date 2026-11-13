/**
 * Archives page: measure sticky navigation height for sidebar/toolbar offsets.
 */

function archivesPage() {
    return document.body?.getAttribute('data-page-type') === 'archives';
}

export function syncArchivesChromeLayout() {
    if (!archivesPage()) return;
    const nav = document.querySelector('.page-container > .navigation-bar');
    if (!nav) return;
    const height = Math.ceil(nav.getBoundingClientRect().height);
    document.documentElement.style.setProperty('--archives-nav-height', `${height}px`);
}

let resizeObserver = null;

export function initArchivesChromeLayout() {
    if (!archivesPage()) return;
    syncArchivesChromeLayout();
    const nav = document.querySelector('.page-container > .navigation-bar');
    if (!nav) return;
    if (typeof ResizeObserver !== 'undefined') {
        resizeObserver?.disconnect();
        resizeObserver = new ResizeObserver(() => syncArchivesChromeLayout());
        resizeObserver.observe(nav);
    }
    if (!initArchivesChromeLayout._resizeBound) {
        initArchivesChromeLayout._resizeBound = true;
        window.addEventListener('resize', syncArchivesChromeLayout, { passive: true });
    }
}
