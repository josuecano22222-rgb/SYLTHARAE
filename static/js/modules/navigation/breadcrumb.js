/**
 * Breadcrumb Management
 * Handles breadcrumb navigation display and updates
 */

import { translations } from '../core/config.js';

/**
 * Update breadcrumb navigation
 * @param {Array} path - Array of breadcrumb items: [{ name: string, state: object }]
 */
export function updateBreadcrumb(path) {
    const breadcrumbNav = document.getElementById('breadcrumbNav');
    if (!breadcrumbNav) return;
    
    breadcrumbNav.innerHTML = '';
    
    path.forEach((item, index) => {
        const breadcrumbItem = document.createElement('div');
        breadcrumbItem.className = 'breadcrumb-item';
        
        if (index < path.length - 1) {
            // Not the last item - make it clickable
            const link = document.createElement('span');
            link.className = 'breadcrumb-link';
            link.textContent = item.name;
            link.style.cursor = 'pointer';
            link.onclick = () => restoreState(item.state);
            breadcrumbItem.appendChild(link);
            
            const separator = document.createElement('span');
            separator.className = 'breadcrumb-separator';
            separator.textContent = ' / ';
            breadcrumbItem.appendChild(separator);
        } else {
            // Last item - current page
            const current = document.createElement('span');
            current.className = 'breadcrumb-current';
            current.textContent = item.name;
            breadcrumbItem.appendChild(current);
        }
        
        breadcrumbNav.appendChild(breadcrumbItem);
    });
}

/**
 * Restore state from breadcrumb click
 * @param {Object} state - Navigation state to restore
 */
function restoreState(state) {
    // This will be implemented by importing navigation functions
    // For now, we'll handle this in the navigator module
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
}

