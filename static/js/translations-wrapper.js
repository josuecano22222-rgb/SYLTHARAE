/**
 * Translations Wrapper
 * Loads translations module and ensures it's available globally
 * This is a module script that imports and re-exports the translations
 */

import translationHelper from './modules/core/translations.js';

// Translations are already exposed globally by the module (window.TranslationHelper, window.t, window.translate)
// This import ensures the module is loaded and executed
console.log('✅ Translations loaded via module');

