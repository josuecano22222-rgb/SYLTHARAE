/**
 * Global custom CSS from Settings, applied to every page.
 *
 * Moved out of an inline <script> so the Content-Security-Policy can forbid
 * inline script (RES-CSP-01). Behaviour is unchanged.
 */
(function() {
    try {
        var el = document.getElementById('global-custom-css');
        var dataEl = document.getElementById('global-custom-css-data');
        if (!el || !dataEl) return;
        var parsed = JSON.parse(dataEl.textContent || '{}');
        var css = (parsed && parsed.custom_css) ? String(parsed.custom_css) : '';
        // Avoid style tag breakouts if someone pastes HTML
        css = css.replace(/<\/style>/gi, '');
        el.textContent = css;
    } catch (e) {
        // no-op
    }
})();
