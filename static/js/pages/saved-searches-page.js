/**
 * Saved Searches Page JavaScript
 * Wired to the real saved-search API (GET/PUT/DELETE /api/search/saved[/<id>]).
 */
import { getCSRFToken } from '../modules/core/utils.js';


// Load translations from JSON script tag
let translations = {};

document.addEventListener('DOMContentLoaded', function() {
    const pageDataEl = document.getElementById('saved-searches-page-data');
    if (pageDataEl) {
        try {
            const data = JSON.parse(pageDataEl.textContent);
            translations = data.translations || {};
            window.translations = window.translations || {};
            Object.assign(window.translations, translations);
        } catch (e) {
            console.error('Error parsing saved searches page data:', e);
        }
    }

    console.log('Saved searches page loaded');
});

// Client-side filter by saved-search name
function filterSavedSearches() {
    const searchTerm = (document.getElementById('searchSaved')?.value || '').toLowerCase();
    document.querySelectorAll('.search-card').forEach(card => {
        const name = card.dataset.name || '';
        card.style.display = name.includes(searchTerm) ? '' : 'none';
    });
}

// Rename a saved search (Edit action)
async function renameSavedSearch(searchId, currentName) {
    // alert-replacement.js overrides window.prompt with a Promise-based modal,
    // so this MUST be awaited: calling .trim() on the Promise threw
    // "name.trim is not a function" and the saved search never renamed.
    const newName = await prompt(translations.renamePrompt || 'Enter a new name for this search:', currentName);
    if (newName === null || newName === undefined) return; // cancelled
    const trimmed = (typeof newName === 'string' ? newName : '').trim();
    if (!trimmed) return;
    if (trimmed === currentName) return;

    try {
        const response = await fetch(`/api/search/saved/${searchId}`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCSRFToken()
            },
            body: JSON.stringify({ name: trimmed })
        });

        if (response.ok) {
            if (window.swapNavigate) { window.swapNavigate(window.location.pathname + '?t=' + Date.now()); } else { window.location.href = window.location.pathname + '?t=' + Date.now(); }
        } else {
            const err = await response.json().catch(() => ({}));
            alert((translations.error || 'Error') + ': ' + (err.error || translations.unknownError || 'Unknown error'));
        }
    } catch (error) {
        alert((translations.error || 'Error') + ': ' + error.message);
    }
}

// Delete a saved search
async function deleteSavedSearch(searchId) {
    if (!confirm(translations.deleteSearchConfirm || 'Are you sure you want to delete this saved search?')) {
        return;
    }

    try {
        const response = await fetch(`/api/search/saved/${searchId}`, {
            method: 'DELETE',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCSRFToken()
            }
        });

        if (response.ok) {
            if (window.swapNavigate) { window.swapNavigate(window.location.pathname + '?t=' + Date.now()); } else { window.location.href = window.location.pathname + '?t=' + Date.now(); }
        } else {
            const err = await response.json().catch(() => ({}));
            alert((translations.error || 'Error') + ': ' + (err.error || translations.unknownError || 'Unknown error'));
        }
    } catch (error) {
        alert((translations.error || 'Error') + ': ' + error.message);
    }
}

// Expose for inline onclick handlers (this file is loaded as a module,
// so top-level function declarations are module-scoped by default).
window.filterSavedSearches = filterSavedSearches;
window.renameSavedSearch = renameSavedSearch;
window.deleteSavedSearch = deleteSavedSearch;

// Export default init function for universal-initializer
export default function init() {
    return Promise.resolve();
}
