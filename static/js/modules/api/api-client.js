/**
 * API Client
 * Fetch wrapper with error handling and CSRF token support
 */

import { getCSRFToken, getCSRFTokenAsync } from '../core/utils.js';

/**
 * Make an API request with automatic CSRF token handling and timeout
 * @param {string} url - API endpoint URL
 * @param {Object} options - Fetch options
 * @param {number} timeout - Request timeout in milliseconds (default: 30000)
 * @returns {Promise<Response>} Fetch response
 */
export async function apiRequest(url, options = {}, timeout = 30000) {
    const method = (options.method || 'GET').toUpperCase();
    const needsCSRF = ['POST', 'PUT', 'DELETE', 'PATCH'].includes(method);
    
    // Get CSRF token - always fetch async for POST/PUT/DELETE to ensure it's fresh
    let csrfToken = '';
    if (needsCSRF) {
        // Try sync first for speed
        csrfToken = getCSRFToken();
        
        // If no token or empty, fetch it async
        if (!csrfToken || csrfToken.trim() === '') {
            try {
                csrfToken = await getCSRFTokenAsync();
            } catch (error) {
                console.warn('Failed to fetch CSRF token async:', error);
                // Try sync one more time as fallback
                csrfToken = getCSRFToken();
            }
        }
        
        // If still no token, throw error
        if (!csrfToken || csrfToken.trim() === '') {
            throw new Error('CSRF token is required but could not be obtained. Please refresh the page.');
        }
    }
    
    const defaultOptions = {
        headers: {
            'Content-Type': 'application/json',
            ...(needsCSRF && csrfToken ? { 'X-CSRFToken': csrfToken } : {})
        }
    };
    
    // Merge headers - ensure CSRF token is not overwritten
    const headers = {
        ...defaultOptions.headers,
        ...(options.headers || {})
    };
    
    // Ensure CSRF token is set if needed
    if (needsCSRF && csrfToken && !headers['X-CSRFToken']) {
        headers['X-CSRFToken'] = csrfToken;
    }
    
    const mergedOptions = {
        ...defaultOptions,
        ...options,
        headers
    };
    
    // PERFORMANCE FIX: Add request timeout to prevent hanging requests
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeout);
    
    // Add signal to options if not already present
    if (!mergedOptions.signal) {
        mergedOptions.signal = controller.signal;
    }
    
    try {
        const response = await fetch(url, mergedOptions);
        clearTimeout(timeoutId);
        
        // Handle non-OK responses
        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            
            // If CSRF error, try to refresh token and retry once
            if (response.status === 400 && errorData.error && errorData.error.includes('CSRF')) {
                console.warn('CSRF token invalid, attempting to refresh...');
                try {
                    const newToken = await getCSRFTokenAsync();
                    if (newToken && newToken !== csrfToken) {
                        // Retry with new token (with new timeout)
                        const retryController = new AbortController();
                        const retryTimeoutId = setTimeout(() => retryController.abort(), timeout);
                        headers['X-CSRFToken'] = newToken;
                        const retryResponse = await fetch(url, { 
                            ...mergedOptions, 
                            headers,
                            signal: retryController.signal
                        });
                        clearTimeout(retryTimeoutId);
                        if (retryResponse.ok) {
                            return retryResponse;
                        }
                    }
                } catch (retryError) {
                    console.error('Failed to refresh CSRF token:', retryError);
                }
            }
            
            throw new Error(errorData.error || `HTTP error! status: ${response.status}`);
        }
        
        return response;
    } catch (error) {
        clearTimeout(timeoutId);
        // Don't log AbortError as error - it's expected when requests are cancelled or timeout
        if (error.name === 'AbortError') {
            // Check if it was a timeout or manual abort
            if (controller.signal.aborted && !options.signal) {
                throw new Error('Request timeout. Please try again.');
            }
            // Silently handle manual abort - this is normal when cancelling requests
            throw error;
        }
        console.error('API request error:', error);
        throw error;
    }
}

/**
 * Make a GET request
 * @param {string} url - API endpoint URL (can include query params)
 * @param {Object} params - Additional query parameters (optional)
 * @param {Object} options - Additional fetch options (e.g., signal for AbortController)
 * @returns {Promise<Object>} JSON response data
 */
export async function apiGet(url, params = {}, options = {}) {
    const urlObj = new URL(url, window.location.origin);
    // Add additional params (will override existing ones with same key)
    Object.entries(params).forEach(([key, value]) => {
        if (value !== null && value !== undefined && value !== '') {
            urlObj.searchParams.set(key, value);
        }
    });
    
    const response = await apiRequest(urlObj.toString(), { method: 'GET', ...options });
    const data = await response.json();
    
    // Handle error responses
    if (!data.success && data.error) {
        throw new Error(data.error);
    }
    
    return data;
}

/**
 * Make a POST request
 * @param {string} url - API endpoint URL
 * @param {Object} data - Request body data
 * @param {Object} options - Additional fetch options
 * @returns {Promise<Object>} JSON response data
 */
export async function apiPost(url, data = {}, options = {}) {
    const response = await apiRequest(url, {
        method: 'POST',
        body: JSON.stringify(data),
        ...options
    });
    return response.json();
}

/**
 * Make a DELETE request
 * @param {string} url - API endpoint URL
 * @param {Object} options - Additional fetch options
 * @returns {Promise<Object>} JSON response data
 */
export async function apiDelete(url, options = {}) {
    const response = await apiRequest(url, {
        method: 'DELETE',
        ...options
    });
    return response.json();
}

/**
 * Make a PUT request
 * @param {string} url - API endpoint URL
 * @param {Object} data - Request body data
 * @param {Object} options - Additional fetch options
 * @returns {Promise<Object>} JSON response data
 */
export async function apiPut(url, data = {}, options = {}) {
    const response = await apiRequest(url, {
        method: 'PUT',
        body: JSON.stringify(data),
        ...options
    });
    return response.json();
}

