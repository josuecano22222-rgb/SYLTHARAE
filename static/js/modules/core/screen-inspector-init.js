/**
 * Screen Inspector start-up (only included when the Inspector is enabled).
 *
 * Moved out of an inline <script> so the Content-Security-Policy can forbid
 * inline script (RES-CSP-01). Behaviour is unchanged.
 */
// Deferred scripts run before DOMContentLoaded, so the runtime is there;
// `init()` is idempotent, which is what lets a page module call it too.
document.addEventListener('DOMContentLoaded', function () {
    if (window.ScreenInspector) {
        window.ScreenInspector.init();
    }
});
