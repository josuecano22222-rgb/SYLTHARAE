/**
 * File Management System
 * Handles all file listing, filtering, selection, and bulk operations
 * Consolidated from files-management.js
 */

import { apiPost, apiDelete } from '../api/api-client.js';
import { getCSRFToken } from '../core/utils.js';
import * as fileSelection from './file-selection.js';
import * as fileExport from './file-export.js';

// Current state
const FileManagement = {
    // Current state
    selectedFiles: new Set(),
    currentView: 'list',
    
    // Pagination data from server
    data: window.fileManagementData || {},
    
    // Initialize
    init() {
        this.restoreFilterValues();
        this.setupEventListeners();
        this.restoreViewPreference();
        this.updateBulkToolbar();
    },
    
    // Restore filter values from URL parameters
    restoreFilterValues() {
        const params = new URLSearchParams(window.location.search);
        
        // Restore search input
        const searchInput = document.getElementById('smartSearch');
        if (searchInput && params.has('search')) {
            searchInput.value = params.get('search');
        }
        
        // Restore filter dropdowns (file type supports multi-select via comma / getAll)
    const filters = {
        'sourceFilter': 'source',
        'sideFilter': 'side',
        'statusFilter': 'status'
    };
    
    Object.entries(filters).forEach(([elementId, paramName]) => {
        const element = document.getElementById(elementId);
        if (element && params.has(paramName)) {
            element.value = params.get(paramName);
        }
    });

    const typeFilter = document.getElementById('fileTypeFilter');
    if (typeFilter) {
        const typeValues = params.getAll('file_type');
        const joined = typeValues.length
            ? typeValues
            : String(params.get('file_type') || '').split(',').map((v) => v.trim()).filter(Boolean);
        if (typeFilter.multiple) {
            const wanted = new Set(joined);
            Array.from(typeFilter.options).forEach((opt) => {
                opt.selected = wanted.has(opt.value);
            });
        } else if (joined[0]) {
            typeFilter.value = joined[0];
        }
    }
        // Restore per page selector
        const perPageSelect = document.getElementById('perPageFiles');
        if (perPageSelect && params.has('limit')) {
            perPageSelect.value = params.get('limit');
        }
    },
    
    // Setup all event listeners
    setupEventListeners() {
        // View mode toggle
        document.querySelectorAll('.view-mode-btn').forEach(btn => {
            btn.addEventListener('click', (e) => this.switchView(e.target.closest('.view-mode-btn').dataset.view));
        });
        
        // Filter changes - search input
        const searchInput = document.getElementById('smartSearch');
        if (searchInput) {
            // Remove existing listeners to avoid duplicates
            const newSearchInput = searchInput.cloneNode(true);
            searchInput.parentNode.replaceChild(newSearchInput, searchInput);
            
            newSearchInput.addEventListener('keypress', (e) => {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    this.applyFilters();
                }
            });
            
            // Also trigger on blur if value changed
            let lastSearchValue = newSearchInput.value;
            newSearchInput.addEventListener('blur', () => {
                if (newSearchInput.value !== lastSearchValue) {
                    lastSearchValue = newSearchInput.value;
                    this.applyFilters();
                }
            });
        }
        
        // Filter dropdowns - add change listeners
        ['sourceFilter', 'sideFilter', 'fileTypeFilter', 'statusFilter'].forEach(id => {
            const element = document.getElementById(id);
            if (element) {
                // Remove existing listeners to avoid duplicates
                const newElement = element.cloneNode(true);
                element.parentNode.replaceChild(newElement, element);
                
                newElement.addEventListener('change', () => {
                    this.applyFilters();
                });
            }
        });
        
        // Checkbox changes
        document.querySelectorAll('.file-checkbox').forEach(cb => {
            cb.addEventListener('change', () => this.updateBulkToolbar());
        });
    },
    
    // ==================== VIEW MANAGEMENT ====================
    switchView(view) {
        this.currentView = view;
        
        // Update buttons
        document.querySelectorAll('.view-mode-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.view === view);
        });
        
        // Show/hide views
        document.querySelectorAll('.files-view').forEach(viewEl => {
            viewEl.style.display = viewEl.dataset.view === view ? 'block' : 'none';
        });
        
        // Save preference
        localStorage.setItem('filesViewMode', view);
    },
    
    restoreViewPreference() {
        const saved = localStorage.getItem('filesViewMode');
        if (saved) {
            this.switchView(saved);
        }
    },
    
    // ==================== SELECTION MANAGEMENT ====================
    selectAllFiles() {
        fileSelection.selectAllFiles();
        const selectAllCheckbox = document.getElementById('selectAllCheckbox');
        if (selectAllCheckbox) {
            selectAllCheckbox.checked = true;
        }
        this.updateBulkToolbar();
    },
    
    deselectAllFiles() {
        fileSelection.deselectAllFiles();
        const selectAllCheckbox = document.getElementById('selectAllCheckbox');
        if (selectAllCheckbox) {
            selectAllCheckbox.checked = false;
        }
        this.updateBulkToolbar();
    },
    
    toggleSelectAll(checkbox) {
        if (checkbox.checked) {
            this.selectAllFiles();
        } else {
            this.deselectAllFiles();
        }
    },

    /**
     * The single select/deselect control (see file-selection.js): selects
     * everything when nothing is selected, clears the selection otherwise.
     */
    toggleAllFilesSelection() {
        fileSelection.toggleAllFilesSelection();
        this.updateBulkToolbar();
    },
    
    updateBulkToolbar() {
        const selected = document.querySelectorAll('.file-checkbox:checked').length;
        
        // Update count
        const countEl = document.getElementById('selectedCount');
        if (countEl) countEl.textContent = selected;
        
        // Enable/disable bulk action buttons
        const bulkBtns = [
            'bulkAnalyzeBtn', 'bulkContentBtn', 'bulkOriginalsBtn',
            'bulkNamesCsvBtn', 'bulkNamesExcelBtn', 'bulkTextFolderBtn',
            'bulkOriginalsFolderBtn', 'bulkDeleteBtn', 'bulkViewAnalysisBtn',
            'bulkRenameExportBtn', 'bulkOpenOriginalBtn', 'bulkLocateBtn',
        ];
        bulkBtns.forEach(btnId => {
            const btn = document.getElementById(btnId);
            if (btn) btn.disabled = selected === 0;
        });
        // Open original is single-file only
        const openBtn = document.getElementById('bulkOpenOriginalBtn');
        if (openBtn) openBtn.disabled = selected !== 1;
        
        // Update select all checkbox state
        const allCheckboxes = document.querySelectorAll('.file-checkbox');
        const selectAllCheckbox = document.getElementById('selectAllCheckbox');
        if (selectAllCheckbox && allCheckboxes.length > 0) {
            selectAllCheckbox.checked = selected === allCheckboxes.length;
            selectAllCheckbox.indeterminate = selected > 0 && selected < allCheckboxes.length;
        }

        // Keep the single select/deselect toggle in sync with the selection.
        fileSelection.updateSelectToggle();
    },
    
    // ==================== FILTER MANAGEMENT ====================
    applyFilters() {
        const params = new URLSearchParams();

        const search = document.getElementById('smartSearch')?.value?.trim();
        const source = document.getElementById('sourceFilter')?.value;
        const side = document.getElementById('sideFilter')?.value;
        const typeEl = document.getElementById('fileTypeFilter');
        const fileTypes = typeEl?.multiple
            ? Array.from(typeEl.selectedOptions).map((o) => o.value).filter(Boolean)
            : (typeEl?.value ? [typeEl.value] : []);
        const status = document.getElementById('statusFilter')?.value;
        const limit = document.getElementById('perPageFiles')?.value || '10';

        // The column sort is the table's own control: keep whatever the
        // headers put in the URL (`sort` + `order`).
        const currentParams = new URLSearchParams(window.location.search);
        const sort = currentParams.get('sort');
        const order = currentParams.get('order');

        // Add to params if not empty
        if (search) params.set('search', search);
        if (source) params.set('source', source);
        if (side) params.set('side', side);
        if (fileTypes.length === 1) params.set('file_type', fileTypes[0]);
        else if (fileTypes.length > 1) params.set('file_type', fileTypes.join(','));
        if (status) params.set('status', status);
        if (sort) params.set('sort', sort);
        if (order) params.set('order', order);
        if (limit) params.set('limit', limit);
        
        // Reset to first page when filtering
        params.delete('cursor');
        
        // Navigate
        (window.swapNavigate
            ? window.swapNavigate(window.location.pathname + '?' + params.toString())
            : (window.location.href = window.location.pathname + '?' + params.toString()));
    },
    
    clearFileSearch() {
        const searchInput = document.getElementById('smartSearch');
        if (searchInput) {
            searchInput.value = '';
            this.applyFilters();
        }
    },
    
    // ==================== PAGINATION ====================
    changeFilesPageSize() {
        const perPageSelect = document.getElementById('perPageFiles');
        if (perPageSelect) {
            const params = new URLSearchParams(window.location.search);
            params.set('limit', perPageSelect.value);
            params.set('page', '1'); // a new batch starts at the first batch
            params.delete('cursor');
            (window.swapNavigate
            ? window.swapNavigate(window.location.pathname + '?' + params.toString())
            : (window.location.href = window.location.pathname + '?' + params.toString()));
        }
    },
    
    // ==================== BULK OPERATIONS ====================
    async bulkAnalyze() {
        const selected = Array.from(document.querySelectorAll('.file-checkbox:checked')).map(cb => cb.value);
        
        if (selected.length === 0) {
            const msg = window.translations?.pleaseSelectFilesToAnalyze || 'Please select files to analyze';
            if (window.showWarning) {
                window.showWarning(msg);
            } else if (window.alert) {
                window.alert(msg);
            }
            return;
        }
        
        try {
            const data = await apiPost('/analysis/batch/process', { file_ids: selected });
            
            if (data.success) {
                const message = `${window.translations?.analysisStartedFor || 'Analysis started for'} ${selected.length} ${window.translations?.files || 'files'}`;
                if (window.showSuccess) {
                    window.showSuccess(message);
                } else if (window.alert) {
                    window.alert(message);
                }
                setTimeout(() => {
                    (window.swapNavigate
                            ? window.swapNavigate(window.location.pathname + '?t=' + Date.now())
                            : (window.location.href = window.location.pathname + '?t=' + Date.now()));
                }, 2000);
            } else {
                const errorMsg = `${window.translations?.errorStartingAnalysis || 'Error starting analysis'}: ${data.error || 'Unknown error'}`;
                if (window.showError) {
                    window.showError(errorMsg);
                } else if (window.alert) {
                    window.alert(errorMsg);
                }
            }
        } catch (error) {
            console.error('Error in bulkAnalyze:', error);
            const errorMsg = window.translations?.errorStartingAnalysis || 'Error starting analysis';
            if (window.showError) {
                window.showError(errorMsg);
            } else if (window.alert) {
                window.alert(errorMsg);
            }
        }
    },
    
    async bulkExport(mode) {
        const selected = Array.from(document.querySelectorAll('.file-checkbox:checked')).map(cb => cb.value);

        if (selected.length === 0) {
            const msg = window.translations?.pleaseSelectFilesToExport || 'Please select files to export';
            if (window.showWarning) {
                window.showWarning(msg);
            } else if (window.alert) {
                window.alert(msg);
            }
            return;
        }

        // The two batch export kinds: the extracted content, or the
        // original source files as one ZIP. Both Save As.
        await fileExport.exportSelectedFiles(selected, mode === 'originals' ? 'originals' : 'text');
    },
    
    async bulkDelete() {
        const selected = Array.from(document.querySelectorAll('.file-checkbox:checked')).map(cb => cb.value);
        
        if (selected.length === 0) {
            const msg = window.translations?.pleaseSelectFilesToDelete || 'Please select files to delete';
            if (window.showWarning) {
                window.showWarning(msg);
            } else if (window.alert) {
                window.alert(msg);
            }
            return;
        }
        
        const confirmMsg = `${selected.length} ${window.translations?.deleteFilesConfirm || 'files will be deleted. Are you sure?'}`;
        if (!confirm(confirmMsg)) {
            return;
        }
        
        try {
            const data = await apiPost('/files/bulk-delete', { file_ids: selected });
            
            if (data.success) {
                const message = `${window.translations?.deletedFiles || 'Deleted'} ${selected.length} ${window.translations?.files || 'files'}`;
                if (window.showSuccess) {
                    window.showSuccess(message);
                } else if (window.alert) {
                    window.alert(message);
                }
                (window.swapNavigate
                            ? window.swapNavigate(window.location.pathname + '?t=' + Date.now())
                            : (window.location.href = window.location.pathname + '?t=' + Date.now()));
            } else {
                const errorMsg = `${window.translations?.errorDeletingFiles || 'Error deleting files'}: ${data.error || 'Unknown error'}`;
                if (window.showError) {
                    window.showError(errorMsg);
                } else if (window.alert) {
                    window.alert(errorMsg);
                }
            }
        } catch (error) {
            console.error('Error in bulkDelete:', error);
            const errorMsg = window.translations?.errorDeletingFiles || 'Error deleting files';
            if (window.showError) {
                window.showError(errorMsg);
            } else if (window.alert) {
                window.alert(errorMsg);
            }
        }
    },
    
    // ==================== SINGLE FILE OPERATIONS ====================
    async deleteFile(fileId) {
        const confirmMsg = window.translations?.deleteFileConfirm || 'Are you sure you want to delete this file?';
        if (!confirm(confirmMsg)) {
            return;
        }
        
        try {
            const data = await apiDelete(`/file/${fileId}/delete`);
            
            if (data.success) {
                if (window.showSuccess) {
                    window.showSuccess(window.translations?.fileDeleted || 'File deleted');
                } else if (window.alert) {
                    window.alert(window.translations?.fileDeleted || 'File deleted');
                }
                (window.swapNavigate
                            ? window.swapNavigate(window.location.pathname + '?t=' + Date.now())
                            : (window.location.href = window.location.pathname + '?t=' + Date.now()));
            } else {
                const errorMsg = `${window.translations?.errorDeletingFile || 'Error deleting file'}: ${data.error || 'Unknown error'}`;
                if (window.showError) {
                    window.showError(errorMsg);
                } else if (window.alert) {
                    window.alert(errorMsg);
                }
            }
        } catch (error) {
            console.error('Error in deleteFile:', error);
            const errorMsg = window.translations?.errorDeletingFile || 'Error deleting file';
            if (window.showError) {
                window.showError(errorMsg);
            } else if (window.alert) {
                window.alert(errorMsg);
            }
        }
    }
};

// FILE-UI-01: files_list.html references deleteFile(id) from inline onclick
// handlers; expose the implementation globally (this module is loaded as an ES
// module, so its top-level names are module-scoped). bulkDelete is published by
// the page that owns the list it deletes from - files-list-page.js here, and
// keywords-list-page.js / words-list-page.js on their own lists - because this
// module is imported on every route and used to overwrite their handlers.
window.deleteFile = (...args) => FileManagement.deleteFile(...args);

// Export for use in other modules
export default FileManagement;

