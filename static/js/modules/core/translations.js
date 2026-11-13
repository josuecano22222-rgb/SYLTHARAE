/**
 * Translation Helper Module
 * Integrates with Flask-Babel translations for client-side message translation
 */

class TranslationHelper {
    constructor() {
        this.translations = window.appTranslations || window.translations || {};
        this.currentLocale = window.currentLocale || 'en';
    }
    
    /**
     * Translate a message key or return the message if no translation found
     * @param {string} key - Translation key or message text
     * @param {object} params - Parameters for placeholder replacement
     * @returns {string} Translated message
     */
    translate(key, params = {}) {
        // If key is already translated (not a key), return as-is
        if (!key || typeof key !== 'string') {
            return key || '';
        }
        
        // Try to get translation from dictionary
        let translated = this.translations[key];
        
        // If not found, try with common prefixes
        if (!translated) {
            const prefixes = ['msg_', 'error_', 'success_', 'warning_', 'info_', 'notification_'];
            for (const prefix of prefixes) {
                const prefixedKey = prefix + key;
                if (this.translations[prefixedKey]) {
                    translated = this.translations[prefixedKey];
                    break;
                }
            }
        }
        
        // If still not found, use the key as fallback (or try direct lookup)
        if (!translated) {
            // Check if key contains spaces (likely a direct message)
            if (key.includes(' ')) {
                // Try to find a translation that matches
                const normalizedKey = key.toLowerCase().trim();
                for (const [transKey, transValue] of Object.entries(this.translations)) {
                    if (transValue && transValue.toLowerCase().trim() === normalizedKey) {
                        translated = transValue;
                        break;
                    }
                }
            }
            
            // If still not found, use key as-is (might be already translated)
            if (!translated) {
                translated = key;
            }
        }
        
        // Replace placeholders
        if (params && Object.keys(params).length > 0) {
            translated = this.replacePlaceholders(translated, params);
        }
        
        return translated;
    }
    
    /**
     * Replace placeholders in translation string
     * Supports {key} and %(key)s formats
     */
    replacePlaceholders(text, params) {
        let result = text;
        
        // Replace {key} format
        for (const [key, value] of Object.entries(params)) {
            const placeholder = `{${key}}`;
            result = result.replace(new RegExp(placeholder.replace(/[{}]/g, '\\$&'), 'g'), value);
        }
        
        // Replace %(key)s format (Flask-Babel style)
        for (const [key, value] of Object.entries(params)) {
            const placeholder = `%(${key})s`;
            result = result.replace(new RegExp(placeholder.replace(/[%()s]/g, '\\$&'), 'g'), value);
        }
        
        return result;
    }
    
    /**
     * Get translation for a specific category
     */
    getCategoryTranslation(category) {
        const categoryMap = {
            'success': this.translate('Success') || 'Success',
            'error': this.translate('Error') || 'Error',
            'warning': this.translate('Warning') || 'Warning',
            'info': this.translate('Information') || 'Information',
            'processing': this.translate('Processing') || 'Processing'
        };
        
        return categoryMap[category] || category;
    }
    
    /**
     * Update translations dictionary (useful for dynamic updates)
     */
    updateTranslations(newTranslations) {
        this.translations = { ...this.translations, ...newTranslations };
    }
    
    /**
     * Get all translations
     */
    getAllTranslations() {
        return { ...this.translations };
    }
}

// Initialize translation helper
const translationHelper = new TranslationHelper();

// Export globally
window.TranslationHelper = translationHelper;
window.t = (key, params) => translationHelper.translate(key, params);
window.translate = (key, params) => translationHelper.translate(key, params);

// Export for modules
export default translationHelper;

