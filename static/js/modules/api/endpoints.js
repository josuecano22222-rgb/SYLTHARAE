/**
 * API Endpoint Definitions
 * Centralized API URL builders
 */

/**
 * Build API endpoint URLs
 */
export const endpoints = {
    // Search endpoints
    search: (params) => {
        const url = new URL('/api/search', window.location.origin);
        Object.entries(params || {}).forEach(([key, value]) => {
            if (value !== null && value !== undefined && value !== '') {
                url.searchParams.set(key, value);
            }
        });
        return url.toString();
    },
    
    searchHistory: (limit = 10) => `/api/search/history?limit=${limit}`,
    searchSaved: () => '/api/search/saved',
    searchSavedById: (id) => `/api/search/saved/${id}`,
    searchExport: () => '/api/search/export',
    searchRipgrepStatus: () => '/api/search/ripgrep-status',
    searchSnippets: () => '/api/search/snippets',
    searchCancel: () => '/api/search/cancel',
    searchMetrics: () => '/api/search/metrics',
    
    // File endpoints
    fileDetails: (fileId) => `/file/${fileId}`,
    fileExport: (fileId) => `/api/files/${fileId}/export`,
    fileDelete: (fileId) => `/file/${fileId}/delete`,
    filePreview: (fileId, maxWidth = 1200, maxHeight = 800) => 
        `/api/preview/${fileId}?max_width=${maxWidth}&max_height=${maxHeight}`,
    
    // Bulk file operations
    bulkAnalyze: () => '/analysis/batch/process',
    bulkExport: () => '/files/export',
    bulkDelete: () => '/files/bulk-delete',
    
    // Section endpoints - Use /api/archives/* for cursor-based pagination
    sectionCategory: (params) => {
        const url = new URL('/api/archives/categories', window.location.origin);
        Object.entries(params || {}).forEach(([key, value]) => {
            if (value !== null && value !== undefined && value !== '') {
                url.searchParams.set(key, value);
            }
        });
        return url.toString();
    },
    
    sectionKeywords: (params) => {
        const url = new URL('/api/archives/keywords', window.location.origin);
        Object.entries(params || {}).forEach(([key, value]) => {
            if (value !== null && value !== undefined && value !== '') {
                url.searchParams.set(key, value);
            }
        });
        return url.toString();
    },
    
    sectionTitles: (params) => {
        const url = new URL('/api/archives/titles', window.location.origin);
        Object.entries(params || {}).forEach(([key, value]) => {
            if (value !== null && value !== undefined && value !== '') {
                url.searchParams.set(key, value);
            }
        });
        return url.toString();
    },
    
    sectionSources: (params) => {
        const url = new URL('/api/archives/sources', window.location.origin);
        Object.entries(params || {}).forEach(([key, value]) => {
            if (value !== null && value !== undefined && value !== '') {
                url.searchParams.set(key, value);
            }
        });
        return url.toString();
    },
    
    sectionSides: (params) => {
        const url = new URL('/api/archives/sides', window.location.origin);
        Object.entries(params || {}).forEach(([key, value]) => {
            if (value !== null && value !== undefined && value !== '') {
                url.searchParams.set(key, value);
            }
        });
        return url.toString();
    },
    
    sectionHash: (params) => {
        const url = new URL('/api/archives/hashs', window.location.origin);
        Object.entries(params || {}).forEach(([key, value]) => {
            if (value !== null && value !== undefined && value !== '') {
                url.searchParams.set(key, value);
            }
        });
        return url.toString();
    },
    
    sectionGeolocation: (params) => {
        const url = new URL('/api/archives/geolocation', window.location.origin);
        Object.entries(params || {}).forEach(([key, value]) => {
            if (value !== null && value !== undefined && value !== '') {
                url.searchParams.set(key, value);
            }
        });
        return url.toString();
    },

    sectionFiletypes: (params) => {
        const url = new URL('/api/archives/filetypes', window.location.origin);
        Object.entries(params || {}).forEach(([key, value]) => {
            if (value !== null && value !== undefined && value !== '') {
                url.searchParams.set(key, value);
            }
        });
        return url.toString();
    },
    
    sectionAddress: (params) => {
        const url = new URL('/api/archives/addresses', window.location.origin);
        Object.entries(params || {}).forEach(([key, value]) => {
            if (value !== null && value !== undefined && value !== '') {
                url.searchParams.set(key, value);
            }
        });
        return url.toString();
    },
    
    // Item endpoints - Use /api/archives/files for consistency
    itemFiles: (section, itemId, params) => {
        const url = new URL('/api/archives/files', window.location.origin);
        url.searchParams.set('section', section);
        url.searchParams.set('id', itemId);
        Object.entries(params || {}).forEach(([key, value]) => {
            if (value !== null && value !== undefined && value !== '') {
                url.searchParams.set(key, value);
            }
        });
        return url.toString();
    },
    
    // Item files with additional filters (source_id, side_id)
    itemFilesWithFilters: (section, itemId, params) => {
        const url = new URL('/api/archives/files', window.location.origin);
        url.searchParams.set('section', section);
        url.searchParams.set('id', itemId);
        Object.entries(params || {}).forEach(([key, value]) => {
            if (value !== null && value !== undefined && value !== '') {
                url.searchParams.set(key, value);
            }
        });
        return url.toString();
    },
    
    // Source/Side categories and keywords endpoints
    sourceCategoriesKeywords: (sourceId, params) => {
        const url = new URL('/api/archives/source-categories-keywords', window.location.origin);
        url.searchParams.set('source_id', sourceId);
        Object.entries(params || {}).forEach(([key, value]) => {
            if (value !== null && value !== undefined && value !== '') {
                url.searchParams.set(key, value);
            }
        });
        return url.toString();
    },
    
    sideCategoriesKeywords: (sideId, params) => {
        const url = new URL('/api/archives/side-categories-keywords', window.location.origin);
        url.searchParams.set('side_id', sideId);
        Object.entries(params || {}).forEach(([key, value]) => {
            if (value !== null && value !== undefined && value !== '') {
                url.searchParams.set(key, value);
            }
        });
        return url.toString();
    },
    
    // Keyword associations
    updateKeywordAssociations: () => '/api/keywords/update-associations',
    
    // Add item endpoints
    addItem: (type) => `/api/${type}/add`,
    searchItems: (type, query) => `/api/${type}/search?q=${encodeURIComponent(query)}`
};

/**
 * Get section API endpoint based on section name
 */
export function getSectionEndpoint(section) {
    const sectionApiMap = {
        category: endpoints.sectionCategory,
        keywords: endpoints.sectionKeywords,
        titles: endpoints.sectionTitles,
        sources: endpoints.sectionSources,
        sides: endpoints.sectionSides,
        hash: endpoints.sectionHash,
        address: endpoints.sectionAddress,
        geolocation: endpoints.sectionGeolocation,
        filetypes: endpoints.sectionFiletypes
    };
    
    return sectionApiMap[section] || null;
}

