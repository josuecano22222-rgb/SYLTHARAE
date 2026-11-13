/**
 * Sides List Page JavaScript
 * Extracted from Side/sides_list.html
 */

// Load translations from JSON script tag
let translations = {};

document.addEventListener('DOMContentLoaded', function() {
    // Load translations from JSON script tag
    const pageDataEl = document.getElementById('sides-list-page-data');
    if (pageDataEl) {
        try {
            const data = JSON.parse(pageDataEl.textContent);
            translations = data.translations || {};
            // Also make available on window for backward compatibility
            window.translations = window.translations || {};
            Object.assign(window.translations, translations);
        } catch (e) {
            console.error('Error parsing sides list page data:', e);
        }
    }
    
    console.log('Sides list page loaded');
});
// Toast notification helper function
function showToast(message, type = 'info', duration = 4000) {
    const toastContainer = (document.querySelector('[data-toast-region]') || document.getElementById('toastContainer'));
    if (!toastContainer) {
        const container = document.createElement('div');
        container.id = 'toastContainer';
        container.className = 'toast-container position-fixed top-0 end-0 p-3';
        container.style.zIndex = '9999';
        document.body.appendChild(container);
    }
    
    const toastId = 'toast-' + Date.now();
    const icons = {
        success: 'check-circle-fill',
        error: 'exclamation-triangle-fill',
        warning: 'exclamation-triangle-fill',
        info: 'info-circle-fill'
    };
    
    const bgColors = {
        success: 'success',
        error: 'danger',
        warning: 'warning',
        info: 'info'
    };
    
    const toastHtml = `
        <div id="${toastId}" class="toast align-items-center text-white bg-${bgColors[type]} border-0" role="alert" aria-live="assertive" aria-atomic="true">
            <div class="d-flex">
                <div class="toast-body">
                    <i class="bi bi-${icons[type]} me-2"></i>
                    ${message}
                </div>
                <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
            </div>
        </div>
    `;
    
    (document.querySelector('[data-toast-region]') || document.getElementById('toastContainer')).insertAdjacentHTML('beforeend', toastHtml);
    const toastElement = document.getElementById(toastId);
    const toast = new bootstrap.Toast(toastElement, { delay: duration });
    toast.show();
    
    toastElement.addEventListener('hidden.bs.toast', () => {
        toastElement.remove();
    });
}
// Global state for filtering and sorting
// ---------------------------------------------------------------------
// The list is the server's now: search, order and page live in the query
// string and the page renders what the route sends. This file keeps what a
// row and the toolbar offer: selection, bulk actions, the modals and the
// per-row operations.
// ---------------------------------------------------------------------

// Navigate to this list with changed query parameters; everything not named
// here (sort, order, per page) is carried over.
function navigateSides(changes) {
    const params = new URLSearchParams(window.location.search);
    Object.entries(changes || {}).forEach(([name, value]) => {
        if (value === undefined || value === null || value === '') {
            params.delete(name);
        } else {
            params.set(name, value);
        }
    });
    if (window.swapNavigate) { window.swapNavigate(window.location.pathname + '?' + params.toString()); } else { window.location.href = window.location.pathname + '?' + params.toString(); }
}

// The per-page selector in the table toolbar
function changeSidesPageSize() {
    const perPage = document.getElementById('sidesPerPage');
    if (perPage) navigateSides({ per_page: perPage.value, page: '1' });
}

// The select-all checkbox in the table header
function sidesSelectAll(master) {
    document.querySelectorAll('.side-checkbox').forEach(cb => {
        cb.checked = master.checked;
    });
    updateBulkButtons();
}

function selectAll() {
    document.querySelectorAll('.side-checkbox').forEach(cb => {
        cb.checked = true;
    });
    updateBulkButtons();
}

function selectNone() {
    document.querySelectorAll('.side-checkbox').forEach(cb => cb.checked = false);
    updateBulkButtons();
}

/** One control: select every row when empty, clear when any are selected. */
function toggleAllSelection() {
    const anySelected = document.querySelectorAll('.side-checkbox:checked').length > 0;
    if (anySelected) {
        selectNone();
    } else {
        selectAll();
    }
}

// The toolbar owns how the scope is drawn and announced; this page owns the
// numbers, because they are what the reader can see: how many rows are on
// screen right now, and how many of them are chosen. The words come from the
// bar (the component renders them through the catalogs), so no English is
// assembled here. One call, and the toolbar decides the rest - no action is
// named, and nothing is bound.
function updateBulkButtons() {
    if (!window.ActionToolbar) return;
    window.ActionToolbar.sync('sidesActionBar', {
        selected: document.querySelectorAll('.side-checkbox:checked').length,
        total: document.querySelectorAll('.side-checkbox').length
    });
}

function bulkExport() {
    const selectedIds = Array.from(document.querySelectorAll('.side-checkbox:checked'))
        .map((cb) => parseInt(cb.value, 10))
        .filter((id) => Number.isSafeInteger(id) && id > 0);

    if (selectedIds.length === 0) {
        showToast('Please select sides to export', 'warning');
        return;
    }

    const menu = document.querySelector('[data-export-interface="sides"]');
    const columns = Array.from(menu?.querySelectorAll('.ut-export-column-check:checked') || [])
        .map((box) => box.getAttribute('data-export-column'))
        .filter(Boolean);
    if (!columns.length) {
        showToast('Please select at least one column to export.', 'warning');
        return;
    }

    const params = new URLSearchParams();
    params.set('format', 'csv');
    params.set('ids', selectedIds.join(','));
    params.set('columns', columns.join(','));
    params.set('filename', `sides_selected_${new Date().toISOString().slice(0, 10)}.csv`);
    showToast(`Exporting ${selectedIds.length} side(s)...`, 'info');
    window.location.assign('/api/export/sides?' + params.toString());
}

function bulkUpdate() {
    const checkboxes = document.querySelectorAll('.side-checkbox:checked');
    const selectedIds = Array.from(checkboxes).map(cb => parseInt(cb.value));
    
    if (selectedIds.length === 0) {
        showToast('Please select sides to update', 'warning');
        return;
    }
    
    // Edit Selected edits one record: the selected side, or the first of
    // several after asking (same contract as Words and Keywords).
    if (selectedIds.length > 1 && !confirm(translations.editFirstSelected || 'Several sides are selected. Edit the first one?')) {
        return;
    }
    openSideModal(selectedIds[0]);
}

// Export single side
function exportSide(sideId) {
    fetch(`/api/sides/${sideId}/export`, { method: 'GET', credentials: 'same-origin' })
        .then(response => {
            if (!response.ok) throw new Error('Export failed');
            return response.blob();
        })
        .then(blob => {
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `side_${sideId}_export.json`;
            document.body.appendChild(a);
            a.click();
            window.URL.revokeObjectURL(url);
            document.body.removeChild(a);
            showToast(translations.sideDataExportedSuccessfully || 'Side data exported successfully!', 'success');
        })
        .catch(error => {
            console.error('Error exporting side:', error);
            showToast(translations.errorExportingSideData || 'Error exporting side data', 'error');
        });
}

// Clear the search box and the search itself
function clearSearch() {
    const searchInput = document.getElementById('sideSearch');
    if (searchInput) searchInput.value = '';
    navigateSides({ search: '', page: '1' });
}

// View side categories and keywords
function viewSideCategoriesKeywords(sideId) {
    if (window.swapNavigate) { window.swapNavigate(`/sides/${sideId}/categories-keywords`); } else { window.location.href = `/sides/${sideId}/categories-keywords`; }
}

// Search: Enter navigates with ?search=, which re-renders the list server-side
function initializeEventListeners() {
    const searchInput = document.getElementById('sideSearch');
    if (searchInput) {
        searchInput.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') {
                e.preventDefault();
                navigateSides({ search: searchInput.value.trim(), page: '1' });
            }
        });
    }
}

// Initialize on DOM ready: wire the search, then sync the toolbar once.
document.addEventListener('DOMContentLoaded', function () {
    initializeEventListeners();
    updateBulkButtons();
});

window.selectAll = selectAll;
window.selectNone = selectNone;
window.toggleAllSelection = toggleAllSelection;
window.sidesSelectAll = sidesSelectAll;
window.updateBulkButtons = updateBulkButtons;
window.bulkExport = bulkExport;
window.bulkUpdate = bulkUpdate;
window.changeSidesPageSize = changeSidesPageSize;
window.clearSearch = clearSearch;
window.viewSideCategoriesKeywords = viewSideCategoriesKeywords;
window.exportSide = exportSide;

function viewSide(sideId) {
    if (window.swapNavigate) { window.swapNavigate(`/sides/${sideId}`); } else { window.location.href = `/sides/${sideId}`; }
}

function editSide(sideId) {
    openSideModal(sideId);
}



function duplicateSide(sideId) {
    if (confirm(translations.createCopyOfSide)) {
        const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
        fetch(`/api/sides/${sideId}/duplicate`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken
            }
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                showToast(translations.sideDuplicatedSuccessfully || 'Side duplicated successfully!', 'success');
                setTimeout(() => {
                    if (window.swapNavigate) { window.swapNavigate(window.location.pathname + '?t=' + Date.now()); } else { window.location.href = window.location.pathname + '?t=' + Date.now(); }
                }, 500);
            } else {
                showToast((translations.errorDuplicatingSide || 'Error duplicating side') + ': ' + (data.message || (translations.unknownError || 'Unknown error')), 'error');
            }
        })
        .catch(error => {
            console.error('Error:', error);
            showToast(translations.errorDuplicatingSide || 'Error duplicating side', 'error');
        });
    }
}

function toggleSideStatus(sideId) {
    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
    fetch(`/api/sides/${sideId}/toggle-status`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken
        }
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            showToast(translations.sideStatusUpdated || 'Side status updated!', 'success');
            setTimeout(() => {
                if (window.swapNavigate) { window.swapNavigate(window.location.pathname + '?t=' + Date.now()); } else { window.location.href = window.location.pathname + '?t=' + Date.now(); }
            }, 500);
        } else {
            showToast((translations.errorUpdatingStatus || 'Error updating status') + ': ' + (data.message || (translations.unknownError || 'Unknown error')), 'error');
        }
    })
    .catch(error => {
        console.error('Error:', error);
        showToast(translations.errorUpdatingSideStatus || 'Error updating side status', 'error');
    });
}

function deleteSide(sideId, sideName = '') {
    const confirmMessage = sideName 
        ? `${translations.areYouSureDeleteSide || 'Are you sure you want to delete the side'} "${sideName}"?\n\n${translations.actionCannotBeUndone || 'This action cannot be undone.'}`
        : translations.deleteSideConfirm;
    
    // Show confirmation modal
    const modal = new bootstrap.Modal(document.getElementById('deleteConfirmModal'));
    const messageEl = document.getElementById('deleteConfirmMessage');
    const confirmBtn = document.getElementById('deleteConfirmButton');
    
    messageEl.textContent = confirmMessage;
    
    // Remove any existing event listeners
    const newConfirmBtn = confirmBtn.cloneNode(true);
    confirmBtn.parentNode.replaceChild(newConfirmBtn, confirmBtn);
    
    // Add click handler for confirmation
    newConfirmBtn.addEventListener('click', function performDelete() {
        modal.hide();
        
        // Show processing notification
        if (window.MessageFormatter) {
            window.MessageFormatter.showNotification('delete', 'processing', { item: sideName || 'Side' });
        }
        
        const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
        fetch(`/api/sides/${sideId}`, {
            method: 'DELETE',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken
            }
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                // Show formatted success notification
                if (window.MessageFormatter) {
                    window.MessageFormatter.showDeleteSuccess(sideName || 'Side', {
                        title: translations.sideDeleted || 'Side Deleted',
                        duration: 4000
                    });
                } else {
                    showToast(translations.sideDeletedSuccessfully || 'Side deleted successfully!', 'success');
                }
                setTimeout(() => {
                    if (window.swapNavigate) { window.swapNavigate(window.location.pathname + '?t=' + Date.now()); } else { window.location.href = window.location.pathname + '?t=' + Date.now(); }
                }, 500);
            } else {
                // Show formatted error notification
                if (window.MessageFormatter) {
                    window.MessageFormatter.showDeleteError(sideName || 'Side', {
                        title: translations.deleteFailed || 'Delete Failed',
                        detail: data.error,
                        duration: 8000
                    });
                } else {
                    showToast((translations.errorDeletingSide || 'Error deleting side') + ': ' + (data.error || (translations.unknownError || 'Unknown error')), 'error');
                }
            }
        })
        .catch(error => {
            console.error('Error:', error);
            if (window.MessageFormatter) {
                window.MessageFormatter.showDeleteError(sideName || 'Side', {
                    title: translations.deleteError || 'Delete Error',
                    duration: 6000
                });
            } else {
                showToast(translations.errorDeletingSide || 'Error deleting side', 'error');
            }
        });
    });
    
    modal.show();
}

// Modal functions
function openSideModal(sideId = null) {
    const modal = new bootstrap.Modal(document.getElementById('sideModal'));
    const form = document.getElementById('sideForm');
    const modalTitle = document.getElementById('modalTitle');
    const submitButtonText = document.getElementById('submitButtonText');
    
    // Reset form
    form.reset();
    document.getElementById('sideId').value = '';
    document.getElementById('importanceSlider').value = '0.5';
    document.getElementById('importanceValue').textContent = '0.50';
    
    if (sideId) {
        // Edit mode
        modalTitle.textContent = translations.editSide || 'Edit Side';
        submitButtonText.textContent = translations.updateSide || 'Update Side';
        document.getElementById('modalIcon').className = 'bi bi-pencil me-2';
        document.getElementById('sideId').value = sideId;
        
        // Load side data
        fetch(`/api/sides/${sideId}`)
            .then(response => {
                if (!response.ok) {
                    return response.json().then(err => {
                        throw new Error(err.error || `HTTP error! status: ${response.status}`);
                    });
                }
                return response.json();
            })
            .then(data => {
                if (data.success && data.side) {
                    const side = data.side;
                    document.getElementById('sideName').value = side.name || '';
                    
                    const importance = side.importance || 0.5;
                    document.getElementById('importanceSlider').value = importance;
                    document.getElementById('importanceValue').textContent = parseFloat(importance).toFixed(2);
                } else {
                    showToast((translations.errorLoadingSide || 'Error loading side') + ': ' + (data.error || (translations.unknownError || 'Unknown error')), 'error');
                    modal.hide();
                }
            })
            .catch(error => {
                console.error('Error loading side data:', error);
                showToast((translations.errorLoadingSideData || 'Error loading side data') + ': ' + error.message, 'error');
                modal.hide();
            });
    } else {
        // Add mode
        modalTitle.textContent = translations.addNewSide || 'Add New Side';
        submitButtonText.textContent = translations.createSide || 'Create Side';
        document.getElementById('modalIcon').className = 'bi bi-plus-circle me-2';
    }
    
    modal.show();
}

function submitSideForm() {
    const form = document.getElementById('sideForm');
    const sideId = document.getElementById('sideId').value;
    const formData = new FormData(form);
    
    // Validate required fields
    if (!formData.get('name')) {
        showToast(translations.pleaseFillInSideName || 'Please fill in the side name', 'warning');
        return;
    }
    
    // Convert FormData to JSON
    const data = {
        name: formData.get('name'),
        importance: parseFloat(formData.get('importance')) || 0.5
    };
    
    const url = sideId ? `/api/sides/${sideId}` : '/api/sides';
    const method = sideId ? 'PUT' : 'POST';
    
    // ✅ SECURITY: Get CSRF token
    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
    
    fetch(url, {
        method: method,
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken
        },
        body: JSON.stringify(data)
    })
    .then(response => {
        console.log('Response status:', response.status);
        // Check if response is ok (status 200-299)
        if (!response.ok) {
            return response.json().then(err => {
                console.error('Error response:', err);
                throw new Error(err.error || `HTTP error! status: ${response.status}`);
            });
        }
        return response.json();
    })
    .then(result => {
        console.log('Response result:', result);
        // Get side name from form
        const sideName = document.getElementById('sideName')?.value || 'Side';
        
        // Check if result has success field (POST/PUT responses)
        if (result && result.success !== undefined) {
            if (result.success) {
                // Show formatted success notification
                const isEdit = sideId ? true : false;
                if (window.MessageFormatter && typeof window.MessageFormatter.showCreateSuccess === 'function') {
                    if (isEdit) {
                        window.MessageFormatter.showUpdateSuccess(sideName, {
                            title: 'Side Updated',
                            duration: 4000
                        });
                    } else {
                        window.MessageFormatter.showCreateSuccess(sideName, {
                            title: 'Side Created',
                            duration: 4000
                        });
                    }
                } else {
                    showToast(
                        isEdit ? (translations.sideUpdatedSuccessfully || 'Side updated successfully!') : (translations.sideCreatedSuccessfully || 'Side created successfully!'),
                        'success'
                    );
                }
                
                // Close modal
                const modal = bootstrap.Modal.getInstance(document.getElementById('sideModal'));
                if (modal) {
                    modal.hide();
                }
                
                // Small delay to ensure modal closes before reload
                // Add cache-busting parameter to ensure fresh data
                setTimeout(() => {
                    if (window.swapNavigate) { window.swapNavigate(window.location.pathname + '?t=' + Date.now()); } else { window.location.href = window.location.pathname + '?t=' + Date.now(); }
                }, 500);
            } else {
                // Show formatted error notification
                if (window.MessageFormatter && typeof window.MessageFormatter.showCreateError === 'function') {
                    const isEdit = sideId ? true : false;
                    if (isEdit) {
                        window.MessageFormatter.showUpdateError(sideName, {
                            title: 'Update Failed',
                            detail: result.error,
                            duration: 8000
                        });
                    } else {
                        window.MessageFormatter.showCreateError(sideName, {
                            title: 'Create Failed',
                            detail: result.error,
                            duration: 8000
                        });
                    }
                } else {
                    showToast((translations.error || 'Error') + ': ' + (result.error || (translations.unknownError || 'Unknown error')), 'error');
                }
            }
        } else {
            // If no success field but response was ok, assume it worked
            console.log('No success field, but response was ok - assuming success');
            const isEdit = sideId ? true : false;
            if (window.MessageFormatter && typeof window.MessageFormatter.showCreateSuccess === 'function') {
                if (isEdit) {
                    window.MessageFormatter.showUpdateSuccess(sideName, {
                        title: 'Side Updated',
                        duration: 4000
                    });
                } else {
                    window.MessageFormatter.showCreateSuccess(sideName, {
                        title: 'Side Created',
                        duration: 4000
                    });
                }
            } else {
                showToast(
                    isEdit ? (translations.sideUpdatedSuccessfully || 'Side updated successfully!') : (translations.sideCreatedSuccessfully || 'Side created successfully!'),
                    'success'
                );
            }
            const modal = bootstrap.Modal.getInstance(document.getElementById('sideModal'));
            if (modal) {
                modal.hide();
            }
            setTimeout(() => {
                if (window.swapNavigate) { window.swapNavigate(window.location.pathname + '?t=' + Date.now()); } else { window.location.href = window.location.pathname + '?t=' + Date.now(); }
            }, 500);
        }
    })
    .catch(error => {
        console.error('Error saving side:', error);
        const sideName = document.getElementById('sideName')?.value || 'Side';
        const isEdit = sideId ? true : false;
        if (window.MessageFormatter && typeof window.MessageFormatter.showCreateError === 'function') {
            if (isEdit) {
                window.MessageFormatter.showUpdateError(sideName, {
                    title: 'Update Error',
                    duration: 6000
                });
            } else {
                window.MessageFormatter.showCreateError(sideName, {
                    title: 'Create Error',
                    duration: 6000
                });
            }
        } else {
            showToast((translations.errorSavingSide || 'Error saving side') + ': ' + error.message, 'error');
        }
    });
}

// Make functions globally accessible for onclick handlers
window.viewSide = viewSide;
window.editSide = editSide;
window.duplicateSide = duplicateSide;
window.toggleSideStatus = toggleSideStatus;
window.deleteSide = deleteSide;
window.openSideModal = openSideModal;
window.submitSideForm = submitSideForm;

// Initialize importance slider
document.addEventListener('DOMContentLoaded', function() {
    const slider = document.getElementById('importanceSlider');
    const valueDisplay = document.getElementById('importanceValue');
    
    if (slider && valueDisplay) {
        slider.addEventListener('input', (e) => {
            valueDisplay.textContent = parseFloat(e.target.value).toFixed(2);
        });
    }
    
    // Check if we should open the modal in edit mode from URL parameter
    const urlParams = new URLSearchParams(window.location.search);
    const editId = urlParams.get('edit');
    if (editId) {
        // Remove the edit parameter from URL
        urlParams.delete('edit');
        const newUrl = window.location.pathname + (urlParams.toString() ? '?' + urlParams.toString() : '');
        window.history.replaceState({}, '', newUrl);
        
        // Open modal in edit mode
        openSideModal(parseInt(editId));
    }
});

// Export default init function for universal-initializer
export default function init() {
    // The initialization is already handled in DOMContentLoaded above
    // This is just for compatibility with universal-initializer
    return Promise.resolve();
}