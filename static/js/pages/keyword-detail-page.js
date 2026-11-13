/**
 * Keyword Detail Page JavaScript
 * Extracted from Keyword/keyword_detail.html
 */
import { getCSRFToken } from '../modules/core/utils.js';


// Load translations from JSON script tag
let translations = {};

document.addEventListener('DOMContentLoaded', function() {
    // Load translations from JSON script tag
    const pageDataEl = document.getElementById('keyword-detail-page-data');
    if (pageDataEl) {
        try {
            const data = JSON.parse(pageDataEl.textContent);
            translations = data.translations || {};
        } catch (e) {
            console.error('Error parsing keyword detail page data:', e);
        }
    }
    
    console.log('Keyword detail page loaded');
});

// ✅ SECURITY: Helper function to get CSRF token

function deleteKeyword(id) {
    if (confirm(translations.deleteConfirm)) {
        fetch(`/api/keywords/${id}`, {
            method: 'DELETE',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCSRFToken()
            }
        })
            .then(r => r.json())
            .then(data => {
                if (data.success) {
                    alert(translations.keywordDeletedSuccessfully);
                    // Get URL from page data or use default
                    const pageDataEl = document.getElementById('keyword-detail-page-data');
                    let redirectUrl = '/keywords';
                    if (pageDataEl) {
                        try {
                            const data = JSON.parse(pageDataEl.textContent);
                            redirectUrl = data.keywords_list_url || '/keywords';
                        } catch (e) {
                            console.warn('Error parsing page data, using default URL');
                        }
                    }
                    if (window.swapNavigate) { window.swapNavigate(redirectUrl); } else { window.location.href = redirectUrl; }
                } else {
                    alert(translations.error + ': ' + data.error);
                }
            })
            .catch(e => alert(translations.error + ': ' + e.message));
    }
}
// DETL-01: referenced by the template's inline onclick handler; this module is
// loaded as ES module, so top-level functions are module-scoped by default.
window.deleteKeyword = deleteKeyword;
