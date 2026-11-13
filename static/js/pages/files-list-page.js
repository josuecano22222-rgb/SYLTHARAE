/**
 * Files List Page JavaScript
 * Extracted from file/files_list.html
 */

import FileManagement from '../modules/file-operations/file-management.js';
import { enhanceMultiSelect, iconForFileType } from '../modules/ui/multi-select-dropdown.js';
import {
    exportSelectionToFolder,
    exportFileNames,
    openDetailsExportDialog,
} from '../modules/file-operations/selectable-files-ops.js';
import DocumentActions, { initDocumentActions } from '../modules/documents/document-actions.js';

// Load translations from JSON script tag
let translations = {};

function bindFilesListWindowExports() {
    window.selectAllFiles = function () {
        if (FileManagement && typeof FileManagement.selectAllFiles === 'function') {
            FileManagement.selectAllFiles();
        }
    };
    window.deselectAllFiles = function () {
        if (FileManagement && typeof FileManagement.deselectAllFiles === 'function') {
            FileManagement.deselectAllFiles();
        }
    };
    window.toggleSelectAll = function (checkbox) {
        if (FileManagement && typeof FileManagement.toggleSelectAll === 'function') {
            FileManagement.toggleSelectAll(checkbox);
        }
    };
    window.toggleAllFilesSelection = function () {
        if (FileManagement && typeof FileManagement.toggleAllFilesSelection === 'function') {
            FileManagement.toggleAllFilesSelection();
        }
    };
    window.updateBulkToolbar = function () {
        if (FileManagement && typeof FileManagement.updateBulkToolbar === 'function') {
            FileManagement.updateBulkToolbar();
        }
    };
    window.bulkAnalyze = function () {
        if (FileManagement && typeof FileManagement.bulkAnalyze === 'function') {
            FileManagement.bulkAnalyze();
        }
    };
    window.bulkExport = function (mode) {
        if (FileManagement && typeof FileManagement.bulkExport === 'function') {
            FileManagement.bulkExport(mode);
        }
    };
    // bulkDelete() is this page's: the registry publishes the file-management
    // one for no page, and the keywords/word lists publish their own.
    window.bulkDelete = function () {
        if (FileManagement && typeof FileManagement.bulkDelete === 'function') {
            FileManagement.bulkDelete();
        }
    };
    window.exportSelectionToFolder = exportSelectionToFolder;
    window.exportFileNames = exportFileNames;
    window.openDetailsExportDialog = openDetailsExportDialog;
    window.exportSelectedFileNames = async function (format = 'csv') {
        return exportFileNames({ scope: 'selected', format: format === 'excel' ? 'excel' : 'csv' });
    };
    window.clearFileSearch = function () {
        if (FileManagement && typeof FileManagement.clearFileSearch === 'function') {
            FileManagement.clearFileSearch();
        }
    };
    window.changeFilesPageSize = function () {
        if (FileManagement && typeof FileManagement.changeFilesPageSize === 'function') {
            FileManagement.changeFilesPageSize();
        }
    };
    window.removeSameContextDuplicates = removeSameContextDuplicates;
    if (typeof FileManagement.applyFilters === 'function') {
        const bound = FileManagement.applyFilters.bind(FileManagement);
        window.applyFileFilters = bound;
        window.applyFilters = bound;
    }
}

/**
 * Remove the repeats inside each (source, side): one file recorded more than
 * once in the same source and the same side keeps its earliest record, and the
 * rest are removed. Files from another source or side are never touched.
 *
 * The reader sees the exact numbers first (a read-only preview), then confirms.
 * Nothing is deleted without that confirmation.
 */
async function removeSameContextDuplicates() {
    const toast = (message, tone) => {
        if (!window.Toast) return;
        if (tone === 'error') window.Toast.error(message);
        else if (tone === 'success') window.Toast.success(message);
        else if (window.Toast.info) window.Toast.info(message);
    };
    const csrf = () => {
        if (window.CSRF && typeof window.CSRF.getToken === 'function') return window.CSRF.getToken();
        const meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute('content') : '';
    };

    let preview;
    try {
        const response = await fetch('/api/files/same-context-duplicates', {
            credentials: 'same-origin',
            headers: { Accept: 'application/json' },
        });
        preview = await response.json();
        if (!response.ok || !preview.success) throw new Error(preview.error || 'preview failed');
    } catch (_error) {
        toast(translations.duplicatePreviewFailed || 'The repeated files could not be checked.', 'error');
        return;
    }

    if (!preview.to_remove) {
        toast(translations.noRepeatedFiles || 'No file repeats inside one source and side.', 'info');
        return;
    }

    const confirmed = await window.ConfirmDialog.request({
        root: 'sameContextDuplicatesDialog',
        title: translations.removeRepeatsTitle || 'Remove exact repeats in the same source and side?',
        message: (translations.removeRepeatsMessage
            || 'Records with the same name, content and details, in the same source and the same side, are removed; one record is kept. Records with another name, source or side are not touched. Original files on disk are not deleted.')
            + ` ${preview.to_remove} record(s) in ${preview.groups} group(s).`,
        dangerous: true,
    });
    if (!confirmed) return;

    try {
        const response = await fetch('/api/files/same-context-duplicates/remove', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { Accept: 'application/json', 'X-CSRFToken': csrf() },
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok || !result.success) throw new Error(result.error || 'remove failed');
        toast(result.message || `Removed ${result.removed} repeated file record(s).`, 'success');
        if (window.refreshMainContent) window.refreshMainContent();
        else window.location.reload();
    } catch (_error) {
        toast(translations.duplicateRemoveFailed || 'The exact repeats could not be removed.', 'error');
    }
}

// The universal initializer imports this module and runs init() on a full
// load AND on every in-application navigation (the static sidebar), so the
// page's handlers re-bind to the fresh table instead of going stale.
function init() {
    // Load translations from JSON script tag
    const pageDataEl = document.getElementById('files-list-page-data');
    if (pageDataEl) {
        try {
            const data = JSON.parse(pageDataEl.textContent);
            translations = data.translations || {};
            // Make translations available globally for FileManagement
            window.translations = window.translations || {};
            Object.assign(window.translations, translations);
        } catch (e) {
            console.error('Error parsing files list page data:', e);
        }
    }
    
    // Load page data (pagination info) from JSON script tag
    const pageInfoEl = document.getElementById('page-data');
    if (pageInfoEl) {
        try {
            const pageData = JSON.parse(pageInfoEl.textContent);
            // Set data for FileManagement
            window.fileManagementData = pageData;
            FileManagement.data = pageData;
            console.log('Page data loaded:', pageData);
        } catch (e) {
            console.error('Error parsing page data:', e);
        }
    }
    
    // Initialize file management
    if (FileManagement && typeof FileManagement.init === 'function') {
        FileManagement.init();
    }

    bindFilesListWindowExports();
    window.DocumentActions = DocumentActions;

    const typeFilter = document.getElementById('fileTypeFilter');
    if (typeFilter) {
        enhanceMultiSelect(typeFilter, {
            placeholder: translations.allTypes || 'All types',
            compact: true,
            iconForOption: (value) => iconForFileType(value),
        });
    }

    if (window.bootstrap?.Tooltip) {
        document.querySelectorAll('.action-bar [data-bs-toggle="tooltip"]').forEach((el) => {
            try {
                window.bootstrap.Tooltip.getOrCreateInstance(el, { trigger: 'hover focus' });
            } catch (_error) { /* */ }
        });
    }

    console.log('Files list page loaded');
}

export { init };
