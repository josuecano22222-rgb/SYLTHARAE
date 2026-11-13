/**
 * Core State Management
 * Centralized state objects for navigation, modals, pagination, files, and charts
 */

/**
 * Navigation state
 */
export const navigationState = {
    history: [],
    currentIndex: -1,
    currentView: 'grid', // View mode for sections
    fileViewMode: 'list', // View mode for files (grid or list)
    searchQuery: '',
    sortBy: 'file_count_desc', // Default sort: file count descending
    activeFilters: {}, // Store active filters (category, source, side, dates)
    filePagination: {
        currentPage: 1,
        perPage: 50,  // Default files per page
        total: 0,
        totalPages: 0,
        totalSize: 0,
        has_prev: false,
        has_next: false
    },
    sectionPagination: {
        currentPage: 1,
        perPage: 10,
        total: 0,
        totalPages: 0,
        has_prev: false,
        has_next: false
    },
    currentFileSection: null,
    currentFileItemId: null,
    currentSection: null,
    // File-list controls (Archives item view)
    fileListFilters: {
        fileType: '',
        sortBy: 'file_date',
        sortDir: 'desc',
        search: ''
    },
    // Accumulated files for Show More (append mode)
    loadedFiles: [],
    // Source/Side categories-keywords view state
    currentSourceId: null,
    currentSideId: null,
    currentSourceSection: null,
    currentSourceItemId: null,
    currentSourceItemName: null,
    // Filters for combined file queries
    currentSourceFilter: null,
    currentSideFilter: null
};

/**
 * File navigation state for modal
 */
export const fileNavigationState = {
    currentFiles: [],
    currentIndex: -1,
    currentPage: 1,
    totalPages: 1
};

/**
 * Modal state
 */
export const modalState = {
    isOpen: false,
    currentFileId: null,
    currentFileName: null,
    currentAnalysis: null,
};

/**
 * Pagination state
 */
export const paginationState = {
    currentPage: 1,
    perPage: 10,
    total: 0,
    totalPages: 0
};

/**
 * File state
 */
export const fileState = {
    selectedFiles: new Set(),
    currentView: 'list'
};

/**
 * Chart state for classification charts
 */
export const classificationChartState = {
    currentChart: null,
    chartData: null,
    currentDataType: 'categories', // 'categories', 'words', 'keywords'
    currentChartType: 'bar', // 'bar', 'pie', 'line'
    filterValue: ''
};

/**
 * Section cursor state for cursor-based pagination
 */
export const sectionCursorState = {
    category: {
        pageToCursor: new Map(),
        cursorToPage: new Map(),
        lastCursor: null
    },
    keywords: {
        pageToCursor: new Map(),
        cursorToPage: new Map(),
        lastCursor: null
    },
    titles: {
        pageToCursor: new Map(),
        cursorToPage: new Map(),
        lastCursor: null
    },
    sources: {
        pageToCursor: new Map(),
        cursorToPage: new Map(),
        lastCursor: null
    },
    sides: {
        pageToCursor: new Map(),
        cursorToPage: new Map(),
        lastCursor: null
    },
    hash: {
        pageToCursor: new Map(),
        cursorToPage: new Map(),
        lastCursor: null
    },
    geolocation: {
        pageToCursor: new Map(),
        cursorToPage: new Map(),
        lastCursor: null
    },
    filetypes: {
        pageToCursor: new Map(),
        cursorToPage: new Map(),
        lastCursor: null
    }
};

