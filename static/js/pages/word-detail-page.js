/**
 * Word Detail Page JavaScript
 * Extracted from Word/Word_detail.html
 * 
 * TODO: Copy all inline JavaScript from Word_detail.html into this file
 */
import { getCSRFToken } from '../modules/core/utils.js';


// Load translations from JSON script tag
let translations = {};

document.addEventListener('DOMContentLoaded', function() {
    // Load translations from JSON script tag
    const pageDataEl = document.getElementById('word-detail-page-data');
    if (pageDataEl) {
        try {
            const data = JSON.parse(pageDataEl.textContent);
            translations = data.translations || {};
            // Also make available on window for backward compatibility
            window.translations = window.translations || {};
            Object.assign(window.translations, translations);
        } catch (e) {
            console.error('Error parsing word detail page data:', e);
        }
    }
    
    console.log('Word detail page loaded');
});

// ✅ SECURITY: Helper function to get CSRF token

function editWord(id) {
    // Navigate to words list page - user can edit from there
    // Or we could pass a parameter to auto-trigger edit, but for simplicity just navigate
    // Get URL from page data or use default
    const pageDataEl = document.getElementById('word-detail-page-data');
    let redirectUrl = '/words';
    if (pageDataEl) {
        try {
            const data = JSON.parse(pageDataEl.textContent);
            redirectUrl = data.words_list_url || '/words';
        } catch (e) {
            console.warn('Error parsing page data, using default URL');
        }
    }
    if (window.swapNavigate) { window.swapNavigate(redirectUrl); } else { window.location.href = redirectUrl; }
}

function deleteWord(id) {
    if (!confirm(translations.deleteConfirm)) return;
    
    fetch(`/api/words/${id}`, {
        method: 'DELETE',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCSRFToken()
        }
    })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                alert(translations.wordDeletedSuccessfully);
                // Get URL from page data or use default
                const pageDataEl = document.getElementById('word-detail-page-data');
                let redirectUrl = '/words';
                if (pageDataEl) {
                    try {
                        const data = JSON.parse(pageDataEl.textContent);
                        redirectUrl = data.words_list_url || '/words';
                    } catch (e) {
                        console.warn('Error parsing page data, using default URL');
                    }
                }
                if (window.swapNavigate) { window.swapNavigate(redirectUrl); } else { window.location.href = redirectUrl; }
            } else {
                alert(translations.error + ': ' + (data.error || 'Unknown error'));
            }
        })
        .catch(e => {
            console.error('Error deleting word:', e);
            alert(translations.error + ': ' + e.message);
        });
}
// DETL-01: referenced by the template's inline onclick handler; this module is
// loaded as ES module, so top-level functions are module-scoped by default.
window.deleteWord = deleteWord;
