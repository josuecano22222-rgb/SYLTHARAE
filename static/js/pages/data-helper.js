/**
 * Data Helper Utility
 * Provides utilities for passing data from server to JavaScript
 */

/**
 * Get page data from JSON script tag
 * @param {string} id - ID of the script tag containing JSON data
 * @returns {Object} Parsed data object or empty object if not found
 */
export function getPageData(id = 'page-data') {
    // Soft-nav can leave a stale body-level island with the same generic id
    // (e.g. `page-data`). Prefer the copy inside the live main pane.
    const main = document.getElementById('mainContent');
    const element = (main && main.querySelector('#' + CSS.escape(id)))
        || document.getElementById(id);
    if (!element) {
        return {};
    }
    
    try {
        return JSON.parse(element.textContent);
    } catch (e) {
        console.error(`Error parsing page data from #${id}:`, e);
        return {};
    }
}

/**
 * Get data from data attribute
 * @param {string} selector - CSS selector for element with data attribute
 * @param {string} attribute - Name of data attribute (without 'data-' prefix)
 * @returns {*} Parsed value or null
 */
export function getDataAttribute(selector, attribute) {
    const element = document.querySelector(selector);
    if (!element) {
        return null;
    }
    
    const value = element.dataset[attribute];
    if (!value) {
        return null;
    }
    
    // Try to parse as JSON, fallback to string
    try {
        return JSON.parse(value);
    } catch (e) {
        return value;
    }
}

/**
 * Set data on window object (for backward compatibility)
 * @param {string} key - Key to set on window object
 * @param {*} value - Value to set
 */
export function setWindowData(key, value) {
    if (typeof window !== 'undefined') {
        window[key] = value;
    }
}

export default {
    getPageData,
    getDataAttribute,
    setWindowData
};

