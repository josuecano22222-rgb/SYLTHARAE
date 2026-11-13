/**
 * Core Utility Functions
 * Pure utility functions used throughout the application
 */

/**
 * Escape HTML to prevent XSS attacks
 * @param {string} text - Text to escape
 * @returns {string} Escaped HTML
 */
export function escapeHtml(text) {
    if (text == null) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/**
 * Escape a value for use inside a double-quoted HTML attribute.
 *
 * escapeHtml() escapes via textContent -> innerHTML, which handles & < > but
 * NOT quotes, because a text node never contains them. That is correct for
 * element content and unsafe for attribute values: a filename of
 *
 *     x" onmouseover="alert(1)
 *
 * survives escapeHtml unchanged, closes the attribute and injects a new one.
 * Filenames inside an uploaded archive are attacker-chosen, so every
 * attribute built from one needs this escaper instead.
 *
 * @param {*} value - raw value, typically an untrusted filename
 * @returns {string} value safe to place inside a double-quoted attribute
 */
export function escapeAttribute(value) {
    if (value == null) return '';
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

/**
 * Format file size in human-readable format
 * @param {number} bytes - File size in bytes
 * @returns {string} Formatted file size
 */
export function formatFileSize(bytes) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
}

/**
 * Get CSRF token from meta tag with fallback to API
 * @returns {string} CSRF token (synchronous - returns empty if not in meta tag)
 */
export function getCSRFToken() {
    const token = document.querySelector('meta[name="csrf-token"]');
    return token ? token.getAttribute('content') : '';
}

/**
 * Get CSRF token with async fallback to API
 * @returns {Promise<string>} CSRF token
 */
export async function getCSRFTokenAsync() {
    // Try meta tag first
    let metaToken = document.querySelector('meta[name="csrf-token"]');
    if (metaToken) {
        const token = metaToken.getAttribute('content');
        if (token && token.trim() !== '') {
            return token;
        }
    }
    
    // Fallback: fetch from API
    try {
        const response = await fetch('/api/csrf-token');
        if (!response.ok) {
            console.warn('Failed to fetch CSRF token from API');
            return '';
        }
        const data = await response.json();
        const newToken = data.csrf_token || '';
        
        // Update meta tag if it exists, or create it if it doesn't
        if (newToken) {
            if (!metaToken) {
                metaToken = document.createElement('meta');
                metaToken.setAttribute('name', 'csrf-token');
                document.head.appendChild(metaToken);
            }
            metaToken.setAttribute('content', newToken);
        }
        
        return newToken;
    } catch (error) {
        console.error('Error fetching CSRF token:', error);
        return '';
    }
}

/**
 * Build URL with query parameters
 * @param {string} baseUrl - Base URL
 * @param {Object} params - Query parameters object
 * @returns {string} URL with query string
 */
export function buildUrl(baseUrl, params) {
    const url = new URL(baseUrl, window.location.origin);
    Object.entries(params).forEach(([key, value]) => {
        if (value !== null && value !== undefined && value !== '') {
            url.searchParams.set(key, value);
        }
    });
    return url.toString();
}

/**
 * Debounce function to limit function calls
 * @param {Function} func - Function to debounce
 * @param {number} wait - Wait time in milliseconds
 * @returns {Function} Debounced function
 */
export function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

