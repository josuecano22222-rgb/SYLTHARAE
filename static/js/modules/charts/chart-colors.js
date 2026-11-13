/**
 * Chart Colors Utility
 * Provides theme-aware chart colors from CSS variables
 */

/**
 * Get chart colors from CSS variables
 * @param {number} count - Number of colors needed
 * @returns {Array<string>} Array of color values
 */
function getChartColors(count = 10) {
    const colors = [];
    const root = document.documentElement;
    
    // Get chart colors from CSS variables (chart-color-1 through chart-color-10)
    for (let i = 1; i <= Math.max(count, 10); i++) {
        const cssVar = `--chart-color-${i}`;
        const color = getComputedStyle(root).getPropertyValue(cssVar).trim();
        
        if (color) {
            colors.push(color);
        } else {
            // Fallback to default colors if CSS variable not set
            const defaults = [
                '#667eea', '#764ba2', '#10b981', '#f59e0b', '#ef4444',
                '#06b6d4', '#8b5cf6', '#ec4899', '#f97316', '#14b8a6'
            ];
            colors.push(defaults[(i - 1) % defaults.length]);
        }
    }
    
    return colors.slice(0, count);
}

/**
 * Get theme colors (success, warning, danger, etc.)
 * @returns {Object} Object with theme color values
 */
function getThemeColors() {
    const root = document.documentElement;
    const getColor = (varName) => {
        return getComputedStyle(root).getPropertyValue(varName).trim() || '';
    };
    
    return {
        primary: getColor('--primary-color'),
        secondary: getColor('--secondary-color'),
        success: getColor('--success-color'),
        warning: getColor('--warning-color'),
        danger: getColor('--danger-color'),
        info: getColor('--info-color'),
        unsorted: getColor('--text-muted') || '#6c757d',
        textWhite: getColor('--text-white') || '#ffffff'
    };
}

/**
 * Get color with opacity
 * @param {string} color - Hex color value
 * @param {number} opacity - Opacity (0-1)
 * @returns {string} rgba color string
 */
function getColorWithOpacity(color, opacity = 0.1) {
    if (!color) return `rgba(0, 0, 0, ${opacity})`;
    
    // Remove # if present
    color = color.replace('#', '');
    
    // Convert hex to RGB
    const r = parseInt(color.substring(0, 2), 16);
    const g = parseInt(color.substring(2, 4), 16);
    const b = parseInt(color.substring(4, 6), 16);
    
    return `rgba(${r}, ${g}, ${b}, ${opacity})`;
}

// Export for use in other scripts
if (typeof window !== 'undefined') {
    window.ChartColors = {
        getChartColors,
        getThemeColors,
        getColorWithOpacity
    };
}

// Export for ES modules
export { getChartColors, getThemeColors, getColorWithOpacity };
export default {
    getChartColors,
    getThemeColors,
    getColorWithOpacity
};


