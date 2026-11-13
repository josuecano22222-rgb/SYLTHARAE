/**
 * Keyword Associations Utility
 * Handles updating keyword associations across all files
 */

import notificationSystem from '../ui/notifications.js';
import { translations } from '../core/config.js';
import { getCSRFToken } from '../core/utils.js';
import { apiPost } from '../api/api-client.js';
import { endpoints } from '../api/endpoints.js';
import { navigationState } from '../core/state.js';
import { loadSectionView } from '../views/section-view.js';
import { loadItemView } from '../views/item-view.js';
import { loadRootView } from '../views/root-view.js';

/**
 * Update keyword associations across all files
 */
export async function updateKeywordAssociations() {
    const btn = document.getElementById('updateKeywordsBtn');
    let updateBtn = btn;
    
    if (!btn) {
        // Try to find button by class if ID not found
        const buttons = document.querySelectorAll('.add-item-btn');
        updateBtn = Array.from(buttons).find(b => 
            b.textContent.includes('Update Keywords') || 
            b.textContent.includes('Update')
        );
        if (!updateBtn) {
            console.error('Update Keywords button not found');
            notificationSystem.error('Update Keywords button not found');
            return;
        }
    }
    
    const originalHTML = updateBtn.innerHTML;
    updateBtn.disabled = true;
    updateBtn.innerHTML = '<i class="bi bi-hourglass-split"></i> <span>' + 
        (translations.updating || 'Updating...') + '</span>';
    
    // Confirm action - handle both Promise and boolean returns
    const confirmMessage = translations.updateKeywordAssociationsConfirm || 
                'This will scan all files in the database and update keyword associations. This may take a while. Continue?';
    
    // Check if confirm returns a Promise (new async version) or boolean (old sync version)
    const confirmResult = confirm(confirmMessage);
    if (confirmResult && typeof confirmResult.then === 'function') {
        // It's a Promise - wait for it
        const confirmed = await confirmResult;
        if (!confirmed) {
            updateBtn.disabled = false;
            updateBtn.innerHTML = originalHTML;
            return;
        }
    } else if (!confirmResult) {
        // It's a boolean and user cancelled
        updateBtn.disabled = false;
        updateBtn.innerHTML = originalHTML;
        return;
    }
    
    try {
        const apiUrl = endpoints.updateKeywordAssociations();
        const data = await apiPost(apiUrl, {});
        
        updateBtn.disabled = false;
        updateBtn.innerHTML = originalHTML;
        
        if (data.success) {
            let message = (translations.keywordAssociationsUpdated || 'Keyword associations updated successfully!') + '\n\n' +
                (translations.filesProcessed || 'Files processed') + ': ' + data.files_processed + ' / ' + data.total_files + '\n' +
                (translations.newAssociations || 'New associations') + ': ' + data.new_associations + '\n' +
                (translations.keywordsChecked || 'Keywords checked') + ': ' + data.keywords_checked;
            if (data.errors > 0) {
                message += '\n' + (translations.errors || 'Errors') + ': ' + data.errors;
            }
            notificationSystem.success(message);
            
            // Reload current view to show updated data
            if (navigationState.currentSection === 'keywords') {
                // Reload keywords section
                loadSectionView('keywords', navigationState.sectionPagination?.currentPage || 1);
            } else if (navigationState.currentFileSection === 'keywords') {
                // Reload keyword files view
                loadItemView(
                    navigationState.currentFileSection, 
                    navigationState.currentFileItemId, 
                    null, 
                    navigationState.filePagination?.currentPage || 1
                );
            } else {
                // Reload root view
                loadRootView();
            }
        } else {
            notificationSystem.error(data.error || translations.error || 'Error updating keyword associations');
        }
    } catch (error) {
        updateBtn.disabled = false;
        updateBtn.innerHTML = originalHTML;
        console.error('Error updating keyword associations:', error);
        notificationSystem.error((translations.error || 'Error') + ': ' + (error.message || 'Unknown error'));
    }
}

// No window export here: the registry publishes this implementation as
// window.updateKeywordAssociationsShared, and the keywords list page publishes
// its own window.updateKeywordAssociations. A third publication from this
// module made the name's owner depend on import order.

export default {
    updateKeywordAssociations
};

