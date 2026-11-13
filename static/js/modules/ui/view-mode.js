/**
 * View Mode Module
 * Handles switching between grid and list view modes
 */

import { navigationState } from '../core/state.js';
import { loadSectionView } from '../views/section-view.js';

/**
 * Set view mode (grid or list)
 */
export function setViewMode(mode) {
    if (!navigationState) return;
    
    navigationState.currentView = mode;
    
    // Update toggle buttons
    document.querySelectorAll('.view-toggle-btn').forEach(btn => {
        btn.classList.remove('active');
        if (btn.getAttribute('data-view') === mode) {
            btn.classList.add('active');
        }
    });
    
    // Apply view mode to current section
    applyViewMode();
}

/**
 * Apply current view mode to the displayed content
 */
function applyViewMode() {
    const currentState = navigationState.history?.[navigationState.currentIndex];
    if (currentState && currentState.type === 'section') {
        // Reload section view with current view mode
        loadSectionView(currentState.section);
    }
}

/**
 * Get current view mode
 */
export function getViewMode() {
    return navigationState?.currentView || 'grid';
}

/**
 * Set file view mode (grid or list)
 */
export function setFileViewMode(mode) {
    if (!navigationState) return;
    
    // Validate mode
    if (mode !== 'grid' && mode !== 'list') {
        mode = 'list';
    }
    
    navigationState.fileViewMode = mode;
    
    // Save to localStorage
    try {
        localStorage.setItem('fileViewMode', mode);
    } catch (e) {
        console.warn('Could not save file view mode to localStorage:', e);
    }
    
    // Update toggle buttons
    document.querySelectorAll('.file-view-toggle .view-toggle-btn').forEach(btn => {
        btn.classList.remove('active');
        if (btn.getAttribute('data-view') === mode) {
            btn.classList.add('active');
            btn.style.background = '#3b82f6';
            btn.style.color = 'white';
        } else {
            btn.style.background = 'transparent';
            btn.style.color = '#64748b';
        }
    });
    
    // Reload current file view with new mode
    applyFileViewMode();
}

/**
 * Apply current file view mode to the displayed content
 */
function applyFileViewMode() {
    // Check if we're viewing files
    if (navigationState.currentFileSection && navigationState.currentFileItemId) {
        // Get current state from history to preserve item name
        const currentState = navigationState.history?.[navigationState.currentIndex];
        const itemName = currentState?.itemName || null;
        
        // Use dynamic import to avoid circular dependencies
        Promise.all([
            import('../views/item-view.js'),
            import('../navigation/navigator.js')
        ]).then(([itemViewModule, navigatorModule]) => {
            // Check if we have filters applied
            if (navigationState.currentSourceFilter || navigationState.currentSideFilter) {
                itemViewModule.loadItemFilesWithFilters(
                    navigationState.currentFileSection,
                    navigationState.currentFileItemId,
                    itemName,
                    navigationState.currentSourceFilter,
                    navigationState.currentSideFilter,
                    navigationState.filePagination?.currentPage || 1
                );
            } else {
                itemViewModule.loadItemView(
                    navigationState.currentFileSection,
                    navigationState.currentFileItemId,
                    itemName,
                    navigationState.filePagination?.currentPage || 1
                );
            }
        }).catch(err => {
            console.error('Error reloading file view:', err);
        });
    }
}

/**
 * Get current file view mode
 */
export function getFileViewMode() {
    // Try to load from localStorage first
    try {
        const saved = localStorage.getItem('fileViewMode');
        if (saved === 'grid' || saved === 'list') {
            return saved;
        }
    } catch (e) {
        // Ignore localStorage errors
    }
    
    return navigationState?.fileViewMode || 'list';
}

/**
 * Initialize file view mode from localStorage
 */
export function initializeFileViewMode() {
    const saved = getFileViewMode();
    if (navigationState) {
        navigationState.fileViewMode = saved;
    }
}

