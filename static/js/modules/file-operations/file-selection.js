/**
 * File Selection and Filtering
 * Handles file selection, filtering, and bulk operations
 */

import { exportSelectedFiles as exportFiles } from './file-export.js';

/**
 * The checkboxes whose checked state means "this file is selected".
 * One definition for select-all, deselect-all, the count and the toggle
 * button, so they can never disagree about what is selected.
 */
const FILE_CHECKBOX_SELECTOR =
    '.file-checkbox, .file-select-checkbox, .file-row-item input[type="checkbox"]';

/**
 * The same checkboxes, qualified with :checked per part. A selector list
 * cannot be suffixed as a whole ("a, b:checked" leaves "a" unchecked-filtered),
 * so the checked form is spelled out.
 */
const FILE_CHECKBOX_CHECKED_SELECTOR =
    '.file-checkbox:checked, .file-select-checkbox:checked, .file-row-item input[type="checkbox"]:checked';

/**
 * Select all files
 */
export function selectAllFiles() {
    // Support both .file-checkbox and .file-select-checkbox for compatibility
    const checkboxes = document.querySelectorAll(FILE_CHECKBOX_SELECTOR);
    checkboxes.forEach(checkbox => {
        checkbox.checked = true;
    });
    updateSelectedCount();
}

/**
 * Deselect all files
 */
export function deselectAllFiles() {
    // Support both .file-checkbox and .file-select-checkbox for compatibility
    const checkboxes = document.querySelectorAll(FILE_CHECKBOX_SELECTOR);
    checkboxes.forEach(checkbox => {
        checkbox.checked = false;
    });
    updateSelectedCount();
}

/**
 * The one select/deselect control: selects every file when nothing is
 * selected, clears the selection otherwise (it replaces the former
 * Select All / Select None button pair). The visual state - label, icon,
 * pressed flag, count - follows the selection through updateSelectToggle().
 */
export function toggleAllFilesSelection() {
    const anySelected =
        document.querySelectorAll(FILE_CHECKBOX_CHECKED_SELECTOR).length > 0;
    if (anySelected) {
        deselectAllFiles();
    } else {
        selectAllFiles();
    }
}

/**
 * Sync the single select/deselect toggle button with the current selection:
 * its label and icon come from the page translations (with the same English
 * fallbacks the rest of the module uses), aria-pressed says whether a
 * selection is active, and the count badge shows how many files are selected.
 */
export function updateSelectToggle() {
    const button = document.getElementById('selectToggleBtn');
    if (!button) {
        return;
    }
    const selected =
        document.querySelectorAll(FILE_CHECKBOX_CHECKED_SELECTOR).length;
    const active = selected > 0;
    const words = (typeof window !== 'undefined' && window.translations) || {};
    button.setAttribute('aria-pressed', active ? 'true' : 'false');
    button.classList.toggle('is-selected', active);
    const toggleText = active
        ? (words.deselectAll || 'Select None')
        : (words.selectAll || 'Select All');
    const label = button.querySelector('.select-toggle-label, .visually-hidden');
    if (label) {
        label.textContent = toggleText;
    }
    button.setAttribute('title', toggleText);
    button.setAttribute('aria-label', toggleText);
    if (window.bootstrap?.Tooltip) {
        try {
            const tip = window.bootstrap.Tooltip.getInstance(button);
            if (tip) tip.setContent({ '.tooltip-inner': toggleText });
        } catch (_error) { /* */ }
    }
    const icon = button.querySelector('.select-toggle-icon');
    if (icon) {
        icon.className = `bi ${active ? 'bi-dash-square-fill' : 'bi-check-square-fill'} select-toggle-icon`;
    }
    const count = document.getElementById('selectToggleCount');
    if (count) {
        count.textContent = String(selected);
        count.hidden = !active;
    }
}

/**
 * Export selected files — ONE zip download of the extracted text of every
 * selected file. (This used to fire one download per file with a 100 ms
 * stagger; with a 50-file selection that meant fifty downloads.)
 */
export async function exportSelectedFiles() {
    // Support both .file-checkbox and .file-select-checkbox for compatibility
    const selectedCheckboxes = document.querySelectorAll('.file-checkbox:checked, .file-select-checkbox:checked, .file-row-item input[type="checkbox"]:checked');
    const fileIds = Array.from(selectedCheckboxes).map(cb => parseInt(cb.value));

    if (fileIds.length === 0) {
        const notificationSystem = await import('../ui/notifications.js');
        const { translations } = await import('../core/config.js');
        notificationSystem.default.warning(translations.pleaseSelectAtLeastOneFileToExport || 'Please select at least one file to export');
        return;
    }

    const { exportSelectedFiles: exportBulkZip } = await import('./bulk-analyst-actions.js');
    exportBulkZip();
}

/**
 * Export the ORIGINAL source files of the selection as one zip download
 * (see bulk-analyst-actions.js).
 */
export async function exportSelectedOriginals() {
    const { exportSelectedOriginals: exportOriginalsZip } = await import('./bulk-analyst-actions.js');
    exportOriginalsZip();
}

/**
 * Filter displayed files by search query
 */
export function filterDisplayedFiles(searchQuery) {
    const query = (searchQuery || '').toLowerCase().trim();
    
    // Support .file-card (list view), .file-grid-item (grid view), and .file-row-item for compatibility
    // Also support any element with data-file-id attribute
    const fileElements = document.querySelectorAll('.file-card[data-file-id], .file-grid-item[data-file-id], .file-row-item[data-file-id], [data-file-id]');
    
    if (!query) {
        // Show all if no query - restore original numbering
        fileElements.forEach((el) => {
            el.style.display = '';
            // Restore original number from data attribute if it exists
            const originalNumber = el.getAttribute('data-original-number');
            if (originalNumber) {
                const numberBadge = el.querySelector('.file-card-number');
                if (numberBadge) {
                    numberBadge.textContent = originalNumber;
                }
            }
        });
        updateSelectedCount();
        return;
    }
    
    // Split query into keywords for better matching
    const keywords = query.split(/\s+/).filter(k => k.length > 0);
    const lowerQuery = query.toLowerCase();
    
    // Store original numbers before filtering
    fileElements.forEach(element => {
        const numberBadge = element.querySelector('.file-card-number');
        if (numberBadge && !element.hasAttribute('data-original-number')) {
            element.setAttribute('data-original-number', numberBadge.textContent);
        }
    });
    
    let visibleIndex = 0;
    fileElements.forEach(element => {
        const fileName = (element.getAttribute('data-file-name') || '').toLowerCase();
        const fileType = (element.getAttribute('data-file-type') || '').toLowerCase();
        const fileSource = (element.getAttribute('data-file-source') || element.getAttribute('data-file-source-id') || element.getAttribute('data-file-source-name') || '').toLowerCase();
        const fileSide = (element.getAttribute('data-file-side') || element.getAttribute('data-file-side-id') || element.getAttribute('data-file-side-name') || '').toLowerCase();
        const fileDate = (element.getAttribute('data-file-date') || '').toLowerCase();
        const fileMeta = element.textContent.toLowerCase();
        
        // Check if all keywords match (AND logic) or any part matches
        let matches = false;
        if (keywords.length > 1) {
            // All keywords must be found (AND logic)
            matches = keywords.every(keyword => 
                fileName.includes(keyword) || 
                fileType.includes(keyword) || 
                fileSource.includes(keyword) || 
                fileSide.includes(keyword) ||
                fileDate.includes(keyword) ||
                fileMeta.includes(keyword)
            );
        } else {
            // Single keyword or phrase match
            matches = fileName.includes(lowerQuery) || 
                     fileType.includes(lowerQuery) || 
                     fileSource.includes(lowerQuery) || 
                     fileSide.includes(lowerQuery) ||
                     fileDate.includes(lowerQuery) ||
                     fileMeta.includes(lowerQuery);
        }
        
        if (matches) {
            element.style.display = '';
            visibleIndex++;
            // Update number badge to reflect position in filtered list
            const numberBadge = element.querySelector('.file-card-number');
            if (numberBadge) {
                numberBadge.textContent = visibleIndex;
            }
        } else {
            element.style.display = 'none';
        }
    });
    
    updateSelectedCount();
}

/**
 * Update selected files count display
 */
function updateSelectedCount() {
    const selectedCount = document.querySelectorAll('.file-checkbox:checked, .file-select-checkbox:checked, .file-row-item input[type="checkbox"]:checked').length;
    const totalCount = document.querySelectorAll('.file-checkbox, .file-select-checkbox, .file-row-item input[type="checkbox"]').length;
    
    // Update any count display elements if they exist
    const countElements = document.querySelectorAll('[data-selected-count], #selectedCount');
    countElements.forEach(el => {
        el.textContent = `${selectedCount} / ${totalCount}`;
    });

    // The select/deselect toggle always shows the current selection.
    updateSelectToggle();
}

/**
 * Initialize file selection handlers
 */

export function initializeFileSelection() {
    // Marker on <html>, not a module variable: each soft-nav revisit can
    // re-evaluate this module, which would reset a module-scoped flag and
    // pile up another document-level change listener.
    if (document.documentElement.dataset.fileSelectionWired) return;
    document.documentElement.dataset.fileSelectionWired = '1';
    // Add change handlers to checkboxes
    document.addEventListener('change', (e) => {
        if (e.target.classList.contains('file-checkbox')
                || e.target.classList.contains('file-select-checkbox')) {
            updateSelectedCount();
        }
    });
}

