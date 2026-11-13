/**
 * Source Detail Page JavaScript
 * Extracted from Sources/source_detail.html
 * 
 * TODO: Copy all inline JavaScript from source_detail.html into this file
 */
import { getCSRFToken } from '../modules/core/utils.js';


// Load translations from JSON script tag
let translations = {};

document.addEventListener('DOMContentLoaded', function() {
    // Load translations from JSON script tag
    const pageDataEl = document.getElementById('source-detail-page-data');
    if (pageDataEl) {
        try {
            const data = JSON.parse(pageDataEl.textContent);
            translations = data.translations || {};
            // Also make available on window for backward compatibility
            window.translations = window.translations || {};
            Object.assign(window.translations, translations);
        } catch (e) {
            console.error('Error parsing source detail page data:', e);
        }
    }
    
    console.log('Source detail page loaded');
});

// ✅ SECURITY: Helper function to get CSRF token

function deleteSource(id, sourceName = '') {
    const confirmMessage = sourceName 
        ? `Are you sure you want to delete the source "${sourceName}"?\n\nThis action cannot be undone.`
        : translations.deleteSourceConfirm;
    
    if (confirm(confirmMessage)) {
        // Show processing notification
        if (window.MessageFormatter) {
            window.MessageFormatter.showNotification('delete', 'processing', { item: sourceName || 'Source' });
        }
        
        fetch(`/api/sources/${id}`, {
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
                        window.MessageFormatter.showDeleteSuccess(sourceName || 'Source', {
                            title: 'Source Deleted',
                            duration: 4000
                        });
                    } else {
                        alert(translations.sourceDeletedSuccessfully);
                    }
                    setTimeout(() => {
                        // Get URL from page data or use default
                        const pageDataEl = document.getElementById('source-detail-page-data');
                        let redirectUrl = '/sources';
                        if (pageDataEl) {
                            try {
                                const data = JSON.parse(pageDataEl.textContent);
                                redirectUrl = data.sources_list_url || '/sources';
                            } catch (e) {
                                console.warn('Error parsing page data, using default URL');
                            }
                        }
                        if (window.swapNavigate) { window.swapNavigate(redirectUrl); } else { window.location.href = redirectUrl; }
                    }, 500);
                } else {
                    // Show formatted error notification
                    if (window.MessageFormatter) {
                        window.MessageFormatter.showDeleteError(sourceName || 'Source', {
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
                    window.MessageFormatter.showDeleteError(sourceName || 'Source', {
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
window.deleteSource = deleteSource;
