/**
 * Theme Manager Wrapper
 * Loads theme manager module and ensures it's available globally
 * This is a module script that imports and re-exports the theme manager
 */

import themeManager from './modules/ui/theme-manager.js';

// Theme manager is already exposed globally by the module (window.ThemeManager)
// This import ensures the module is loaded and executed
console.log('✅ Theme Manager loaded via module');

