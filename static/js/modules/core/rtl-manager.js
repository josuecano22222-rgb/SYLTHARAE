/**
 * RTL Manager - Handles Right-to-Left (RTL) layout switching
 * Automatically applies RTL styles for RTL languages (Arabic, Persian, Hebrew, Urdu)
 */

class RTLManager {
    constructor() {
        this.currentLanguage = null;
        // List of RTL language codes
        this.rtlLanguages = ['ar', 'fa', 'he', 'ur'];
        this.init();
    }

    init() {
        // Get current language from multiple sources (priority order)
        const htmlElement = document.documentElement;
        let detectedLanguage = null;
        
        // Priority 1: Check sessionStorage (user's recent choice)
        try {
            const storedLanguage = sessionStorage.getItem('userLanguage');
            if (storedLanguage && this.rtlLanguages.includes(storedLanguage) || 
                ['en', 'fr', 'es', 'de', 'it', 'pt', 'ru', 'zh_CN', 'zh_TW', 'ja', 'ko', 'tr', 'hi', 'nl', 'pl', 'el', 'vi', 'th', 'id', 'ms', 'sv', 'no', 'da', 'fi', 'cs', 'sk', 'hu', 'ro', 'bg', 'hr', 'sr', 'uk'].includes(storedLanguage)) {
                detectedLanguage = storedLanguage;
            }
        } catch (e) {
            console.warn('Could not read sessionStorage:', e);
        }
        
        // Priority 2: Check HTML lang attribute
        if (!detectedLanguage) {
            detectedLanguage = htmlElement.getAttribute('lang');
        }
        
        // Priority 3: Check dir attribute to infer language
        if (!detectedLanguage) {
            const currentDir = htmlElement.getAttribute('dir') || 'ltr';
            if (currentDir === 'rtl') {
                // Default to Arabic if RTL but no lang specified
                detectedLanguage = 'ar';
            }
        }
        
        // Priority 4: Default fallback
        if (!detectedLanguage) {
            detectedLanguage = 'en';
        }
        
        this.currentLanguage = detectedLanguage;
        
        // Apply RTL if needed on page load (with error handling)
        try {
            this.applyDirection(this.currentLanguage);
        } catch (error) {
            console.error('Error applying initial direction:', error);
            // Fallback: Apply basic direction
            const isRTL = this.isRTLLanguage(this.currentLanguage);
            htmlElement.setAttribute('dir', isRTL ? 'rtl' : 'ltr');
            htmlElement.setAttribute('lang', this.currentLanguage);
        }
        
        // Listen for language changes
        this.setupLanguageChangeListeners();
        
        // Listen for system setting changes
        document.addEventListener('systemSettingChanged', (e) => {
            if (e.detail && e.detail.key === 'language') {
                this.applyDirection(e.detail.value);
            }
        });
        
        // Listen for language changed events
        document.addEventListener('languageChanged', (e) => {
            if (e.detail && e.detail.language) {
                this.applyDirection(e.detail.language);
            }
        });
    }

    /**
     * Check if a language is RTL
     * @param {string} language - Language code
     * @returns {boolean}
     */
    isRTLLanguage(language) {
        return this.rtlLanguages.includes(language);
    }

    /**
     * Apply RTL or LTR direction based on language
     * @param {string} language - Language code (e.g., 'ar', 'en', 'fa', 'he')
     */
    applyDirection(language) {
        if (!language || typeof language !== 'string') {
            console.warn('Invalid language code for RTL Manager:', language);
            return;
        }

        try {
            const htmlElement = document.documentElement;
            const bodyElement = document.body;
            
            // Determine direction based on language
            const isRTL = this.isRTLLanguage(language);
            const direction = isRTL ? 'rtl' : 'ltr';
            
            // Update HTML attributes (critical for proper rendering)
            htmlElement.setAttribute('dir', direction);
            htmlElement.setAttribute('lang', language);
            
            // Update body class for additional styling if needed
            if (bodyElement) {
                if (isRTL) {
                    bodyElement.classList.add('rtl-layout');
                    bodyElement.classList.remove('ltr-layout');
                } else {
                    bodyElement.classList.add('ltr-layout');
                    bodyElement.classList.remove('rtl-layout');
                }
            }
            
            // Store current language
            this.currentLanguage = language;
            
            // Store in sessionStorage for persistence
            try {
                sessionStorage.setItem('userLanguage', language);
                sessionStorage.setItem('rtlDirection', direction);
            } catch (e) {
                console.warn('Could not store language in sessionStorage:', e);
            }
            
            // Dispatch event for other components
            try {
                document.dispatchEvent(new CustomEvent('directionChanged', {
                    detail: { language, direction, isRTL }
                }));
            } catch (e) {
                console.warn('Could not dispatch directionChanged event:', e);
            }
            
            console.log(`✅ RTL Manager: Applied ${direction.toUpperCase()} layout for language: ${language}`);
        } catch (error) {
            console.error('Error in applyDirection:', error);
            // Fallback: Apply basic direction even if error occurs
            try {
                const htmlElement = document.documentElement;
                const isRTL = this.isRTLLanguage(language);
                htmlElement.setAttribute('dir', isRTL ? 'rtl' : 'ltr');
                htmlElement.setAttribute('lang', language);
            } catch (fallbackError) {
                console.error('Fallback direction application also failed:', fallbackError);
            }
        }
    }

    /**
     * Setup listeners for language change events
     */
    setupLanguageChangeListeners() {
        // Listen for language dropdown in sidebar
        const languageSelect = document.getElementById('sidebarLanguageSelect');
        if (languageSelect) {
            languageSelect.addEventListener('change', (e) => {
                const langCode = e.target.value;
                if (langCode) {
                    // Apply direction immediately (before page reload)
                    this.applyDirection(langCode);
                }
            });
        }
        
        // Also listen for language links (if any still exist for backward compatibility)
        const languageLinks = document.querySelectorAll('.language-switcher a[href*="/set_language/"]');
        languageLinks.forEach(link => {
            link.addEventListener('click', (e) => {
                // Extract language code from URL
                const href = link.getAttribute('href');
                const match = href.match(/\/set_language\/([^\/]+)/);
                if (match) {
                    const langCode = match[1];
                    // Apply direction immediately (before page reload)
                    this.applyDirection(langCode);
                }
            });
        });
    }

    /**
     * Get current direction
     * @returns {string} 'rtl' or 'ltr'
     */
    getCurrentDirection() {
        return document.documentElement.getAttribute('dir') || 'ltr';
    }

    /**
     * Check if current layout is RTL
     * @returns {boolean}
     */
    isRTL() {
        return this.getCurrentDirection() === 'rtl';
    }
}

// Initialize RTL Manager immediately (don't wait for DOMContentLoaded)
// This ensures direction is applied as early as possible
let rtlManager = null;

// Initialize immediately if DOM is already loaded, otherwise wait for DOMContentLoaded
function initializeRTLManager() {
    if (!rtlManager) {
        try {
            rtlManager = new RTLManager();
            window.RTLManager = rtlManager;
            console.log('✅ RTL Manager initialized');
        } catch (error) {
            console.error('❌ Failed to initialize RTL Manager:', error);
            // Fallback: Apply basic direction
            try {
                const htmlElement = document.documentElement;
                const storedLang = sessionStorage?.getItem('userLanguage') || htmlElement.getAttribute('lang') || 'en';
                const isRTL = ['ar', 'fa', 'he', 'ur'].includes(storedLang);
                htmlElement.setAttribute('dir', isRTL ? 'rtl' : 'ltr');
                htmlElement.setAttribute('lang', storedLang);
            } catch (fallbackError) {
                console.error('Fallback direction application failed:', fallbackError);
            }
        }
    }
}

// Try to initialize immediately
if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
        // Wait for DOMContentLoaded
        document.addEventListener('DOMContentLoaded', initializeRTLManager);
    } else {
        // DOM is already loaded, initialize immediately
        initializeRTLManager();
    }
    
    // Also try after a short delay to catch any edge cases
    setTimeout(initializeRTLManager, 100);
}

export default RTLManager;

