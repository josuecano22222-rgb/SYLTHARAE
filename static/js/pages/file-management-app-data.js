/**
 * File management: expose #page-data as window.appData for older modules.
 *
 * Moved out of an inline <script> so the Content-Security-Policy can forbid
 * inline script (RES-CSP-01). Behaviour is unchanged.
 */
// Load page data and set window.appData for backward compatibility
(function() {
    const pageDataEl = document.getElementById('page-data');
    if (pageDataEl) {
        try {
            window.appData = JSON.parse(pageDataEl.textContent);
            if (window.appData?.translations) {
                window.translations = window.translations || {};
                Object.assign(window.translations, window.appData.translations);
            }
        } catch (e) {
            console.error('Error parsing page data:', e);
            window.appData = {};
        }
    }
})();
