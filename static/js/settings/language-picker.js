/**
 * Settings: visual system-language picker.
 *
 * Moved out of an inline <script> so the Content-Security-Policy can forbid
 * inline script (RES-CSP-01). Behaviour is unchanged.
 */
/**
 * Apply a system language selection from the visual language picker.
 * Updates the picker state instantly and delegates the actual switch to
 * the LanguageSwitcher (persists system-wide, then reloads).
 */
window.selectSystemLanguage = function (code) {
    // Instant visual feedback (page reload finalizes the state)
    document.querySelectorAll('[data-language-option]').forEach(function (btn) {
        var isActive = btn.getAttribute('data-language-option') === code;
        btn.classList.toggle('active', isActive);
        btn.setAttribute('aria-checked', isActive ? 'true' : 'false');
    });
    var hiddenSelect = document.getElementById('systemLanguageSelect');
    if (hiddenSelect) { hiddenSelect.value = code; }

    if (window.LanguageSwitcher && typeof window.LanguageSwitcher.changeLanguage === 'function') {
        window.LanguageSwitcher.changeLanguage(code);
    } else if (window.toggleSystemSetting) {
        // Fallback: stage the change for the standard Save flow
        window.toggleSystemSetting('language', code);
    }
};
