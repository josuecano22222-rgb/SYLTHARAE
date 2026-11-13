/**
 * Language Persistence Manager
 * Ensures language is preserved across all interactions, form submissions, and AJAX requests
 * This is a CRITICAL component that prevents language from reverting to default
 */

class LanguagePersistence {
    constructor() {
        this.currentLanguage = null;
        this.isInitialized = false;
        this.originalFetch = null;
        this.originalFormSubmit = null;
        this.userExplicitlySetLanguage = false; // Track if user explicitly set language
        this.lastSentLanguage = null; // Track what we sent in request
        this.init();
    }

    init() {
        if (this.isInitialized) {
            return;
        }

        // The server renders the page in the language of the latest explicit
        // choice (the user_language cookie). On load that rendered language is
        // authoritative; a stale sessionStorage value must not override it.
        this.syncFromServerRenderedLanguage();

        // Get current language from multiple sources (if not already set from sessionStorage)
        if (!this.currentLanguage) {
            this.detectCurrentLanguage();
        }
        
        // Intercept fetch requests
        this.interceptFetch();
        
        // Intercept form submissions
        this.interceptFormSubmissions();
        
        // Monitor language changes
        this.setupLanguageMonitoring();
        
        // Ensure language on page load
        this.ensureLanguageOnLoad();
        
        // Periodic check to ensure language hasn't reverted
        this.setupPeriodicCheck();
        
        this.isInitialized = true;
        console.log(`✅ Language Persistence Manager initialized with language: ${this.currentLanguage}${this.userExplicitlySetLanguage ? ' (user explicit)' : ''}`);
    }

    /**
     * Adopt the language the server rendered and align sessionStorage with it.
     * Returns false when the page has no valid language attribute.
     */
    syncFromServerRenderedLanguage() {
        const htmlLang = document.documentElement.getAttribute('lang');
        if (!htmlLang || !this.isValidLanguage(htmlLang)) {
            return false;
        }
        this.currentLanguage = htmlLang;
        try {
            if (sessionStorage.getItem('userLanguage') !== htmlLang) {
                sessionStorage.setItem('userLanguage', htmlLang);
                sessionStorage.setItem('languageChangeTime', Date.now().toString());
            }
            if (sessionStorage.getItem('userExplicitLanguage') === 'true') {
                this.userExplicitlySetLanguage = true;
            }
        } catch (e) {
            console.warn('Could not align sessionStorage with the rendered language:', e);
        }
        return true;
    }

    /**
     * Detect current language from multiple sources
     */
    detectCurrentLanguage() {
        // Priority 1: the server-rendered language (authoritative on load)
        if (this.syncFromServerRenderedLanguage()) {
            return;
        }

        // Priority 2: Check HTML lang attribute (server-rendered)
        const htmlElement = document.documentElement;
        const htmlLang = htmlElement.getAttribute('lang');
        if (htmlLang && this.isValidLanguage(htmlLang)) {
            this.currentLanguage = htmlLang;
            // Don't mark as explicit if it came from HTML (server default)
            return;
        }

        // Priority 3: Check dir attribute to infer language
        const dir = htmlElement.getAttribute('dir');
        if (dir === 'rtl') {
            this.currentLanguage = 'ar'; // Default RTL to Arabic
            return;
        }

        // Priority 4: Default
        this.currentLanguage = 'en';
    }

    /**
     * Check if language code is valid
     */
    isValidLanguage(lang) {
        // Canonical supported languages: Arabic, English, Hebrew, Persian
        // (kept in sync with settings/languages.py SUPPORTED_LANGUAGES).
        const validLanguages = ['en', 'ar', 'he', 'fa'];
        return validLanguages.includes(lang);
    }

    /**
     * Intercept all fetch requests to ensure language is preserved
     */
    interceptFetch() {
        // Store original fetch
        this.originalFetch = window.fetch;
        
        // Override fetch
        const self = this;
        window.fetch = function(url, options = {}) {
            // Convert URL to string if it's a URL object
            const urlString = typeof url === 'string' ? url : (url instanceof URL ? url.href : String(url));
            
            // Check if body is FormData - if so, we need to be careful with headers
            const isFormData = options.body instanceof FormData;
            
            // Ensure credentials are included (for session cookies)
            const mergedOptions = {
                ...options,
                credentials: options.credentials || 'same-origin'
            };

            // Handle headers carefully - don't interfere with FormData requests
            if (isFormData) {
                // For FormData, only add language headers if headers object already exists
                // Don't create a new headers object - let browser set Content-Type automatically
                // The browser will automatically set Content-Type with boundary for FormData
                if (mergedOptions.headers) {
                    // Headers already exist - add language headers
                    if (typeof mergedOptions.headers === 'object' && !(mergedOptions.headers instanceof Headers)) {
                        // Plain object - safe to modify
                        if (self.currentLanguage && urlString.includes('/api/')) {
                            mergedOptions.headers['X-Language'] = self.currentLanguage;
                            if (self.userExplicitlySetLanguage) {
                                mergedOptions.headers['X-Language-Explicit'] = 'true';
                            }
                        }
                    } else if (mergedOptions.headers instanceof Headers) {
                        // Headers object - use set() method
                        if (self.currentLanguage && urlString.includes('/api/')) {
                            mergedOptions.headers.set('X-Language', self.currentLanguage);
                            if (self.userExplicitlySetLanguage) {
                                mergedOptions.headers.set('X-Language-Explicit', 'true');
                            }
                        }
                    }
                }
                // If no headers exist for FormData, don't create any - browser will handle Content-Type
            } else {
                // For non-FormData requests, safely create/modify headers
                if (!mergedOptions.headers) {
                    mergedOptions.headers = {};
                }
                
                // Ensure language is in headers for API requests
                // CRITICAL: Always send current language, even if user explicitly set it
                // This ensures server knows what language the client expects
                if (self.currentLanguage && urlString.includes('/api/')) {
                    mergedOptions.headers['X-Language'] = self.currentLanguage;
                    // Also add a flag to indicate if this is user's explicit choice
                    if (self.userExplicitlySetLanguage) {
                        mergedOptions.headers['X-Language-Explicit'] = 'true';
                    }
                }
            }

            // Store what language we're sending and what we currently have
            const sentLanguage = self.currentLanguage;
            const currentLanguageBeforeRequest = self.currentLanguage;
            self.lastSentLanguage = sentLanguage;
            
            // Call original fetch
            return self.originalFetch.call(window, url, mergedOptions)
                .then(response => {
                    // CRITICAL: Only trust response header if user hasn't explicitly set a language
                    // If user explicitly set language, we NEVER update from response headers
                    const responseLang = response.headers.get('X-Language');
                    
                    // CRITICAL: If user has explicitly set language, NEVER update from response headers
                    // Response headers can be wrong due to caching, session issues, etc.
                    if (self.userExplicitlySetLanguage) {
                        // User explicitly set language - IGNORE all response headers
                        // Always trust user's explicit choice over server response
                        if (responseLang && responseLang !== self.currentLanguage) {
                            console.debug(`🚫 Ignoring response language (${responseLang}) - user explicitly set ${self.currentLanguage}`);
                        }
                        // Ensure HTML attributes match user's explicit choice
                        if (self.currentLanguage) {
                            const htmlElement = document.documentElement;
                            const currentHtmlLang = htmlElement.getAttribute('lang');
                            if (currentHtmlLang !== self.currentLanguage) {
                                htmlElement.setAttribute('lang', self.currentLanguage);
                                const isRTL = ['ar', 'fa', 'he', 'ur'].includes(self.currentLanguage);
                                htmlElement.setAttribute('dir', isRTL ? 'rtl' : 'ltr');
                            }
                        }
                    } else {
                        // User hasn't explicitly set language - but DON'T auto-update from server responses
                        // Server responses can be inconsistent due to caching, session issues, etc.
                        // Only update language through explicit user actions (LanguageSwitcher)
                        if (responseLang && responseLang !== self.currentLanguage) {
                            console.debug(`Server response language (${responseLang}) differs from current (${self.currentLanguage}), but not auto-updating (only user actions change language)`);
                        }
                    }
                    
                    return response;
                })
                .catch(error => {
                    // Don't log AbortError - it's expected when requests are cancelled
                    if (error.name !== 'AbortError') {
                        console.error('Fetch error:', error);
                    }
                    throw error;
                });
        };
    }

    /**
     * Intercept form submissions to ensure language is preserved
     */
    interceptFormSubmissions() {
        // Store original submit
        this.originalFormSubmit = HTMLFormElement.prototype.submit;
        
        // Override form submit
        const self = this;
        HTMLFormElement.prototype.submit = function() {
            // Add hidden language field if not present
            if (self.currentLanguage) {
                let langInput = this.querySelector('input[name="language"]');
                if (!langInput) {
                    langInput = document.createElement('input');
                    langInput.type = 'hidden';
                    langInput.name = 'language';
                    langInput.value = self.currentLanguage;
                    this.appendChild(langInput);
                } else {
                    langInput.value = self.currentLanguage;
                }
            }
            
            // Call original submit
            return self.originalFormSubmit.call(this);
        };

        // Also intercept addEventListener('submit')
        document.addEventListener('submit', function(e) {
            const form = e.target;
            if (form && form.tagName === 'FORM' && self.currentLanguage) {
                let langInput = form.querySelector('input[name="language"]');
                if (!langInput) {
                    langInput = document.createElement('input');
                    langInput.type = 'hidden';
                    langInput.name = 'language';
                    langInput.value = self.currentLanguage;
                    form.appendChild(langInput);
                } else {
                    langInput.value = self.currentLanguage;
                }
            }
        }, true); // Use capture phase
    }

    /**
     * Setup monitoring for language changes
     * NOTE: LanguagePersistence is READ-ONLY. It only reflects changes made by LanguageSwitcher.
     */
    setupLanguageMonitoring() {
        // Listen for language change events from LanguageSwitcher (the sole handler)
        document.addEventListener('languageChanged', (e) => {
            if (e.detail && e.detail.language) {
                // Reflect the change made by LanguageSwitcher
                this.updateLanguage(e.detail.language, true, true); // Mark as user explicit
            }
        });

        // REMOVED: systemSettingChanged listener - language changes only go through LanguageSwitcher
        // REMOVED: directionChanged listener - RTL manager should not trigger language changes
        // LanguagePersistence does NOT initiate language changes, only reflects them
    }

    /**
     * Ensure language is set on page load
     */
    ensureLanguageOnLoad() {
        // Run immediately - restore from sessionStorage (user's explicit choice)
        this.restoreLanguage();
        
        // Also run after DOM is ready
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', () => {
                this.restoreLanguage();
                // REMOVED: verifyLanguageFromServer - never auto-update from server
                // Language only changes through user actions (LanguageSwitcher)
            });
        }
        
        // Also run after a short delay to catch any late changes
        setTimeout(() => {
            this.restoreLanguage();
        }, 100);
    }

    /**
     * Verify language from server by checking HTML lang attribute
     * This ensures we're in sync with what the server rendered
     */
    async verifyLanguageFromServer() {
        // REMOVED: Never auto-update language from server
        // Only user actions (LanguageSwitcher) can change language
        // This prevents the language switching loop
        return;
    }

    /**
     * Restore language from storage
     */
    restoreLanguage() {
        try {
            const stored = sessionStorage.getItem('userLanguage');
            const wasExplicit = sessionStorage.getItem('userExplicitLanguage') === 'true';
            
            if (stored && this.isValidLanguage(stored)) {
                if (stored !== this.currentLanguage) {
                    console.log('Restoring language from storage:', stored, wasExplicit ? '(user explicit)' : '');
                    this.updateLanguage(stored, false, wasExplicit);
                    if (wasExplicit) {
                        this.userExplicitlySetLanguage = true;
                    }
                } else if (wasExplicit) {
                    // Language matches, but mark as explicit if it was
                    this.userExplicitlySetLanguage = true;
                }
            }
        } catch (e) {
            console.warn('Could not restore language:', e);
        }
    }

    /**
     * Update current language
     * @param {string} language - Language code
     * @param {boolean} saveToStorage - Whether to save to sessionStorage
     * @param {boolean} isUserExplicit - Whether this is an explicit user action
     * 
     * NOTE: This method is READ-ONLY. It only updates internal state and HTML attributes.
     * Language changes MUST go through LanguageSwitcher, which is the sole handler.
     */
    updateLanguage(language, saveToStorage = true, isUserExplicit = false) {
        if (!this.isValidLanguage(language)) {
            console.warn('Invalid language code:', language);
            return;
        }

        if (language === this.currentLanguage) {
            // Language matches, but ensure explicit flag is set if needed
            if (isUserExplicit) {
                this.userExplicitlySetLanguage = true;
            }
            return; // No change needed
        }

        // CRITICAL: LanguagePersistence is READ-ONLY
        // It only reflects changes made by LanguageSwitcher
        // If user has explicitly set a language, prevent automatic changes
        if (this.userExplicitlySetLanguage && !isUserExplicit) {
            console.warn(`🚫 BLOCKED: Attempted to change language from ${this.currentLanguage} to ${language}, but user explicitly set ${this.currentLanguage}. Ignoring automatic change.`);
            return; // Block the automatic change
        }

        // If user explicitly set language, mark it
        if (isUserExplicit) {
            this.userExplicitlySetLanguage = true;
        }

        this.currentLanguage = language;

        // Save to sessionStorage (read-only tracking)
        if (saveToStorage) {
            try {
                sessionStorage.setItem('userLanguage', language);
                sessionStorage.setItem('languageChangeTime', Date.now().toString());
                if (isUserExplicit) {
                    sessionStorage.setItem('userExplicitLanguage', 'true');
                }
            } catch (e) {
                console.warn('Could not save language to sessionStorage:', e);
            }
        }

        // Update HTML attributes (read-only reflection)
        const htmlElement = document.documentElement;
        htmlElement.setAttribute('lang', language);
        
        const isRTL = ['ar', 'fa', 'he', 'ur'].includes(language);
        htmlElement.setAttribute('dir', isRTL ? 'rtl' : 'ltr');

        console.log(`✅ Language Persistence: Reflected language change to ${language}${isUserExplicit ? ' (user explicit)' : ''}`);
    }

    /**
     * Setup periodic check to ensure language hasn't reverted
     */
    setupPeriodicCheck() {
        // Check every 2 seconds
        setInterval(() => {
            this.checkLanguageConsistency();
        }, 2000);
    }

    /**
     * Check if language is consistent across all sources
     */
    checkLanguageConsistency() {
        const htmlElement = document.documentElement;
        const htmlLang = htmlElement.getAttribute('lang');
        
        // If HTML lang doesn't match current language, restore it (always fix HTML)
        if (htmlLang !== this.currentLanguage && this.currentLanguage) {
            htmlElement.setAttribute('lang', this.currentLanguage);
            
            const isRTL = ['ar', 'fa', 'he', 'ur'].includes(this.currentLanguage);
            htmlElement.setAttribute('dir', isRTL ? 'rtl' : 'ltr');
        }

        // Check sessionStorage - but only update if user hasn't explicitly set language
        // OR if sessionStorage matches what user explicitly set
        try {
            const stored = sessionStorage.getItem('userLanguage');
            const wasExplicit = sessionStorage.getItem('userExplicitLanguage') === 'true';
            
            if (stored && stored !== this.currentLanguage && this.isValidLanguage(stored)) {
                // CRITICAL: If user has explicitly set a language, NEVER change it
                // Only update if user hasn't explicitly set a language
                if (!this.userExplicitlySetLanguage) {
                    // User hasn't explicitly set language - update to match sessionStorage
                    this.updateLanguage(stored, false, wasExplicit);
                } else if (wasExplicit && stored === this.currentLanguage) {
                    // Stored matches current and was explicit - just mark as explicit
                    this.userExplicitlySetLanguage = true;
                } else {
                    // User explicitly set a different language - update sessionStorage to match current
                    // This prevents sessionStorage from overriding user's explicit choice
                    sessionStorage.setItem('userLanguage', this.currentLanguage);
                    sessionStorage.setItem('languageChangeTime', Date.now().toString());
                    sessionStorage.setItem('userExplicitLanguage', 'true');
                }
            } else if (stored === this.currentLanguage && wasExplicit) {
                // Language matches and was explicit - ensure flag is set
                this.userExplicitlySetLanguage = true;
            }
        } catch (e) {
            // Ignore sessionStorage errors
        }
    }

    /**
     * Get current language
     */
    getCurrentLanguage() {
        return this.currentLanguage || 'en';
    }
}

// Initialize immediately (don't wait for DOM)
let languagePersistence = null;

if (typeof window !== 'undefined') {
    // Initialize immediately
    languagePersistence = new LanguagePersistence();
    window.LanguagePersistence = languagePersistence;
    
    // Also initialize after DOM is ready (in case DOM wasn't ready)
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => {
            if (!window.LanguagePersistence) {
                languagePersistence = new LanguagePersistence();
                window.LanguagePersistence = languagePersistence;
            }
        });
    }
}

export default LanguagePersistence;

