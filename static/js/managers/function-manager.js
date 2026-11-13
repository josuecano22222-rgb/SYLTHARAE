/**
 * Function Manager
 * Central function registry and export point
 * All functions accessible via this manager
 */

// Core modules
import * as coreState from '../modules/core/state.js';
import * as coreConfig from '../modules/core/config.js';
import * as coreUtils from '../modules/core/utils.js';
import systemSettings from '../modules/core/system-settings.js';
import translationHelper from '../modules/core/translations.js';

// Navigation modules
import * as navigation from '../modules/navigation/navigator.js';
import * as breadcrumb from '../modules/navigation/breadcrumb.js';
import * as history from '../modules/navigation/history.js';
import * as eventDelegation from '../modules/navigation/event-delegation.js';

// View modules
import * as rootView from '../modules/views/root-view.js';
import * as sectionView from '../modules/views/section-view.js';
import * as itemView from '../modules/views/item-view.js';
import * as fileView from '../modules/views/file-view.js';

// Rendering modules
import * as gridRenderer from '../modules/rendering/grid-renderer.js';
import * as listRenderer from '../modules/rendering/list-renderer.js';
import * as pagination from '../modules/rendering/pagination.js';

// File operations modules
import * as fileDetails from '../modules/file-operations/file-details.js';
import * as fileExport from '../modules/file-operations/file-export.js';
import * as fileNavigation from '../modules/file-operations/file-navigation.js';
import * as fileSelection from '../modules/file-operations/file-selection.js';
import * as bulkAnalyst from '../modules/file-operations/bulk-analyst-actions.js';
import fileManagement from '../modules/file-operations/file-management.js';

// Modal modules
import * as modalManager from '../modules/modals/modal-manager.js';
import * as select2Initializers from '../modules/modals/select2-initializers.js';

// Search modules
import * as modalSearch from '../modules/search/modal-search.js';
import * as globalSearch from '../modules/search/global-search.js';

// Chart modules
import * as classificationCharts from '../modules/charts/classification-charts.js';
import * as chartColors from '../modules/charts/chart-colors.js';
import chartExport from '../modules/charts/chart-export.js';
import chartResponsive from '../modules/charts/chart-responsive.js';

// UI modules
import notificationSystem from '../modules/ui/notifications.js';
import * as sidebar from '../modules/ui/sidebar.js';
import * as filters from '../modules/ui/filters.js';
import * as viewMode from '../modules/ui/view-mode.js';
import themeManager from '../modules/ui/theme-manager.js';

// API modules
import * as apiClient from '../modules/api/api-client.js';
import * as endpoints from '../modules/api/endpoints.js';

// Utility modules
import * as keywordAssociations from '../modules/utils/keyword-associations.js';

// Message modules - these use IIFEs, so we need to access from window
// Import them to trigger initialization, then use window versions
import '../modules/messages/message_system.js';
import '../modules/messages/message-router.js';
import '../modules/messages/message-formatter.js';
import '../modules/messages/interface-messages.js';

// Get from window after IIFEs have run
// Note: MessageRouter is the instance (not class) on window
const MessageSystem = typeof window !== 'undefined' ? window.MessageSystem : null;
const MessageRouter = typeof window !== 'undefined' ? (window.MessageRouter || window.MessageRouterClass) : null;
const MessageFormatter = typeof window !== 'undefined' ? window.MessageFormatter : null;
const InterfaceMessages = typeof window !== 'undefined' ? window.InterfaceMessages : null;

// Rendering modules
import CursorPaginatorModule from '../modules/rendering/cursor-pagination.js';
// CursorPaginator should be available from the module export
const CursorPaginator = CursorPaginatorModule || (typeof window !== 'undefined' ? window.CursorPaginator : null);

/**
 * Function Manager - Central registry
 */
const FunctionManager = {
    // Core
    core: {
        state: coreState,
        config: coreConfig,
        utils: coreUtils,
        systemSettings,
        translations: translationHelper
    },
    
    // Navigation
    navigation: {
        ...navigation,
        breadcrumb,
        history,
        eventDelegation
    },
    
    // Views
    views: {
        root: rootView,
        section: sectionView,
        item: itemView,
        file: fileView
    },
    
    // Rendering
    rendering: {
        grid: gridRenderer,
        list: listRenderer,
        pagination,
        cursorPagination: CursorPaginator
    },
    
    // Messages
    messages: {
        system: MessageSystem,
        router: MessageRouter,
        formatter: MessageFormatter,
        interface: InterfaceMessages
    },
    
    // File operations
    fileOperations: {
        details: fileDetails,
        // Export helpers (copy/download/coordinates) live here so the global
        // click delegate can reach them without relying on window globals.
        fileExport: fileExport,
        export: fileExport,
        navigation: fileNavigation,
        selection: fileSelection,
        management: fileManagement
    },
    
    // Modals
    modals: {
        manager: modalManager,
        select2: select2Initializers,
        closeFileModal: fileDetails.closeFileModal
    },
    
    // Search
    search: {
        modal: modalSearch,
        global: globalSearch
    },
    
    // Charts
    charts: {
        classification: classificationCharts,
        colors: chartColors,
        export: chartExport,
        responsive: chartResponsive
    },
    
    // UI
    ui: {
        notifications: notificationSystem,
        sidebar,
        filters,
        viewMode,
        theme: themeManager
    },
    
    // API
    api: {
        client: apiClient,
        endpoints
    },
    
    // Utils
    utils: {
        keywordAssociations
    }
};

// Export for ES modules
export default FunctionManager;

/**
 * Re-bind window.* shims (soft-nav revisits routes without reloading this module).
 * Safe to call on every page mount.
 */
export function attachWindowGlobals() {
    if (typeof window === 'undefined') return;
    window.fms = FunctionManager;
    
    // Expose commonly used functions globally for backward compatibility
    window.navigateToRoot = navigation.navigateToRoot;
    window.navigateToSection = navigation.navigateToSection;
    window.navigateToItem = navigation.navigateToItem;
    window.navigateBack = history.navigateBack;
    window.navigateForward = history.navigateForward;
    window.handleSortChange = navigation.handleSortChange;
    
    // File operations
    window.showFileDetails = fileDetails.showFileDetails;
    window.closeFileModal = fileDetails.closeFileModal;
    window.exportFile = fileExport.exportFile;
    window.switchModalContentTab = fileDetails.switchModalContentTab;
    window.getModalContentTab = fileDetails.getModalContentTab;
    window.previewFile = globalSearch.previewFile; // Also expose previewFile directly for backward compatibility
    window.copyModalContent = fileExport.copyModalContent;
    window.downloadModalContent = fileExport.downloadModalContent;
    window.exportModalFile = fileExport.exportModalFile;
    window.copyGeolocationCoordinates = fileExport.copyGeolocationCoordinates;
    window.navigateFileInModal = fileNavigation.navigateFileInModal;
    window.selectAllFiles = fileSelection.selectAllFiles;
    window.deselectAllFiles = fileSelection.deselectAllFiles;
    // The files list page publishes its own toggleAllFilesSelection; the shared
    // copy carries the suffix so the two never compete for one name.
    window.toggleAllFilesSelectionShared = fileSelection.toggleAllFilesSelection;
    // Search-advanced owns exportSelectedFiles for its own results table; the
    // shared selection export is the suffixed copy.
    window.exportSelectedFilesShared = fileSelection.exportSelectedFiles;
    window.exportSelectedOriginals = fileSelection.exportSelectedOriginals;
    window.filterDisplayedFiles = fileSelection.filterDisplayedFiles;
    // Bulk analyst categorization of the FMAS selection (analyst namespace only)
    window.assignAnalystCategoriesToSelection = bulkAnalyst.assignAnalystCategoriesToSelection;
    window.removeAnalystCategoriesFromSelectionShared = bulkAnalyst.removeAnalystCategoriesFromSelection;
    window.updateAnalystBulkBar = bulkAnalyst.updateAnalystBulkBar;
    // Shared selectable-file operations (folder export, native open, locate, details)
    window.exportSelectionToFolder = async (...args) => {
        const mod = await import('../modules/file-operations/selectable-files-ops.js');
        return mod.exportSelectionToFolder(...args);
    };
    window.exportFileWithRename = async (...args) => {
        const mod = await import('../modules/file-operations/selectable-files-ops.js');
        return mod.exportFileWithRename(...args);
    };
    window.openInNativeApp = async (...args) => {
        const mod = await import('../modules/file-operations/selectable-files-ops.js');
        return mod.openInNativeApp(...args);
    };
    window.locateFile = async (...args) => {
        const mod = await import('../modules/file-operations/selectable-files-ops.js');
        return mod.locateFile(...args);
    };
    window.exportFileNames = async (...args) => {
        const mod = await import('../modules/file-operations/selectable-files-ops.js');
        return mod.exportFileNames(...args);
    };
    window.openDetailsExportDialog = async (...args) => {
        const mod = await import('../modules/file-operations/selectable-files-ops.js');
        return mod.openDetailsExportDialog(...args);
    };
    // Shared document actions controller (data-doc-action toolbar)
    import('../modules/documents/document-actions.js').then((mod) => {
        if (mod.initDocumentActions) mod.initDocumentActions();
        window.DocumentActions = mod.DocumentActions || mod.default;
    }).catch((err) => console.warn('DocumentActions unavailable:', err));
    // Analytical reports from the File Manager selection / card action
    window.openAnalyticalReportForSelection = async (...args) => {
        const mod = await import('../modules/file-operations/fmas-analytical-report.js');
        return mod.openAnalyticalReportForSelection(...args);
    };
    window.openAnalyticalReportForFile = async (...args) => {
        const mod = await import('../modules/file-operations/fmas-analytical-report.js');
        return mod.openAnalyticalReportForFile(...args);
    };
    
    // Modal search
    window.performModalSearch = modalSearch.performModalSearch;
    window.findModalNext = modalSearch.findModalNext;
    window.findModalPrevious = modalSearch.findModalPrevious;
    window.clearModalSearch = modalSearch.clearModalSearch;
    
    // Modal manager
    window.openAddItemModal = modalManager.openAddItemModal;
    window.closeAddItemModal = modalManager.closeAddItemModal;
    window.loadItemsForModal = modalManager.loadItemsForModal;
    window.searchItems = modalManager.searchItems;
    window.submitAddItem = modalManager.submitAddItem;

    window.submitAddWordsCategorys = modalManager.submitAddWordsCategorys;
    window.searchCategoryWords = modalManager.searchCategoryWords;
    
    // Charts
    window.loadClassificationCharts = classificationCharts.loadClassificationCharts;
    window.switchDataType = classificationCharts.switchDataType;
    window.switchChartType = classificationCharts.switchChartType;
    window.filterChartData = classificationCharts.filterChartData;
    
    // Notification shortcuts
    window.showSuccess = (msg) => notificationSystem.success(msg);
    window.showError = (msg) => notificationSystem.error(msg);
    window.showWarning = (msg) => notificationSystem.warning(msg);
    window.showInfo = (msg) => notificationSystem.info(msg);
    
    // View functions
    window.loadSectionPage = sectionView.loadSectionPage;
    window.loadFilePage = itemView.loadFilePage;
    window.toggleSimilarTitles = itemView.toggleSimilarTitles;
    
    // Global search (backward compatibility with enhanced-search.js)
    window.enhancedSearch = {
        performSearch: globalSearch.performSearch,
        goToPage: globalSearch.goToPage,
        exportResults: globalSearch.exportResults,
        previewFile: globalSearch.previewFile,
        loadHistorySearch: globalSearch.loadHistorySearch,
        loadSavedSearch: globalSearch.loadSavedSearch,
        deleteSavedSearch: globalSearch.deleteSavedSearch,
        saveSearch: globalSearch.saveSearch,
        showSaveSearchModal: globalSearch.showSaveSearchModal
    };
    
    // Utility functions
    window.updateKeywordAssociationsShared = keywordAssociations.updateKeywordAssociations;
    
    // Select2 initialization functions (backward compatibility)
    window.initializeCategoryWordSelect = select2Initializers.initializeCategoryWordSelect;
    window.initializeKeywordWordsSelect = select2Initializers.initializeKeywordWordsSelect;
    window.initializeKeywordCategorySelect = select2Initializers.initializeKeywordCategorySelect;
    window.initializeWordsCategorysWordSelect = select2Initializers.initializeWordsCategorysWordSelect;
    window.initializeWordsCategorysCategorySelect = select2Initializers.initializeWordsCategorysCategorySelect;
    
    // Filters
    window.handleGlobalSearch = filters.handleGlobalSearch;
    // The dashboard pages, the files list and the keyword/word lists each own
    // applyFilters/resetFilters with their own section semantics. The generic
    // filter-bar versions live under the suffixed names so a page module is
    // never silently overwritten by the registry.
    window.applyFiltersShared = filters.applyFilters;
    window.resetFiltersShared = filters.resetFilters;
    window.hideFilters = filters.hideFilters;
    window.showFilters = filters.showFilters;
    
    // View mode
    window.setViewMode = viewMode.setViewMode;
    window.setFileViewMode = viewMode.setFileViewMode;
    
    // Initialize file view mode from localStorage
    if (viewMode.initializeFileViewMode) {
        viewMode.initializeFileViewMode();
    }
    
    // File Management functions (from files-management.js). Every binding
    // is existence-guarded: the module is shared by pages at different
    // migration stages (the archives workspace among them), and one absent
    // method used to throw mid-exposure and take the rest of the globals
    // down with it ("Cannot read properties of undefined (reading 'bind')").
    const exposeFileManagement = (method, globalName) => {
        if (fileManagement && typeof fileManagement[method] === 'function') {
            window[globalName] = fileManagement[method].bind(fileManagement);
        }
    };
    exposeFileManagement('selectAllFiles', 'selectAllFiles');
    exposeFileManagement('deselectAllFiles', 'deselectAllFiles');
    exposeFileManagement('toggleSelectAll', 'toggleSelectAll');
    exposeFileManagement('updateBulkToolbar', 'updateBulkToolbar');
    exposeFileManagement('clearFileSearch', 'clearFileSearch');
    exposeFileManagement('changeFilesPageSize', 'changeFilesPageSize');
    exposeFileManagement('bulkAnalyze', 'bulkAnalyze');
    exposeFileManagement('bulkExport', 'bulkExport');
    // bulkDelete belongs to the page whose list it deletes from (files list,
    // keywords list, word list). The registry does not publish it at all.

    exposeFileManagement('deleteFile', 'deleteFile');
    // Expose applyFilters for files list page (override the generic one from filters module)
    exposeFileManagement('applyFilters', 'applyFileFilters');
    exposeFileManagement('navigateToPage', 'navigateToPage');
    
    // Theme Manager
    window.ThemeManager = themeManager;
    window.themeManager = themeManager;
    
    // System Settings
    window.systemSettings = systemSettings;
    
    // Translations
    window.TranslationHelper = translationHelper;
    window.t = (key, params) => translationHelper.translate(key, params);
    window.translate = (key, params) => translationHelper.translate(key, params);
    
    // Chart utilities
    window.ChartColors = chartColors;
    // Don't override window.ChartExport - it's already set by the IIFE in chart-export.js
    // The module export is for ES6 imports only, window.ChartExport should be the actual object
    // window.ChartExport = chartExport; // REMOVED: Causes infinite recursion
    window.ChartResponsive = chartResponsive;
    
    // Message system (already exposed globally by modules, but ensure availability)
    window.MessageSystem = MessageSystem;
    window.MessageRouter = MessageRouter;
    window.MessageFormatter = MessageFormatter;
    window.InterfaceMessages = InterfaceMessages;
    
    // Cursor pagination
    window.CursorPaginator = CursorPaginator;
}

if (typeof window !== 'undefined') {
    attachWindowGlobals();
}

