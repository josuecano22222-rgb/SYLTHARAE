/**
 * Theme Initializer
 * Loads theme colors from JSON and initializes ThemeManager
 */

import { getPageData } from './pages/data-helper.js';

export function initializeTheme() {
    // Get theme colors from JSON script tag
    const themeColors = getPageData('theme-colors');
    
    // Filter out null values and set on window for ThemeManager
    if (themeColors && Object.keys(themeColors).length > 0) {
        window.INITIAL_THEME_COLORS = {};
        
        // Copy non-null values
        Object.entries(themeColors).forEach(([key, value]) => {
            if (value !== null && value !== undefined) {
                if (key === 'chart_colors' && typeof value === 'object') {
                    // Handle chart colors object
                    Object.entries(value).forEach(([num, color]) => {
                        window.INITIAL_THEME_COLORS[`chart_color_${num}`] = color;
                    });
                } else {
                    window.INITIAL_THEME_COLORS[key] = value;
                }
            }
        });
    }
}

// Initialize immediately (before ThemeManager loads)
initializeTheme();

