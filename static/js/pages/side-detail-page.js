/**
 * Side Detail Page JavaScript
 * Extracted from Side/side_detail.html
 */
import { getCSRFToken } from '../modules/core/utils.js';


// Load translations from JSON script tag
let translations = {};

document.addEventListener('DOMContentLoaded', function() {
    // Load translations from JSON script tag
    const pageDataEl = document.getElementById('side-detail-page-data');
    if (pageDataEl) {
        try {
            const data = JSON.parse(pageDataEl.textContent);
            translations = data.translations || {};
        } catch (e) {
            console.error('Error parsing side detail page data:', e);
        }
    }
    
    console.log('Side detail page loaded');
});

// ✅ SECURITY: Helper function to get CSRF token

function deleteSide(id, sideName = '') {
    const confirmMessage = sideName 
        ? `Are you sure you want to delete the side "${sideName}"?\n\nThis action cannot be undone.`
        : translations.deleteSideConfirm;
    
    if (confirm(confirmMessage)) {
        // Show processing notification
        if (window.MessageFormatter) {
            window.MessageFormatter.showNotification('delete', 'processing', { item: sideName || 'Side' });
        }
        
        fetch(`/api/sides/${id}`, {
            method: 'DELETE',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCSRFToken()
            }
        })
            .then(r => r.json())
            .then(data => {
                if (data.success) {
                    // Show formatted success notification
                    if (window.MessageFormatter) {
                        window.MessageFormatter.showDeleteSuccess(sideName || 'Side', {
                            title: 'Side Deleted',
                            duration: 4000
                        });
                    } else {
                        alert(translations.sideDeletedSuccessfully);
                    }
                    setTimeout(() => {
                        // Get URL from page data or use default
                        const pageDataEl = document.getElementById('side-detail-page-data');
                        let redirectUrl = '/sides';
                        if (pageDataEl) {
                            try {
                                const data = JSON.parse(pageDataEl.textContent);
                                redirectUrl = data.sides_list_url || '/sides';
                            } catch (e) {
                                console.warn('Error parsing page data, using default URL');
                            }
                        }
                        if (window.swapNavigate) { window.swapNavigate(redirectUrl); } else { window.location.href = redirectUrl; }
                    }, 500);
                } else {
                    // Show formatted error notification
                    if (window.MessageFormatter) {
                        window.MessageFormatter.showDeleteError(sideName || 'Side', {
                            title: 'Delete Failed',
                            detail: data.error,
                            duration: 8000
                        });
                    } else {
                        alert(translations.error + ': ' + data.error);
                    }
                }
            })
            .catch(e => {
                if (window.MessageFormatter) {
                    window.MessageFormatter.showDeleteError(sideName || 'Side', {
                        title: 'Delete Error',
                        duration: 6000
                    });
                } else {
                    alert(translations.error + ': ' + e.message);
                }
            });
    }
}
// DETL-01: referenced by the template's inline onclick handler; this module is
// loaded as ES module, so top-level functions are module-scoped by default.
window.deleteSide = deleteSide;
