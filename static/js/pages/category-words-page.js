/**
 * Words in Category Page JavaScript
 *
 * This list is a server-sorted, server-paged table now: the page keeps the
 * add-word modal, the remove action and the word search, and asks the server
 * to search, order and page the list by navigating with query parameters.
 * The unified table script owns the header sort (default server navigation).
 */
import { getCSRFToken, escapeHtml } from '../modules/core/utils.js';

// Use window.translations to avoid conflicts with other scripts
if (typeof window.translations === 'undefined') {
    window.translations = {};
}

let translations = window.translations;

document.addEventListener('DOMContentLoaded', function() {
    const pageDataEl = document.getElementById('category-words-page-data');
    if (pageDataEl) {
        try {
            const data = JSON.parse(pageDataEl.textContent);
            Object.assign(window.translations, data.translations || {});
            translations = window.translations;
        } catch (e) {
            console.error('Error parsing category words page data:', e);
            translations = window.translations || {};
        }
    }

    initializeEventListeners();
    console.log('Category words page loaded');
});

function initializeEventListeners() {
    // Search: navigates with ?search=, which re-renders the list server-side
    const searchInput = document.getElementById('wordSearch');
    if (searchInput) {
        searchInput.addEventListener('keydown', function(e) {
            if (e.key === 'Enter') {
                e.preventDefault();
                navigateCategoryWords({ search: searchInput.value.trim(), page: '1' });
            }
        });
    }

    // Event delegation for remove word buttons
    document.addEventListener('click', function(e) {
        const removeBtn = e.target.closest('.remove-word-btn');
        if (removeBtn) {
            e.preventDefault();
            const categoryId = parseInt(removeBtn.getAttribute('data-category-id'));
            const wordId = parseInt(removeBtn.getAttribute('data-word-id'));
            let wordName = removeBtn.getAttribute('data-word-name');

            // Parse JSON string if needed
            try {
                if (wordName && (wordName.startsWith('"') || wordName.startsWith("'"))) {
                    wordName = JSON.parse(wordName);
                }
            } catch (err) {
                // Use as-is if parsing fails
            }

            removeWordFromCategory(categoryId, wordId, wordName);
        }
    });
}

// Navigate to this list with changed query parameters; everything not named
// here (sort, order, per page) is carried over.
function navigateCategoryWords(changes) {
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
function changeCategoryWordsPageSize() {
    const perPage = document.getElementById('categoryWordsPerPage');
    if (perPage) {
        navigateCategoryWords({ per_page: perPage.value, page: '1' });
    }
}

// Clear the search box and the search itself
function clearSearch() {
    const searchInput = document.getElementById('wordSearch');
    if (searchInput) searchInput.value = '';
    navigateCategoryWords({ search: '', page: '1' });
}

// Debounce helper
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

// Get category ID from page data
function getCategoryId() {
    const pageDataEl = document.getElementById('category-words-page-data');
    if (pageDataEl) {
        try {
            const data = JSON.parse(pageDataEl.textContent);
            return data.category_id;
        } catch (e) {
            console.error('Error parsing category ID:', e);
        }
    }
    return 0;
}

// CSRF token helper functions

async function getCSRFTokenAsync() {
    const metaToken = document.querySelector('meta[name="csrf-token"]');
    if (metaToken) {
        const token = metaToken.getAttribute('content');
        if (token) return token;
    }
    
    try {
        const response = await fetch('/api/csrf-token');
        if (!response.ok) {
            console.warn('Failed to fetch CSRF token from API');
            return '';
        }
        const data = await response.json();
        return data.csrf_token || '';
    } catch (error) {
        console.error('Error fetching CSRF token:', error);
        return '';
    }
}

// Toast notification helper
function showToast(message, type = 'info', duration = 4000) {
    let toastContainer = (document.querySelector('[data-toast-region]') || document.getElementById('toastContainer'));
    if (!toastContainer) {
        toastContainer = document.createElement('div');
        toastContainer.id = 'toastContainer';
        toastContainer.className = 'toast-container position-fixed top-0 end-0 p-3';
        toastContainer.style.zIndex = '9999';
        document.body.appendChild(toastContainer);
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
        <div id="${toastId}" class="toast align-items-center text-white bg-${bgColors[type]} border-0" role="alert">
            <div class="d-flex">
                <div class="toast-body">
                    <i class="bi bi-${icons[type]} me-2"></i>
                    ${escapeHtml(message)}
                </div>
                <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
            </div>
        </div>
    `;
    
    toastContainer.insertAdjacentHTML('beforeend', toastHtml);
    const toastElement = document.getElementById(toastId);
    const toast = new bootstrap.Toast(toastElement, { delay: duration });
    toast.show();
    
    toastElement.addEventListener('hidden.bs.toast', () => {
        toastElement.remove();
    });
}

// Remove word from category
async function removeWordFromCategory(categoryId, wordId, wordName) {
    // Handle JSON-encoded word name
    let displayName = wordName;
    try {
        if (typeof wordName === 'string' && (wordName.startsWith('"') || wordName.startsWith("'"))) {
            displayName = JSON.parse(wordName);
        }
    } catch (e) {
        displayName = wordName;
    }
    
    if (!confirm(translations.confirmRemove || `Are you sure you want to remove "${displayName}" from this category?`)) {
        return;
    }
    
    // Get CSRF token
    let csrfToken = getCSRFToken();
    if (!csrfToken) {
        csrfToken = await getCSRFTokenAsync();
    }
    
    if (!csrfToken) {
        showToast('Unable to obtain CSRF token. Please refresh the page.', 'error');
        return;
    }
    
    fetch(`/api/categories/${categoryId}/words/${wordId}`, {
        method: 'DELETE',
        headers: {
            'X-CSRFToken': csrfToken
        }
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            showToast(data.message || translations.wordRemoved || 'Word removed from category successfully', 'success');
            // The list is the server's; reload it without the removed word.
            setTimeout(() => {
                if (window.swapNavigate) { window.swapNavigate(window.location.href); } else { window.location.reload(); }
            }, 800);
        } else {
            showToast(data.error || translations.error || 'Error', 'error');
        }
    })
    .catch(error => {
        console.error('Error removing word from category:', error);
        showToast('Error removing word from category: ' + error.message, 'error');
    });
}

// Word search state
let wordSearchTimeout = null;
let currentWordSearchResults = [];
let selectedWordId = null;
let selectedWordText = null;
let clickOutsideHandler = null;
let inputHandler = null;
let keydownHandler = null;

// Open add word modal
function openAddWordModal() {
    const modalElement = document.getElementById('addWordToCategoryModal');
    if (!modalElement) {
        console.error('Add word modal not found');
        return;
    }
    
    const modal = new bootstrap.Modal(modalElement);
    const modalTitle = document.getElementById('addWordToCategoryModalLabel');
    const categoryIdInput = document.getElementById('targetCategoryId');
    const wordInput = document.getElementById('wordInput');
    const wordResults = document.getElementById('wordSearchResults');
    const selectedWordIdInput = document.getElementById('selectedWordId');
    
    if (!categoryIdInput || !wordInput || !wordResults || !selectedWordIdInput) {
        console.error('Required modal elements not found');
        return;
    }
    
    // Clean up any existing handlers first
    if (clickOutsideHandler) {
        document.removeEventListener('click', clickOutsideHandler, true);
        clickOutsideHandler = null;
    }
    if (inputHandler && wordInput) {
        wordInput.removeEventListener('input', inputHandler);
        inputHandler = null;
    }
    if (keydownHandler && wordInput) {
        wordInput.removeEventListener('keydown', keydownHandler);
        keydownHandler = null;
    }
    if (wordSearchTimeout) {
        clearTimeout(wordSearchTimeout);
        wordSearchTimeout = null;
    }
    
    // Reset state
    selectedWordId = null;
    selectedWordText = null;
    currentWordSearchResults = [];
    wordInput.value = '';
    selectedWordIdInput.value = '';
    wordResults.style.display = 'none';
    wordResults.innerHTML = '';
    
    // Update modal title with category name
    const pageDataEl = document.getElementById('category-words-page-data');
    if (pageDataEl && modalTitle) {
        try {
            const data = JSON.parse(pageDataEl.textContent);
            const categoryName = data.category_name || '';
            modalTitle.textContent = `${translations.addWordToCategory || 'Add Word to Category'}: ${categoryName}`;
        } catch (e) {
            console.error('Error parsing category name:', e);
        }
    }
    
    // Initialize word search functionality
    function initializeWordSearch() {
        // Clear any existing timeouts
        if (wordSearchTimeout) {
            clearTimeout(wordSearchTimeout);
            wordSearchTimeout = null;
        }
        
        // Remove existing listeners if any
        if (inputHandler) {
            wordInput.removeEventListener('input', inputHandler);
        }
        if (keydownHandler) {
            wordInput.removeEventListener('keydown', keydownHandler);
        }
        
        // Handle input changes
        inputHandler = function(e) {
            const searchTerm = e.target.value.trim();
            
            // Clear previous timeout
            if (wordSearchTimeout) {
                clearTimeout(wordSearchTimeout);
            }
            
            // Reset selection
            selectedWordId = null;
            selectedWordText = null;
            selectedWordIdInput.value = '';
            
            if (searchTerm.length === 0) {
                wordResults.style.display = 'none';
                wordResults.innerHTML = '';
                return;
            }
            
            // Debounce search
            wordSearchTimeout = setTimeout(() => {
                searchWords(searchTerm);
            }, 300);
        };
        wordInput.addEventListener('input', inputHandler);
        
        // Handle keyboard navigation
        keydownHandler = function(e) {
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                const firstItem = wordResults.querySelector('.word-result-item');
                if (firstItem) {
                    firstItem.focus();
                    firstItem.classList.add('active');
                }
            } else if (e.key === 'Escape') {
                wordResults.style.display = 'none';
            } else if (e.key === 'Enter' && selectedWordId) {
                e.preventDefault();
                // Word already selected, will be handled by form submit
            }
        };
        wordInput.addEventListener('keydown', keydownHandler);
        
        // Focus input when modal opens
        setTimeout(() => {
            wordInput.focus();
        }, 300);
    }
    
    // Search words function
    async function searchWords(searchTerm) {
        if (!searchTerm || searchTerm.length === 0) {
            wordResults.style.display = 'none';
            return;
        }
        
        try {
            const categoryId = getCategoryId();
            const params = new URLSearchParams({
                q: searchTerm,
                page: 1,
                per_page: 20
            });
            
            if (categoryId) {
                params.append('exclude_category_id', categoryId);
            }
            
            const response = await fetch(`/api/words/search?${params.toString()}`);
            const data = await response.json();
            
            if (data.results && data.results.length > 0) {
                currentWordSearchResults = data.results;
                renderWordResults(data.results, searchTerm);
            } else {
                // Show option to create new word
                renderWordResults([], searchTerm);
            }
        } catch (error) {
            console.error('Error searching words:', error);
            wordResults.style.display = 'none';
        }
    }
    
    // Render search results
    function renderWordResults(results, searchTerm) {
        wordResults.innerHTML = '';
        
        if (results.length > 0) {
            results.forEach((item, index) => {
                const wordId = item.id || item.word_id;
                const wordText = item.text || item.word || '';
                const usageCount = item.usage_count || 0;
                
                const itemDiv = document.createElement('div');
                itemDiv.className = 'word-result-item';
                itemDiv.tabIndex = 0;
                itemDiv.setAttribute('data-word-id', wordId);
                itemDiv.setAttribute('data-word-text', wordText);
                
                itemDiv.innerHTML = `
                    <div class="d-flex align-items-center">
                        <i class="bi bi-check-circle me-2 text-primary"></i>
                        <span class="word-text">${escapeHtml(wordText)}</span>
                        ${usageCount > 0 ? `<small class="text-muted ms-2">(${usageCount} files)</small>` : ''}
                    </div>
                `;
                
                itemDiv.addEventListener('click', function() {
                    selectWord(wordId, wordText);
                });
                
                itemDiv.addEventListener('keydown', function(e) {
                    if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        selectWord(wordId, wordText);
                    } else if (e.key === 'ArrowDown') {
                        e.preventDefault();
                        const next = wordResults.querySelectorAll('.word-result-item')[index + 1];
                        if (next) {
                            itemDiv.classList.remove('active');
                            next.focus();
                            next.classList.add('active');
                        }
                    } else if (e.key === 'ArrowUp') {
                        e.preventDefault();
                        if (index > 0) {
                            const prev = wordResults.querySelectorAll('.word-result-item')[index - 1];
                            itemDiv.classList.remove('active');
                            if (prev) {
                                prev.focus();
                                prev.classList.add('active');
                            } else {
                                wordInput.focus();
                            }
                        }
                    }
                });
                
                wordResults.appendChild(itemDiv);
            });
        }
        
        // Always show option to create new word if search term doesn't match exactly
        const exactMatch = results.some(item => {
            const wordText = (item.text || item.word || '').toLowerCase();
            return wordText === searchTerm.toLowerCase();
        });
        
        if (!exactMatch && searchTerm.length > 0 && !searchTerm.match(/^\d+$/)) {
            const createDiv = document.createElement('div');
            createDiv.className = 'word-result-item word-result-create';
            createDiv.tabIndex = 0;
            createDiv.setAttribute('data-word-id', 'new');
            createDiv.setAttribute('data-word-text', searchTerm);
            
            createDiv.innerHTML = `
                <div class="d-flex align-items-center">
                    <i class="bi bi-plus-circle me-2 text-success"></i>
                    <span class="word-text">${escapeHtml(searchTerm)}</span>
                    <small class="text-muted ms-2">(create new)</small>
                </div>
            `;
            
            createDiv.addEventListener('click', function() {
                selectWord('new', searchTerm);
            });
            
            createDiv.addEventListener('keydown', function(e) {
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    selectWord('new', searchTerm);
                }
            });
            
            wordResults.appendChild(createDiv);
        }
        
        wordResults.style.display = 'block';
    }
    
    // Select word function
    function selectWord(wordId, wordText) {
        selectedWordId = wordId;
        selectedWordText = wordText;
        
        if (wordId === 'new') {
            selectedWordIdInput.value = '';
            wordInput.value = wordText;
        } else {
            selectedWordIdInput.value = wordId;
            wordInput.value = wordText;
        }
        
        wordResults.style.display = 'none';
        wordInput.focus();
    }
    
    // Show modal and initialize
    modal.show();
    
    // Wait for modal to be fully shown
    modalElement.addEventListener('shown.bs.modal', function onShown() {
        modalElement.removeEventListener('shown.bs.modal', onShown);
        initializeWordSearch();
        
        // Add click outside handler after initialization
        const inputContainer = wordInput.closest('.position-relative') || wordInput.parentElement;
        clickOutsideHandler = function(e) {
            const target = e.target;
            if (inputContainer && wordResults && 
                !inputContainer.contains(target) && 
                !wordResults.contains(target)) {
                wordResults.style.display = 'none';
            }
        };
        // Use capture phase to catch clicks before they bubble
        setTimeout(() => {
            document.addEventListener('click', clickOutsideHandler, true);
        }, 100);
    }, { once: true });
    
    // Clean up when modal is hidden
    modalElement.addEventListener('hidden.bs.modal', function onHidden() {
        // Clean up event listeners
        if (clickOutsideHandler) {
            document.removeEventListener('click', clickOutsideHandler, true);
            clickOutsideHandler = null;
        }
        
        // Clear search timeout
        if (wordSearchTimeout) {
            clearTimeout(wordSearchTimeout);
            wordSearchTimeout = null;
        }
        
        // Remove input event listeners
        if (inputHandler && wordInput) {
            wordInput.removeEventListener('input', inputHandler);
            inputHandler = null;
        }
        if (keydownHandler && wordInput) {
            wordInput.removeEventListener('keydown', keydownHandler);
            keydownHandler = null;
        }
        
        // Reset form
        if (wordInput) wordInput.value = '';
        if (selectedWordIdInput) selectedWordIdInput.value = '';
        if (wordResults) {
            wordResults.style.display = 'none';
            wordResults.innerHTML = '';
        }
        
        // Reset state
        selectedWordId = null;
        selectedWordText = null;
        currentWordSearchResults = [];
    }, { once: true });
}

// Create a new word
async function createNewWord(wordText) {
    const csrfToken = getCSRFToken() || await getCSRFTokenAsync();
    if (!csrfToken) {
        throw new Error('Unable to obtain CSRF token');
    }
    
    const response = await fetch('/api/words', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken
        },
        body: JSON.stringify({
            word: wordText.trim(),
            csrf_token: csrfToken
        })
    });
    
    const data = await response.json();
    if (data.success) {
        return data.id;
    } else {
        throw new Error(data.error || 'Failed to create word');
    }
}

// Save word to category
async function saveWordToCategory() {
    const categoryIdInput = document.getElementById('targetCategoryId');
    const wordInput = document.getElementById('wordInput');
    const selectedWordIdInput = document.getElementById('selectedWordId');
    
    if (!categoryIdInput || !wordInput || !selectedWordIdInput) {
        showToast('Form elements not found', 'error');
        return;
    }
    
    const categoryId = categoryIdInput.value;
    const wordText = wordInput.value.trim();
    
    if (!wordText) {
        showToast('Please enter or select a word', 'warning');
        return;
    }
    
    // Get word ID - either from selection or create new
    let wordId;
    const selectedId = selectedWordIdInput.value;
    
    if (selectedId === 'new' || (!selectedId && wordText)) {
        // This is a new word, create it first
        try {
            showToast('Creating new word...', 'info');
            wordId = await createNewWord(wordText);
            showToast('Word created successfully', 'success');
        } catch (error) {
            console.error('Error creating word:', error);
            showToast('Error creating word: ' + error.message, 'error');
            return;
        }
    } else if (selectedId) {
        // Existing word, use the ID
        wordId = parseInt(selectedId);
        if (isNaN(wordId)) {
            showToast('Invalid word selection', 'error');
            return;
        }
    } else {
        // Try to find word by text
        try {
            const response = await fetch(`/api/words/search?q=${encodeURIComponent(wordText)}&per_page=1`);
            const data = await response.json();
            if (data.results && data.results.length > 0 && data.results[0].word.toLowerCase() === wordText.toLowerCase()) {
                wordId = data.results[0].id;
            } else {
                // Create new word
                showToast('Creating new word...', 'info');
                wordId = await createNewWord(wordText);
                showToast('Word created successfully', 'success');
            }
        } catch (error) {
            console.error('Error finding/creating word:', error);
            showToast('Error processing word: ' + error.message, 'error');
            return;
        }
    }
    
    // Get CSRF token
    let csrfToken = getCSRFToken();
    if (!csrfToken) {
        csrfToken = await getCSRFTokenAsync();
    }
    
    if (!csrfToken) {
        showToast('Unable to obtain CSRF token. Please refresh the page.', 'error');
        return;
    }
    
    fetch('/api/words-categorys/add', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken
        },
        body: JSON.stringify({
            word_id: wordId,
            category_id: parseInt(categoryId),
            csrf_token: csrfToken
        })
    })
    .then(response => response.json())
    .then(async data => {
        if (data.success) {
            showToast(data.message || translations.wordAdded || 'Word added to category successfully', 'success');
            
            // Clean up modal and event listeners first
            const modalElement = document.getElementById('addWordToCategoryModal');
            if (modalElement) {
                const modal = bootstrap.Modal.getInstance(modalElement);
                if (modal) {
                    // Clean up event listeners
                    if (clickOutsideHandler) {
                        document.removeEventListener('click', clickOutsideHandler, true);
                        clickOutsideHandler = null;
                    }
                    if (wordSearchTimeout) {
                        clearTimeout(wordSearchTimeout);
                        wordSearchTimeout = null;
                    }
                    
                    // Clear form
                    const wordInput = document.getElementById('wordInput');
                    const selectedWordIdInput = document.getElementById('selectedWordId');
                    const wordResults = document.getElementById('wordSearchResults');
                    if (wordInput) {
                        wordInput.value = '';
                        // Remove event listeners
                        const newInput = wordInput.cloneNode(true);
                        wordInput.parentNode.replaceChild(newInput, wordInput);
                    }
                    if (selectedWordIdInput) selectedWordIdInput.value = '';
                    if (wordResults) {
                        wordResults.style.display = 'none';
                        wordResults.innerHTML = '';
                    }
                    
                    // Hide modal
                    modal.hide();
                }
            }
            
            // Add word to list immediately without reload
            try {
                await addWordToList(wordId, wordText);
            } catch (error) {
                console.error('Error adding word to list:', error);
                // Fallback to reload if dynamic update fails
                setTimeout(() => {
                    if (window.swapNavigate) { window.swapNavigate(window.location.href); } else { window.location.reload(); }
                }, 1000);
            }
        } else {
            showToast(data.error || translations.error || 'Error', 'error');
        }
    })
    .catch(error => {
        console.error('Error adding word to category:', error);
        showToast('Error adding word to category: ' + error.message, 'error');
    });
}

// Add word to list dynamically
async function addWordToList(wordId, wordText) {
    // The list is the server's; a fresh page shows the word in its order.
    if (window.swapNavigate) { window.swapNavigate(window.location.href); } else { window.location.reload(); }
}

// Helper function to escape HTML

// Make functions available globally (must be done at module load time)
// This ensures onclick handlers in HTML can access them
if (typeof window !== 'undefined') {
    window.removeWordFromCategory = removeWordFromCategory;
    window.openAddWordModal = openAddWordModal;
    window.saveWordToCategory = saveWordToCategory;
    window.changeCategoryWordsPageSize = changeCategoryWordsPageSize;
    window.clearSearch = clearSearch;
}
