/**
 * Language Switcher - Seamless language switching with instant UI updates
 * Handles language changes via AJAX for smooth, fast transitions
 */

class LanguageSwitcher {
    constructor() {
        this.isChanging = false;
        this.init();
    }

    init() {
        // Setup dropdown handler
        this.setupDropdownHandler();
        
        // Setup top bar quick language menu (event delegation)
        document.addEventListener('click', (e) => {
            const item = e.target.closest('[data-language-switch]');
            if (item && !this.isChanging) {
                e.preventDefault();
                this.changeLanguage(item.getAttribute('data-language-switch'));
            }
        });
        
        // Listen for system setting changes
        document.addEventListener('systemSettingChanged', (e) => {
            if (e.detail.key === 'language') {
                this.handleLanguageChange(e.detail.value, false);
            }
        });
    }

    /**
     * Setup dropdown change handler
     */
    setupDropdownHandler() {
        const languageSelect = document.getElementById('sidebarLanguageSelect');
        if (languageSelect) {
            languageSelect.addEventListener('change', (e) => {
                const selectedLanguage = e.target.value;
                if (selectedLanguage && !this.isChanging) {
                    this.changeLanguage(selectedLanguage);
                }
            });
        }
    }

    /**
     * Translate with graceful fallback to the English source string.
     * @param {string} key
     */
    tr(key) {
        return (typeof window.t === 'function') ? window.t(key) : key;
    }

    /**
     * Change language seamlessly with retry logic and comprehensive error handling
     * @param {string} languageCode - Language code to switch to
     * @param {number} retryCount - Internal retry counter
     */
    async changeLanguage(languageCode, retryCount = 0) {
        if (this.isChanging) {
            console.warn('Language change already in progress, ignoring duplicate request');
            return;
        }

        // Validate language code
        if (!languageCode || typeof languageCode !== 'string') {
            console.error('Invalid language code:', languageCode);
            this.showError(this.tr('Invalid language code'));
            return;
        }

        this.isChanging = true;
        const languageSelect = document.getElementById('sidebarLanguageSelect');
        // The shared shell no longer renders the legacy sidebar selector, so
        // retain the document's current language as the rollback source.
        const originalValue = (languageSelect?.value ||
            document.documentElement.getAttribute('lang') || '').trim();
        const maxRetries = 2;

        try {
            // Show loading state
            this.showLoadingState(languageSelect);

            // Step 1: Apply RTL/LTR immediately for instant visual feedback
            if (window.RTLManager) {
                try {
                    window.RTLManager.applyDirection(languageCode);
                } catch (rtlError) {
                    console.warn('RTL Manager error (non-critical):', rtlError);
                    // Continue even if RTL fails
                }
            } else {
                // Fallback: Apply direction directly if RTLManager not available
                const htmlElement = document.documentElement;
                const isRTL = ['ar', 'fa', 'he', 'ur'].includes(languageCode);
                htmlElement.setAttribute('dir', isRTL ? 'rtl' : 'ltr');
                htmlElement.setAttribute('lang', languageCode);
            }

            // Step 2: Update language via API with retry logic. A stale CSRF
            // token (session rotated by re-login/logout in any tab) is the
            // most common cause of "Failed to change language" - recover by
            // refreshing the token from the server before each retry.
            let response;
            let data;
            let apiSuccess = false;
            let userMessage = null;

            for (let attempt = 0; attempt <= maxRetries; attempt++) {
                try {
                    const csrfToken = (window.CSRF && typeof window.CSRF.getToken === 'function')
                        ? window.CSRF.getToken()
                        : (document.querySelector('meta[name="csrf-token"]')?.content || '');

                    response = await fetch(`/api/settings/system/language`, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-Requested-With': 'XMLHttpRequest',
                            'Accept': 'application/json',
                            'X-CSRFToken': csrfToken
                        },
                        credentials: 'same-origin',
                        body: JSON.stringify({ value: languageCode }),
                        cache: 'no-store' // Prevent caching
                    });

                    // Tolerate non-JSON error bodies (proxies, rate limit pages).
                    let parsed = null;
                    try { parsed = await response.json(); } catch (_) { parsed = null; }
                    data = parsed || {};

                    if (!response.ok) {
                        const errText = (data && data.error) ? String(data.error) : '';
                        if (response.status === 401) {
                            // Session gone - retrying cannot help. Prefer the
                            // translated message over raw server text.
                            console.warn('Language change unauthenticated:', errText);
                            throw Object.assign(new Error(this.tr('Authentication required. Please log in.')), { kind: 'auth' });
                        }
                        if (response.status === 403) {
                            console.warn('Language change forbidden:', errText);
                            throw Object.assign(new Error(this.tr('You do not have permission to change this setting.')), { kind: 'forbidden' });
                        }
                        const staleToken = response.status === 400 &&
                            /csrf|token/i.test(errText);
                        if (staleToken) {
                            throw Object.assign(new Error(errText || 'CSRF token is missing or invalid'), { kind: 'csrf' });
                        }
                        if (response.status === 429) {
                            throw Object.assign(new Error(this.tr('Too many requests. Please wait a moment and try again.')), { kind: 'rate' });
                        }
                        throw Object.assign(new Error(
                            errText || `HTTP error! status: ${response.status}`
                        ), { kind: 'http' });
                    }

                    if (data.success) {
                        apiSuccess = true;
                        break; // Success, exit retry loop
                    } else {
                        throw Object.assign(new Error(data.error || this.tr('Failed to change language')), { kind: 'server' });
                    }
                } catch (apiError) {
                    console.warn(`Language change API attempt ${attempt + 1} failed:`, apiError);

                    // Permanent failures: stop retrying immediately.
                    if (apiError && (apiError.kind === 'auth' || apiError.kind === 'forbidden')) {
                        userMessage = apiError.message;
                        throw apiError;
                    }

                    // Stale CSRF token: mint a fresh one from the server and
                    // retry with it (the previously used token cannot recover).
                    if (apiError && apiError.kind === 'csrf') {
                        if (attempt < maxRetries) {
                            try {
                                if (window.CSRF && typeof window.CSRF.refreshToken === 'function') {
                                    await window.CSRF.refreshToken();
                                }
                            } catch (refreshError) {
                                console.warn('CSRF token refresh failed:', refreshError);
                            }
                            continue;
                        }
                        userMessage = this.tr('Security token expired. Please refresh the page and try again.');
                        throw apiError;
                    }

                    if (attempt < maxRetries) {
                        // Wait before retry (exponential backoff), then refresh
                        // the token anyway so each retry has its best chance.
                        await new Promise(resolve => setTimeout(resolve, 300 * (attempt + 1)));
                        try {
                            if (window.CSRF && typeof window.CSRF.refreshToken === 'function') {
                                await window.CSRF.refreshToken();
                            }
                        } catch (refreshError) { /* non-critical */ }
                        continue;
                    } else {
                        throw apiError; // All retries failed
                    }
                }
            }

            if (!apiSuccess) {
                throw new Error(userMessage || this.tr('Failed to change language'));
            }

            // Step 3: Update dropdown to reflect change
            if (languageSelect) {
                languageSelect.value = languageCode;
            }

            // Step 4: Store in sessionStorage as backup and update Language Persistence
            try {
                sessionStorage.setItem('userLanguage', languageCode);
                sessionStorage.setItem('languageChangeTime', Date.now().toString());
                sessionStorage.setItem('userExplicitLanguage', 'true'); // Mark as user explicit

                // Persist as the explicit user_language cookie so the choice
                // also applies to the login screen and anonymous pages
                // (same cookie the login-screen switcher uses).
                document.cookie = 'user_language=' + encodeURIComponent(languageCode) +
                    '; path=/; max-age=31536000; SameSite=Lax';

                // Update Language Persistence Manager if available (mark as user explicit)
                if (window.LanguagePersistence) {
                    window.LanguagePersistence.updateLanguage(languageCode, false, true);
                }
            } catch (e) {
                console.warn('Could not store language in sessionStorage:', e);
            }

            // Step 5: Reload translations if available
            if (window.translations && typeof window.reloadTranslations === 'function') {
                try {
                    await window.reloadTranslations(languageCode);
                } catch (e) {
                    console.warn('Could not reload translations dynamically:', e);
                    // Non-critical, continue with page reload
                }
            }

            // Step 6: Dispatch event for other components
            document.dispatchEvent(new CustomEvent('languageChanged', {
                detail: { language: languageCode, success: true }
            }));

            // Step 6.5: Ensure Language Persistence Manager knows this is explicit
            // This prevents any response headers from overriding the user's choice
            if (window.LanguagePersistence) {
                // Force update with explicit flag
                window.LanguagePersistence.userExplicitlySetLanguage = true;
                window.LanguagePersistence.currentLanguage = languageCode;
            }

            // Step 7: Smooth page reload with fade transition
            this.reloadPageSmoothly();
        } catch (error) {
            console.error('Error changing language:', error);
            
            // Revert dropdown if error
            if (languageSelect && originalValue) {
                languageSelect.value = originalValue;
            }

            // Revert RTL/LTR if error
            if (window.RTLManager && originalValue) {
                try {
                    window.RTLManager.applyDirection(originalValue);
                } catch (rtlError) {
                    console.warn('Could not revert RTL direction:', rtlError);
                }
            } else if (originalValue) {
                // Fallback revert
                const htmlElement = document.documentElement;
                const isRTL = ['ar', 'fa', 'he', 'ur'].includes(originalValue);
                htmlElement.setAttribute('dir', isRTL ? 'rtl' : 'ltr');
                htmlElement.setAttribute('lang', originalValue);
            }

            // Show error message. Stale-token failures map to a clear,
            // translated security message; other failures prefer the
            // server/classifier message and fall back to a translated generic.
            const fallbackMsg = this.tr(error.kind === 'csrf'
                ? 'Security token expired. Please refresh the page and try again.'
                : 'Failed to change language. Please try again.');
            const shownMsg = (error.kind === 'csrf')
                ? fallbackMsg
                : (error.message && error.message.indexOf('HTTP error!') !== 0 ? error.message : fallbackMsg);
            this.showError(shownMsg || fallbackMsg);
            
            this.isChanging = false;
        }
    }

    /**
     * Handle language change (called from settings page or other sources)
     * @param {string} languageCode - Language code
     * @param {boolean} showLoading - Whether to show loading state
     */
    async handleLanguageChange(languageCode, showLoading = true) {
        if (this.isChanging) {
            return;
        }

        this.isChanging = true;

        try {
            const languageSelect = document.getElementById('sidebarLanguageSelect');
            
            if (showLoading) {
                this.showLoadingState(languageSelect);
            }

            // Apply RTL/LTR immediately
            if (window.RTLManager) {
                window.RTLManager.applyDirection(languageCode);
            }

            // Update dropdown if exists
            if (languageSelect) {
                languageSelect.value = languageCode;
            }

            // Reload translations if available
            if (window.translations && typeof window.reloadTranslations === 'function') {
                try {
                    await window.reloadTranslations(languageCode);
                } catch (e) {
                    console.warn('Could not reload translations dynamically:', e);
                }
            }

            // Smooth reload
            this.reloadPageSmoothly();
        } catch (error) {
            console.error('Error handling language change:', error);
            this.isChanging = false;
        }
    }

    /**
     * Show loading state on dropdown
     * @param {HTMLElement} selectElement - Select element
     */
    showLoadingState(selectElement) {
        if (selectElement) {
            selectElement.disabled = true;
            selectElement.style.opacity = '0.6';
            selectElement.style.cursor = 'wait';

            // Retain support for pages that still expose the native select.
            const container = selectElement.closest('.language-switcher');
            if (container && !container.querySelector('.language-loading')) {
                const spinner = document.createElement('div');
                spinner.className = 'language-loading';
                spinner.innerHTML = '<i class="bi bi-arrow-clockwise spin" aria-hidden="true"></i>';
                spinner.style.cssText = 'position: absolute; inset-inline-end: 0.7rem; top: 50%; transform: translateY(-50%); pointer-events: none; color: inherit;';
                selectElement.style.position = 'relative';
                container.style.position = 'relative';
                container.appendChild(spinner);
            }
        }

        // The shared shell now has one top-bar language control. Reflect the
        // in-flight change there too rather than silently doing nothing when
        // the legacy sidebar select is absent.
        const topbarButton = document.getElementById('topbarLangBtn');
        if (topbarButton) {
            topbarButton.setAttribute('aria-busy', 'true');
            const icon = topbarButton.querySelector('i');
            if (icon) {
                icon.classList.remove('bi-translate');
                icon.classList.add('bi-arrow-repeat', 'spin');
            }
        }
        document.querySelectorAll('[data-language-switch]').forEach((item) => {
            item.disabled = true;
            item.setAttribute('aria-disabled', 'true');
        });
    }

    /**
     * Remove loading state
     */
    removeLoadingState() {
        const languageSelect = document.getElementById('sidebarLanguageSelect');
        if (languageSelect) {
            languageSelect.disabled = false;
            languageSelect.style.opacity = '1';
            languageSelect.style.cursor = 'pointer';
        }

        const loadingSpinner = document.querySelector('.language-loading');
        if (loadingSpinner) loadingSpinner.remove();

        const topbarButton = document.getElementById('topbarLangBtn');
        if (topbarButton) {
            topbarButton.removeAttribute('aria-busy');
            const icon = topbarButton.querySelector('i');
            if (icon) {
                icon.classList.remove('bi-arrow-repeat', 'spin');
                icon.classList.add('bi-translate');
            }
        }
        document.querySelectorAll('[data-language-switch]').forEach((item) => {
            item.disabled = false;
            item.removeAttribute('aria-disabled');
        });
    }

    /**
     * Reload page smoothly with fade transition
     */
    reloadPageSmoothly() {
        // Add fade-out class to body
        document.body.style.transition = 'opacity 0.2s ease-out';
        document.body.style.opacity = '0.7';

        // Small delay for smooth transition, then reload
        setTimeout(() => {
            window.location.reload();
        }, 150);
    }
    
    /**
     * Static method to reload page (can be called from outside)
     */
    static reloadPageSmoothly() {
        // Add fade-out class to body
        document.body.style.transition = 'opacity 0.2s ease-out';
        document.body.style.opacity = '0.7';

        // Small delay for smooth transition, then reload
        setTimeout(() => {
            window.location.reload();
        }, 150);
    }

    /**
     * Show error message
     * @param {string} message - Error message
     */
    showError(message) {
        this.removeLoadingState();

        // Use notification system if available
        if (window.notifications && typeof window.notifications.error === 'function') {
            window.notifications.error(message, { duration: 3000 });
        } else if (window.MessageSystem && typeof window.MessageSystem.show === 'function') {
            window.MessageSystem.show(message, 'error');
        } else {
            // Fallback to alert
            alert(message);
        }
    }
}

// Initialize Language Switcher
let languageSwitcher = null;

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        languageSwitcher = new LanguageSwitcher();
        window.LanguageSwitcher = languageSwitcher;
    });
} else {
    languageSwitcher = new LanguageSwitcher();
    window.LanguageSwitcher = languageSwitcher;
}

export default LanguageSwitcher;

