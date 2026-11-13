/**
 * History Management
 * Handles browser-like back/forward navigation
 */

import { navigationState } from '../core/state.js';

/**
 * Add state to navigation history
 * @param {Object} state - Navigation state object
 */
export function addToHistory(state) {
    // Remove any states after current index (when navigating forward then back)
    if (navigationState.currentIndex < navigationState.history.length - 1) {
        navigationState.history = navigationState.history.slice(0, navigationState.currentIndex + 1);
    }
    
    // Add new state
    navigationState.history.push(state);
    navigationState.currentIndex = navigationState.history.length - 1;
    
    // Limit history size
    if (navigationState.history.length > 50) {
        navigationState.history.shift();
        navigationState.currentIndex--;
    }
}

/**
 * Navigate back in history
 */
export function navigateBack() {
    if (navigationState.currentIndex > 0) {
        navigationState.currentIndex--;
        const state = navigationState.history[navigationState.currentIndex];
        restoreState(state);
    }
}

/**
 * Navigate forward in history
 */
export function navigateForward() {
    if (navigationState.currentIndex < navigationState.history.length - 1) {
        navigationState.currentIndex++;
        const state = navigationState.history[navigationState.currentIndex];
        restoreState(state);
    }
}

/**
 * Restore state from history
 * @param {Object} state - Navigation state to restore
 */
function restoreState(state) {
    // Import navigation functions dynamically to avoid circular dependencies
    if (state.type === 'root') {
        if (window.fms && window.fms.navigation && window.fms.navigation.navigateToRoot) {
            window.fms.navigation.navigateToRoot();
        }
    } else if (state.type === 'section') {
        if (window.fms && window.fms.navigation && window.fms.navigation.navigateToSection) {
            window.fms.navigation.navigateToSection(state.section);
        }
    } else if (state.type === 'item') {
        if (window.fms && window.fms.navigation && window.fms.navigation.navigateToItem) {
            window.fms.navigation.navigateToItem(state.section, state.itemId, state.itemName);
        }
    }
    
    updateNavButtons();
}

/**
 * Update navigation buttons (back/forward) state
 */
export function updateNavButtons() {
    // The archive has its own in-page history controls; keep them distinct
    // from the browser-history controls in the application top bar. Duplicate
    // IDs made getElementById() update the wrong buttons on the archive page.
    const backBtn = document.getElementById('archiveNavBackBtn');
    const forwardBtn = document.getElementById('archiveNavForwardBtn');
    
    if (backBtn) {
        backBtn.disabled = navigationState.currentIndex <= 0;
    }
    
    if (forwardBtn) {
        forwardBtn.disabled = navigationState.currentIndex >= navigationState.history.length - 1;
    }
}

