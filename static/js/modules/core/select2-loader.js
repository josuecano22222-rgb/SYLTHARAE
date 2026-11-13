/**
 * Load Select2 once jQuery is available.
 *
 * Select2 is a jQuery plugin, and jQuery is loaded with ``defer``; this waits
 * for it, then appends Select2 from the URL on this script's own
 * ``data-select2-src`` attribute (so the static URL stays server-rendered).
 *
 * Moved out of an inline <script> so the Content-Security-Policy can forbid
 * inline script (RES-CSP-01). Behaviour is unchanged.
 */
(function () {
    var loader = document.currentScript;
    var src = loader && loader.getAttribute('data-select2-src');
    if (!src) {
        console.error('select2-loader: missing data-select2-src');
        return;
    }
    function loadSelect2() {
        if (typeof jQuery !== 'undefined') {
            var script = document.createElement('script');
            script.src = src;
            script.onload = function () {
                console.log('Select2 loaded successfully');
            };
            script.onerror = function () {
                console.error('Failed to load Select2');
            };
            document.head.appendChild(script);
        } else {
            setTimeout(loadSelect2, 50);
        }
    }
    loadSelect2();
})();
