/**
 * Language and direction, applied before first paint to avoid a flash of the wrong direction.
 *
 * Moved out of an inline <script> so the Content-Security-Policy can forbid
 * inline script (RES-CSP-01). Behaviour is unchanged.
 */
// Server-rendered values from #language-init-data.
var LANGUAGE_INIT_DATA = JSON.parse(document.getElementById('language-init-data').textContent || '{}');

// Initialize language and direction immediately to prevent FOUC (Flash of Unstyled Content)
(function() {
    try {
        const htmlElement = document.documentElement;
        const currentLang = htmlElement.getAttribute('lang') || LANGUAGE_INIT_DATA.currentLanguage;
        const isRTL = ['ar', 'fa', 'he', 'ur'].includes(currentLang);

        // Apply direction immediately
        htmlElement.setAttribute('dir', isRTL ? 'rtl' : 'ltr');
        htmlElement.setAttribute('lang', currentLang);

        // Store in sessionStorage for JavaScript modules
        if (typeof sessionStorage !== 'undefined') {
            try {
                sessionStorage.setItem('userLanguage', currentLang);
                sessionStorage.setItem('rtlDirection', isRTL ? 'rtl' : 'ltr');
                sessionStorage.setItem('languageChangeTime', Date.now().toString());
            } catch (e) {
                // Ignore sessionStorage errors
            }
        }
    } catch (e) {
        console.warn('Language initialization error:', e);
    }
})();
