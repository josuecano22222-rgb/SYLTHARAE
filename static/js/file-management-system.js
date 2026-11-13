/**
 * File Management System - Main Entry Point
 * Main entry point that imports from function-manager.js
 * 
 * This initializes all modules and makes them available via window.fms.* and window.*
 */

import FunctionManager from './managers/function-manager.js';

// Store reference for global access
if (typeof window !== 'undefined') {
    window.FunctionManager = FunctionManager;
}

// Initialize all modules when DOM is ready
function initializeModules() {
    console.log('File Management System: Initializing modules...');
    console.log('File Management System: FunctionManager available:', !!FunctionManager);
    console.log('File Management System: Navigation available:', !!FunctionManager?.navigation);
    console.log('File Management System: initNavigation available:', !!FunctionManager?.navigation?.initNavigation);
    
    // Check if we're on the archives page (has unifiedContentView)
    const isArchivesPage = document.getElementById('unifiedContentView') !== null;
    console.log('File Management System: Is archives page:', isArchivesPage);
    
    // Initialize navigation only on archives page (where unifiedContentView exists)
    if (isArchivesPage && FunctionManager && FunctionManager.navigation && FunctionManager.navigation.initNavigation) {
        console.log('File Management System: Initializing navigation...');
        try {
            FunctionManager.navigation.initNavigation();
            console.log('File Management System: Navigation initialization called');
        } catch (error) {
            console.error('File Management System: Error initializing navigation:', error);
        }
    } else if (!isArchivesPage) {
        // Silently skip navigation initialization on non-archives pages
        console.log('File Management System: Skipping navigation initialization (not on archives page)');
    } else {
        console.error('File Management System: Navigation module not available!');
        console.error('File Management System: FunctionManager structure:', {
            hasFunctionManager: !!FunctionManager,
            hasNavigation: !!FunctionManager?.navigation,
            hasInitNavigation: !!FunctionManager?.navigation?.initNavigation
        });
    }
    
    // Initialize sidebar
    if (FunctionManager.ui?.sidebar?.setupSidebarKeyboardNavigation) {
        FunctionManager.ui.sidebar.setupSidebarKeyboardNavigation();
    }
    
    // Initialize file selection
    if (FunctionManager.fileOperations?.selection?.initializeFileSelection) {
        FunctionManager.fileOperations.selection.initializeFileSelection();
    }
    
    // Initialize file management (if on files page)
    // Check if we're on the files list page by looking for specific elements
    if (FunctionManager.fileOperations?.management?.init) {
        const filesTable = document.getElementById('filesTable');
        const paginationList = document.getElementById('paginationList');
        if (filesTable || paginationList) {
            FunctionManager.fileOperations.management.init();
        }
    }
    
    console.log('File Management System: Modules initialized');
}

// Initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initializeModules);
} else {
    initializeModules();
}

// Export for use in other scripts
export default FunctionManager;

