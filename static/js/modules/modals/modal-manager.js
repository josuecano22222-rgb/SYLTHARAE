/**
 * Modal Manager
 * Centralized modal open/close functionality and item management
 */

import notificationSystem from '../ui/notifications.js';
import { translations } from '../core/config.js';
import { getCSRFToken, escapeHtml } from '../core/utils.js';
import { apiPost, apiGet } from '../api/api-client.js';
import { endpoints } from '../api/endpoints.js';
import * as select2Initializers from './select2-initializers.js';

/**
 * Modal state for add item modals
 */
export const addItemModalState = {
    source: { page: 1, perPage: 10, search: '', total: 0, totalPages: 0 },
    side: { page: 1, perPage: 10, search: '', total: 0, totalPages: 0 },
    keyword: { page: 1, perPage: 10, search: '', total: 0, totalPages: 0 },
    category: { page: 1, perPage: 10, search: '', total: 0, totalPages: 0 }
};

/**
 * Search timeout for debouncing search inputs
 */
let searchTimeout = null;

/**
 * Open add item modal
 * @param {string} modalId - Modal element ID
 * @param {string} section - Section name
 */
export function openAddItemModal(modalId, section) {
    console.log('openAddItemModal called', { modalId, section });
    const modal = document.getElementById(modalId);
    if (!modal) {
        console.error(`Modal ${modalId} not found`);
        notificationSystem.error(`Modal ${modalId} not found`);
        return;
    }
    
    // Check if it's a Bootstrap modal (has 'modal fade' classes)
    const isBootstrapModal = modal.classList.contains('modal') && modal.classList.contains('fade');
    
    if (isBootstrapModal) {
        // Handle Bootstrap modals
        if (typeof bootstrap !== 'undefined' && bootstrap.Modal) {
            const bsModal = new bootstrap.Modal(modal);
            
            // Initialize modal based on section
            if (section === 'category') {
                // Use new Bootstrap modal with search
                if (typeof window.openAddCategoryModal === 'function') {
                    window.openAddCategoryModal();
                    return;
                }
            } else if (section === 'words_categorys') {
                // Use new Bootstrap modal with search
                if (typeof window.openAddWordsCategorysModal === 'function') {
                    window.openAddWordsCategorysModal();
                    return;
                }
            } else if (section === 'keyword' || section === 'keywords') {
                // Use new Bootstrap modal with search
                if (typeof window.openAddKeywordModalFromArchives === 'function') {
                    window.openAddKeywordModalFromArchives();
                    return;
                }
            }
            
            // Default Bootstrap modal show
            bsModal.show();
            
            // Focus trap
            setTimeout(() => {
                const firstInput = modal.querySelector('input[type="text"], input[type="number"], select, textarea');
                if (firstInput) {
                    firstInput.focus();
                }
            }, 300);
        } else {
            console.error('Bootstrap not available');
            notificationSystem.error('Bootstrap modal library not loaded');
        }
        return;
    }
    
    // Legacy archives-modal handling
    // Show modal with proper animation
    modal.style.display = 'flex';
    void modal.offsetWidth; // Force reflow
    modal.classList.add('active');
    modal.style.visibility = 'visible';
    modal.style.opacity = '1';
    document.body.style.overflow = 'hidden';
    
    // Focus trap
    setTimeout(() => {
        const firstInput = modal.querySelector('input[type="text"], input[type="number"], select, textarea');
        if (firstInput) {
            firstInput.focus();
        }
    }, 100);

    // Initialize modal based on section
    if (section === 'category') {
        // Initialize Select2 for category modal
        select2Initializers.initializeCategoryWordSelect();
        loadItemsForModal(section);
    } else if (section === 'keyword' || section === 'keywords') {
        // Initialize Select2 for keyword modal
        select2Initializers.initializeKeywordWordsSelect();
        select2Initializers.initializeKeywordCategorySelect();
        loadItemsForModal(section === 'keywords' ? 'keyword' : section);
    } else if (section === 'words_categorys') {
        // Initialize Select2 for words_categorys modal
        select2Initializers.initializeWordsCategorysWordSelect();
        select2Initializers.initializeWordsCategorysCategorySelect();
    } else {
        // Load items for the section
        loadItemsForModal(section);
    }
}

/**
 * Close add item modal
 * @param {string} modalId - Modal element ID
 */
export function closeAddItemModal(modalId) {
    const modal = document.getElementById(modalId);
    if (!modal) return;

    // Check if it's a Bootstrap modal
    const isBootstrapModal = modal.classList.contains('modal') && modal.classList.contains('fade');
    
    if (isBootstrapModal) {
        // Handle Bootstrap modal close
        if (typeof bootstrap !== 'undefined' && bootstrap.Modal) {
            const bsModal = bootstrap.Modal.getInstance(modal);
            if (bsModal) {
                bsModal.hide();
            }
        }
        return;
    }

    // Legacy archives-modal handling
    // Remove active class first for animation
    modal.classList.remove('active');
    
    // Wait for animation to complete before hiding
    setTimeout(() => {
        modal.style.display = 'none';
        modal.style.visibility = 'hidden';
        modal.style.opacity = '0';
    }, 300);
    
    document.body.style.overflow = '';

    // Reset form
    const form = modal.querySelector('.add-item-form');
    if (form) {
        if (form.tagName === 'FORM' && typeof form.reset === 'function') {
            form.reset();
        } else {
            // For div-based forms, manually clear Select2 fields and inputs
            const selects = form.querySelectorAll('select');
            selects.forEach(select => {
                if (window.$ && $(select).hasClass('select2-hidden-accessible')) {
                    $(select).val(null).trigger('change');
                } else {
                    select.value = '';
                }
            });
            const inputs = form.querySelectorAll('input[type="text"], input[type="number"], input[type="email"], textarea');
            inputs.forEach(input => {
                input.value = '';
            });
        }
    }

    // Destroy Select2 instances
    if (window.$) {
        ['#categoryName', '#keywordWords', '#keywordCategory', '#wordsCategorysWordId', '#wordsCategorysCategoryId'].forEach(selector => {
            const $el = $(selector);
            if ($el.hasClass('select2-hidden-accessible')) {
                $el.select2('destroy');
            }
        });
    }

    // Reset state
    const sectionKey = modalId.replace('add', '').replace('Modal', '').toLowerCase();
    if (addItemModalState[sectionKey]) {
        addItemModalState[sectionKey].page = 1;
        addItemModalState[sectionKey].search = '';
    }
}

/**
 * Load items for modal list
 * @param {string} section - Section name
 * @param {number} page - Page number
 * @param {string} search - Search query
 */
export async function loadItemsForModal(section, page = 1, search = '') {
    const listId = `${section}List`;
    const listElement = document.getElementById(listId);
    if (!listElement) {
        console.warn(`List element ${listId} not found for section ${section}`);
        return;
    }

    // Update state
    if (addItemModalState[section]) {
        addItemModalState[section].page = page;
        addItemModalState[section].search = search;
    }

    // Show loading
    listElement.innerHTML = '<div class="empty-state" style="padding: 2rem; text-align: center; color: #64748b;">Loading...</div>';

    // Build API URL and params based on section
    let apiUrl = '';
    const params = {
        page: page,
        per_page: addItemModalState[section]?.perPage || 10
    };
    
    if (search) {
        params.q = search;
    }

    try {
        let data;
        
        // Modals use different endpoints than section views
        // Modals use /api/* endpoints (page-based pagination)
        // Section views use /api/archives/* endpoints (cursor pagination)
        switch(section) {
            case 'source':
                apiUrl = `/api/sources?${new URLSearchParams(params).toString()}`;
                data = await apiGet(apiUrl);
                break;
            case 'side':
                apiUrl = `/api/sides?${new URLSearchParams(params).toString()}`;
                data = await apiGet(apiUrl);
                break;
            case 'keyword':
            case 'keywords':
                apiUrl = `/api/keywords?${new URLSearchParams(params).toString()}`;
                data = await apiGet(apiUrl);
                break;
            case 'category':
                // Use search endpoint for categories in modals
                if (search) {
                    apiUrl = `/api/categories/search?${new URLSearchParams({ q: search, page, per_page: params.per_page }).toString()}`;
                    data = await apiGet(apiUrl);
                } else {
                    apiUrl = `/api/categories?${new URLSearchParams(params).toString()}`;
                    data = await apiGet(apiUrl);
                }
                break;
            default:
                console.error(`Unknown section: ${section}`);
                listElement.innerHTML = `<div class="empty-state" style="padding: 2rem; text-align: center; color: #ef4444;">Unknown section: ${section}</div>`;
                return;
        }
        
        let items = [];
        let total = 0;
        let totalPages = 1;

        // Handle different response formats
        // apiGet already throws if data.success === false, so we don't need to check that
        if (Array.isArray(data)) {
            // Direct array response (e.g., /api/categories, /api/sources, /api/sides)
            items = data;
            total = data.length;
            totalPages = Math.ceil(total / (addItemModalState[section]?.perPage || 10));
        } else if (data.results || data.items) {
            // Results format (e.g., /api/categories/search)
            items = data.results || data.items || [];
            if (data.pagination) {
                total = data.pagination.total || items.length;
                totalPages = data.pagination.total_pages || Math.ceil(total / (addItemModalState[section]?.perPage || 10));
            } else {
                total = data.total || items.length;
                totalPages = data.total_pages || Math.ceil(total / (addItemModalState[section]?.perPage || 10));
            }
        } else if (data.keywords) {
            // Keywords API format
            items = data.keywords || [];
            total = data.total || 0;
            totalPages = data.total_pages || 1;
        } else if (data.data) {
            // Some APIs return data in data field
            items = data.data || [];
            total = data.total || data.total_estimated || items.length;
            totalPages = data.total_pages || Math.ceil(total / (addItemModalState[section]?.perPage || 10));
        } else {
            // Unknown format - try to extract items
            console.warn(`Unknown response format for ${section}:`, data);
            items = [];
            total = 0;
            totalPages = 1;
        }

        // Update state
        if (addItemModalState[section]) {
            addItemModalState[section].total = total;
            addItemModalState[section].totalPages = totalPages;
        }

        // Render items
        renderItemsList(listId, items, section);
        
        // Render unified pagination
        renderModalPagination(section, page, totalPages);
    } catch (error) {
        console.error(`Error loading items for ${section}:`, error);
        let errorMessage = 'Unknown error';
        if (error) {
            if (typeof error === 'string') {
                errorMessage = error;
            } else if (error.message) {
                errorMessage = error.message;
            } else if (error.error) {
                errorMessage = error.error;
            } else if (error.toString) {
                errorMessage = error.toString();
            }
        }
        listElement.innerHTML = `<div class="empty-state" style="padding: 2rem; text-align: center; color: #ef4444;">Error loading items: ${errorMessage}</div>`;
    }
}

/**
 * Render items list in modal
 * @param {string} listId - List element ID
 * @param {Array} items - Items to render
 * @param {string} section - Section name
 */
export function renderItemsList(listId, items, section) {
    const listElement = document.getElementById(listId);
    if (!listElement) return;

    if (items.length === 0) {
        listElement.innerHTML = `<div class="empty-state" style="padding: 2rem; text-align: center; color: #64748b;">${translations.noItemsFound || 'No items found'}</div>`;
        return;
    }

    let html = '';
    items.forEach(item => {
        const name = item.name || item.text || item.word || 'Unnamed';
        const details = getItemDetailsForList(item, section);
        html += `
            <div class="add-item-list-item" data-id="${item.id}">
                <div class="add-item-list-item-name">${name}</div>
                ${details ? `<div class="add-item-list-item-details">${details}</div>` : ''}
            </div>
        `;
    });

    listElement.innerHTML = html;
}

/**
 * Get item details for list display
 * @param {Object} item - Item object
 * @param {string} section - Section name
 * @returns {string|null} Details string or null
 */
function getItemDetailsForList(item, section) {
    if (section === 'source') {
        const parts = [];
        if (item.job) parts.push(item.job);
        if (item.country) parts.push(item.country);
        return parts.join(' • ') || null;
    } else if (section === 'side') {
        return item.importance ? `Importance: ${item.importance}` : null;
    } else if (section === 'keyword') {
        return item.category_name ? `Category: ${item.category_name}` : null;
    } else if (section === 'category') {
        return item.file_count ? `${item.file_count} files` : null;
    }
    return null;
}

/**
 * Render unified pagination for modal
 * @param {string} section - Section name
 * @param {number} page - Current page
 * @param {number} totalPages - Total pages
 */
function renderModalPagination(section, page, totalPages) {
    const paginationContainer = document.getElementById(`${section}Pagination`);
    if (!paginationContainer) return;
    
    // Show pagination container if there are multiple pages
    if (totalPages > 1) {
        paginationContainer.style.display = 'block';
        
        // Ensure container has an ID
        if (!paginationContainer.id) {
            paginationContainer.id = `${section}Pagination`;
        }
        
        // Use unified pagination
        import('../rendering/unified-pagination.js').then(module => {
            module.renderUnifiedPagination({
                currentPage: page,
                totalPages: totalPages,
                containerId: paginationContainer.id,
                onPageChange: (targetPage) => {
                    const searchInput = document.getElementById(`${section}SearchInput`);
                    const searchTerm = searchInput ? searchInput.value.trim() : '';
                    loadItemsForModal(section, targetPage, searchTerm);
                },
                urlParams: {},
                showInfo: true,
                showJump: totalPages > 5,
                baseUrl: window.location.pathname
            });
        }).catch(err => {
            console.error('Error loading unified pagination for modal:', err);
            // Fallback: just show page info
            const paginationInfo = document.getElementById(`${section}PaginationInfo`);
            if (paginationInfo) {
                paginationInfo.textContent = `Page ${page} of ${totalPages}`;
            }
        });
    } else {
        paginationContainer.style.display = 'none';
    }
}

/**
 * Search items with debounce
 * @param {string} section - Section name
 */
export function searchItems(section) {
    const searchInput = document.getElementById(`${section}SearchInput`);
    if (!searchInput) return;

    const searchTerm = searchInput.value.trim();
    
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(() => {
        loadItemsForModal(section, 1, searchTerm);
    }, 300);
}

/**
 * Submit add item form
 * @param {string} section - Section name
 */
export async function submitAddItem(section) {
    const modalId = `add${section.charAt(0).toUpperCase() + section.slice(1)}Modal`;
    const submitBtn = document.querySelector(`#${modalId} .add-item-btn-submit`);
    const originalBtnText = submitBtn ? submitBtn.textContent : '';
    
    try {
        // Show loading state
        if (submitBtn) {
            submitBtn.disabled = true;
            submitBtn.innerHTML = `<i class="bi bi-arrow-repeat" style="animation: spin 1s linear infinite;"></i> ${translations.submitting || 'Submitting...'}`;
        }

        let data = {};
        let apiUrl = '';
        let useFormData = false;
        let formData = null;

        switch(section) {
            case 'source':
                const sourceName = document.getElementById('sourceName').value.trim();
                if (!sourceName) {
                    notificationSystem.error(translations.sourceNameRequired || 'Source name is required');
                    if (submitBtn) {
                        submitBtn.disabled = false;
                        submitBtn.textContent = originalBtnText;
                    }
                    return;
                }
                // SECURITY FIX: Validate input length to prevent DoS
                if (sourceName.length > 255) {
                    notificationSystem.error('Source name cannot exceed 255 characters');
                    if (submitBtn) {
                        submitBtn.disabled = false;
                        submitBtn.textContent = originalBtnText;
                    }
                    return;
                }
                
                const toNullIfEmpty = (value) => {
                    const trimmed = typeof value === 'string' ? value.trim() : value;
                    return trimmed === '' ? null : trimmed;
                };
                
                const ownershipValue = document.getElementById('sourceOwnership')?.value || '';
                const accessStatusValue = document.getElementById('sourceAccessStatus')?.value || '';
                const dateDiscoveryValue = document.getElementById('sourceDateDiscovery')?.value || '';
                const categoryValue = document.getElementById('sourceCategory')?.value || '';
                
                // VALIDATION FIX: Clamp importance between 0 and 1
                let importance = parseFloat(document.getElementById('sourceImportance').value) || 0.5;
                importance = Math.max(0, Math.min(1, importance)); // Clamp between 0 and 1
                
                // VALIDATION FIX: Validate other field lengths
                const job = document.getElementById('sourceJob').value.trim();
                const country = document.getElementById('sourceCountry').value.trim();
                const city = document.getElementById('sourceCity').value.trim();
                const description = document.getElementById('sourceDescription').value.trim();
                const accounts = document.getElementById('sourceAccounts')?.value.trim() || '';
                const note = document.getElementById('sourceNote')?.value.trim() || '';
                const attachments = document.getElementById('sourceAttachments')?.value.trim() || '';
                
                if (job.length > 255) {
                    notificationSystem.error('Job/Type cannot exceed 255 characters');
                    if (submitBtn) {
                        submitBtn.disabled = false;
                        submitBtn.textContent = originalBtnText;
                    }
                    return;
                }
                if (country.length > 100) {
                    notificationSystem.error('Country cannot exceed 100 characters');
                    if (submitBtn) {
                        submitBtn.disabled = false;
                        submitBtn.textContent = originalBtnText;
                    }
                    return;
                }
                if (city.length > 100) {
                    notificationSystem.error('City cannot exceed 100 characters');
                    if (submitBtn) {
                        submitBtn.disabled = false;
                        submitBtn.textContent = originalBtnText;
                    }
                    return;
                }
                if (description.length > 2000) {
                    notificationSystem.error('Description cannot exceed 2000 characters');
                    if (submitBtn) {
                        submitBtn.disabled = false;
                        submitBtn.textContent = originalBtnText;
                    }
                    return;
                }
                
                data = {
                    name: sourceName,
                    job: job,
                    country: country,
                    city: city,
                    importance: importance,
                    description: description,
                    accounts: accounts,
                    note: note,
                    attachments: attachments,
                    ownership: toNullIfEmpty(ownershipValue),
                    access_status: toNullIfEmpty(accessStatusValue),
                    date_source_discovery: dateDiscoveryValue || null,
                    category_id: categoryValue ? parseInt(categoryValue) : null
                };
                apiUrl = '/api/sources';
                break;

            case 'side':
                const sideName = document.getElementById('sideName').value.trim();
                if (!sideName) {
                    notificationSystem.error(translations.sideNameRequired || 'Side name is required');
                    if (submitBtn) {
                        submitBtn.disabled = false;
                        submitBtn.textContent = originalBtnText;
                    }
                    return;
                }
                // SECURITY FIX: Validate input length
                if (sideName.length > 255) {
                    notificationSystem.error('Side name cannot exceed 255 characters');
                    if (submitBtn) {
                        submitBtn.disabled = false;
                        submitBtn.textContent = originalBtnText;
                    }
                    return;
                }
                // VALIDATION FIX: Clamp importance between 0 and 1
                let sideImportance = parseFloat(document.getElementById('sideImportance').value) || 0.5;
                sideImportance = Math.max(0, Math.min(1, sideImportance)); // Clamp between 0 and 1
                
                data = {
                    name: sideName,
                    importance: sideImportance
                };
                apiUrl = '/api/sides';
                break;

            case 'keyword':
                const selectedWords = window.$ ? $('#keywordWords').val() : null;
                if (!selectedWords || selectedWords.length === 0) {
                    notificationSystem.error(translations.atLeastOneWordRequired || 'At least one word is required');
                    if (submitBtn) {
                        submitBtn.disabled = false;
                        submitBtn.textContent = originalBtnText;
                    }
                    return;
                }
                
                const wordsArray = Array.isArray(selectedWords) ? selectedWords : [selectedWords];
                if (wordsArray.length < 2) {
                    notificationSystem.error(translations.keywordRequiresMultipleWords || 'Keywords must contain at least 2 words. Please select multiple words to create a keyword phrase.');
                    if (submitBtn) {
                        submitBtn.disabled = false;
                        submitBtn.textContent = originalBtnText;
                    }
                    return;
                }
                
                const categoryId = window.$ ? $('#keywordCategory').val() : null;
                const keywordPhrase = wordsArray.join(' ');
                
                // Check for duplicate before submitting
                try {
                    const checkResponse = await fetch('/api/keyword/check', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-CSRFToken': csrfToken
                        },
                        body: JSON.stringify({
                            keyword_text: keywordPhrase,
                            category_id: categoryId || '1'
                        })
                    });
                    
                    const checkData = await checkResponse.json();
                    if (checkData.exists) {
                        notificationSystem.error(checkData.message || `Keyword "${keywordPhrase}" already exists in this category`);
                        if (submitBtn) {
                            submitBtn.disabled = false;
                            submitBtn.textContent = originalBtnText;
                        }
                        return;
                    }
                } catch (error) {
                    console.warn('Error checking keyword duplicate:', error);
                    // Continue with submission if check fails (backend will catch it)
                }
                
                formData = new FormData();
                formData.append('keywords_text', keywordPhrase);
                formData.append('category_id', categoryId || '1');
                // CSRF token will be added to header in fetch call below
                // Also add to form data for compatibility (will be set in submit section)
                
                apiUrl = '/keywords/add';
                useFormData = true;
                break;

            case 'category':
                // Check if using Bootstrap modal (new format)
                const categoryWordInput = document.getElementById('categoryWordInput');
                const selectedCategoryWordIdInput = document.getElementById('selectedCategoryWordId');
                
                if (categoryWordInput && selectedCategoryWordIdInput) {
                    // Bootstrap modal format
                    const wordText = categoryWordInput.value.trim();
                    const wordId = selectedCategoryWordIdInput.value;
                    
                    if (!wordText || !wordId) {
                        notificationSystem.error(translations.categoryNameRequired || 'Please select or enter a word');
                        if (submitBtn) {
                            submitBtn.disabled = false;
                            submitBtn.textContent = originalBtnText;
                        }
                        return;
                    }
                    
                    data = {
                        category_name: wordText.trim()
                    };
                    apiUrl = '/category/add';
                } else {
                    // Legacy Select2 format
                    const categoryName = window.$ ? $('#categoryName').val() : null;
                    if (!categoryName) {
                        notificationSystem.error(translations.categoryNameRequired || 'Category name is required');
                        if (submitBtn) {
                            submitBtn.disabled = false;
                            submitBtn.textContent = originalBtnText;
                        }
                        return;
                    }
                    data = {
                        category_name: categoryName.trim()
                    };
                    apiUrl = '/category/add';
                }
                break;

            default:
                notificationSystem.error(`Unknown section: ${section}`);
                if (submitBtn) {
                    submitBtn.disabled = false;
                    submitBtn.textContent = originalBtnText;
                }
                return;
        }

        // Submit request
        let response;
        if (useFormData && formData) {
            // Get CSRF token with fallback
            let csrfToken = getCSRFToken();
            if (!csrfToken) {
                const { getCSRFTokenAsync } = await import('../core/utils.js');
                csrfToken = await getCSRFTokenAsync();
            }
            
            // Add CSRF token to form data for compatibility
            formData.append('csrf_token', csrfToken);
            
            response = await fetch(apiUrl, {
                method: 'POST',
                headers: {
                    'X-Requested-With': 'XMLHttpRequest',
                    'X-CSRFToken': csrfToken
                },
                body: formData
            });
        } else {
            response = await apiPost(apiUrl, data);
            // Convert response to Response object for consistent handling
            if (response && typeof response.json === 'function') {
                // Already a Response object
            } else {
                // Create mock Response for consistent handling
                response = {
                    ok: true,
                    json: async () => response,
                    headers: { get: () => 'application/json' }
                };
            }
        }

        // Handle response
        const contentType = response.headers?.get('content-type') || '';
        if (response.ok) {
            if (contentType.includes('application/json')) {
                const result = await response.json();
                if (result.success !== false) {
                    const successMessage = result.message || translations.successfullyAdded || 'Successfully added!';
                    notificationSystem.success(successMessage);
                    closeAddItemModal(modalId);
                    
                    setTimeout(() => {
                        (window.swapNavigate
                            ? window.swapNavigate(window.location.pathname + '?t=' + Date.now())
                            : (window.location.href = window.location.pathname + '?t=' + Date.now()));
                    }, 500);
                } else {
                    const errorMsg = result.error || result.message || translations.errorAdding || 'Error adding item';
                    notificationSystem.error(errorMsg);
                }
            } else {
                notificationSystem.success(translations.successfullyAdded || 'Successfully added!');
                closeAddItemModal(modalId);
                
                // Reload current view
                if (window.fms && window.fms.navigation) {
                    if (window.fms.core.state.navigationState.currentSection) {
                        window.fms.navigation.navigateToSection(window.fms.core.state.navigationState.currentSection);
                    } else {
                        window.fms.navigation.navigateToRoot();
                    }
                }
            }
        } else {
            let errorMsg = `${translations.errorAdding || 'Error adding'} ${section}`;
            try {
                if (contentType.includes('application/json')) {
                    const result = await response.json();
                    errorMsg = result.error || result.message || errorMsg;
                }
            } catch (e) {
                console.error('Error parsing error response:', e);
            }
            notificationSystem.error(errorMsg);
        }
    } catch (error) {
        console.error(`Error submitting ${section}:`, error);
        notificationSystem.error(`${translations.errorAdding || 'Error adding'} ${section}: ${error.message}`);
    } finally {
        // Restore button state
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.textContent = originalBtnText;
        }
    }
}

/**
 * Search category words (for category modal list)
 */
export function searchCategoryWords() {
    const searchInput = document.getElementById('categorySearchInput');
    if (!searchInput) return;

    const searchTerm = searchInput.value.trim();
    
    if (searchTimeout) {
        clearTimeout(searchTimeout);
    }
    searchTimeout = setTimeout(() => {
        loadItemsForModal('category', 1, searchTerm);
    }, 300);
}

/**
 * Submit word-category relationship
 */
export async function submitAddWordsCategorys() {
    const submitBtn = document.querySelector('#addWordsCategorysModal .add-item-btn-submit');
    const originalBtnText = submitBtn ? submitBtn.textContent : '';
    
    try {
        // Show loading state
        if (submitBtn) {
            submitBtn.disabled = true;
            submitBtn.innerHTML = `<i class="bi bi-arrow-repeat" style="animation: spin 1s linear infinite;"></i> ${translations.submitting || 'Submitting...'}`;
        }

        const wordId = document.getElementById('wordsCategorysWordId')?.value;
        const categoryId = document.getElementById('wordsCategorysCategoryId')?.value;
        
        // Check if using jQuery Select2
        let wordIdValue = wordId;
        let categoryIdValue = categoryId;
        
        if (typeof jQuery !== 'undefined' && jQuery.fn.select2) {
            const $wordSelect = jQuery('#wordsCategorysWordId');
            const $categorySelect = jQuery('#wordsCategorysCategoryId');
            if ($wordSelect.length && $wordSelect.data('select2')) {
                wordIdValue = $wordSelect.val();
            }
            if ($categorySelect.length && $categorySelect.data('select2')) {
                categoryIdValue = $categorySelect.val();
            }
        }
        
        if (!wordIdValue || !categoryIdValue) {
            notificationSystem.error(translations.bothWordAndCategoryRequired || 'Both word and category are required');
            if (submitBtn) {
                submitBtn.disabled = false;
                submitBtn.textContent = originalBtnText;
            }
            return;
        }
        
        const data = {
            word_id: parseInt(wordIdValue),
            category_id: parseInt(categoryIdValue)
        };
        
        const { apiPost } = await import('../api/api-client.js');
        const response = await apiPost('/api/words-categorys/add', data);
        
        if (response.success !== false) {
            // Show appropriate message based on whether relationship already existed
            if (response.already_exists) {
                notificationSystem.info(
                    response.message || (translations.wordCategoryAlreadyExists || 'Word-category relationship already exists')
                );
            } else {
                notificationSystem.success(
                    response.message || (translations.successfullyAddedWordCategory || 'Successfully added word-category relationship!')
                );
            }
            closeAddItemModal('addWordsCategorysModal');
            
            // Reload page to refresh data
            setTimeout(() => {
                (window.swapNavigate
                            ? window.swapNavigate(window.location.pathname + '?t=' + Date.now())
                            : (window.location.href = window.location.pathname + '?t=' + Date.now()));
            }, 500);
        } else {
            const errorMsg = response.error || (translations.errorAddingWordCategory || 'Error adding word-category relationship');
            notificationSystem.error(errorMsg);
        }
    } catch (error) {
        console.error('Error submitting words_categorys:', error);
        notificationSystem.error(`${translations.errorAddingWordCategory || 'Error adding word-category relationship'}: ${error.message}`);
    } finally {
        // Restore button state
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.textContent = originalBtnText;
        }
    }
}

/**
 * Setup Escape key handler for modals
 */
export function setupModalKeyboardHandlers() {
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') {
            ['addSourceModal', 'addSideModal', 'addKeywordModal', 'addCategoryModal', 'addWordsCategorysModal'].forEach(modalId => {
                const modal = document.getElementById(modalId);
                if (modal) {
                    // Check if Bootstrap modal
                    if (modal.classList.contains('modal') && typeof bootstrap !== 'undefined') {
                        const bsModal = bootstrap.Modal.getInstance(modal);
                        if (bsModal) {
                            bsModal.hide();
                        }
                    } else if (modal.classList.contains('active')) {
                        closeAddItemModal(modalId);
                    }
                }
            });
            
            // Also close file modal with Escape
            const fileModal = document.getElementById('fileModal');
            if (fileModal && fileModal.classList.contains('active')) {
                if (window.fms && window.fms.fileOperations && window.fms.fileOperations.details && window.fms.fileOperations.details.closeFileModal) {
                    window.fms.fileOperations.details.closeFileModal();
                }
            }
        }
    });
}

// Helper functions for Bootstrap modals

function getCSRFTokenForModal() {
    const metaTag = document.querySelector('meta[name="csrf-token"]');
    return metaTag ? metaTag.getAttribute('content') : '';
}

async function getCSRFTokenAsyncForModal() {
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

function showToastForModal(message, type = 'info', duration = 4000) {
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
    if (typeof bootstrap !== 'undefined' && bootstrap.Toast) {
        const toast = new bootstrap.Toast(toastElement, { delay: duration });
        toast.show();
        
        toastElement.addEventListener('hidden.bs.toast', () => {
            toastElement.remove();
        });
    } else {
        setTimeout(() => toastElement.remove(), duration);
    }
}

// Category modal state
let categoryWordSearchTimeout = null;
let categoryWordInputHandler = null;
let categoryWordKeydownHandler = null;
let categoryClickOutsideHandler = null;
let selectedCategoryWordId = null;
let selectedCategoryWordText = null;

/**
 * Open Add Category Modal (Bootstrap version)
 */
export function openAddCategoryModal() {
    const modalElement = document.getElementById('addCategoryModal');
    if (!modalElement) {
        console.error('Add category modal not found');
        return;
    }
    
    if (typeof bootstrap === 'undefined' || !bootstrap.Modal) {
        console.error('Bootstrap not available');
        return;
    }
    
    const modal = new bootstrap.Modal(modalElement);
    const wordInput = document.getElementById('categoryWordInput');
    const wordResults = document.getElementById('categoryWordSearchResults');
    const selectedWordIdInput = document.getElementById('selectedCategoryWordId');
    
    if (!wordInput || !wordResults || !selectedWordIdInput) {
        console.error('Required modal elements not found');
        return;
    }
    
    // Clean up handlers
    if (categoryClickOutsideHandler) {
        document.removeEventListener('click', categoryClickOutsideHandler, true);
        categoryClickOutsideHandler = null;
    }
    if (categoryWordInputHandler && wordInput) {
        wordInput.removeEventListener('input', categoryWordInputHandler);
        categoryWordInputHandler = null;
    }
    if (categoryWordKeydownHandler && wordInput) {
        wordInput.removeEventListener('keydown', categoryWordKeydownHandler);
        categoryWordKeydownHandler = null;
    }
    if (categoryWordSearchTimeout) {
        clearTimeout(categoryWordSearchTimeout);
        categoryWordSearchTimeout = null;
    }
    
    // Reset state
    selectedCategoryWordId = null;
    selectedCategoryWordText = null;
    wordInput.value = '';
    selectedWordIdInput.value = '';
    wordResults.style.display = 'none';
    wordResults.innerHTML = '';
    
    // Initialize word search
    function initializeWordSearch() {
        if (categoryWordSearchTimeout) {
            clearTimeout(categoryWordSearchTimeout);
            categoryWordSearchTimeout = null;
        }
        
        if (categoryWordInputHandler) {
            wordInput.removeEventListener('input', categoryWordInputHandler);
        }
        if (categoryWordKeydownHandler) {
            wordInput.removeEventListener('keydown', categoryWordKeydownHandler);
        }
        
        categoryWordInputHandler = function(e) {
            const searchTerm = e.target.value.trim();
            
            if (categoryWordSearchTimeout) {
                clearTimeout(categoryWordSearchTimeout);
            }
            
            if (searchTerm.length === 0) {
                wordResults.style.display = 'none';
                wordResults.innerHTML = '';
                return;
            }
            
            categoryWordSearchTimeout = setTimeout(() => {
                searchWordsForCategory(searchTerm);
            }, 300);
        };
        wordInput.addEventListener('input', categoryWordInputHandler);
        
        categoryWordKeydownHandler = function(e) {
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                const firstItem = wordResults.querySelector('.word-result-item');
                if (firstItem) {
                    firstItem.focus();
                    firstItem.classList.add('active');
                }
            } else if (e.key === 'Escape') {
                wordResults.style.display = 'none';
            }
        };
        wordInput.addEventListener('keydown', categoryWordKeydownHandler);
        
        setTimeout(() => {
            wordInput.focus();
        }, 300);
    }
    
    // Search words function
    async function searchWordsForCategory(searchTerm) {
        if (!searchTerm || searchTerm.length === 0) {
            wordResults.style.display = 'none';
            return;
        }
        
        try {
            const params = new URLSearchParams({
                q: searchTerm,
                page: 1,
                per_page: 20
            });
            
            const response = await fetch(`/api/words/search?${params.toString()}`);
            const data = await response.json();
            
            if (data.results && data.results.length > 0) {
                renderWordResultsForCategory(data.results, searchTerm);
            } else {
                renderWordResultsForCategory([], searchTerm);
            }
        } catch (error) {
            console.error('Error searching words:', error);
            wordResults.style.display = 'none';
        }
    }
    
    // Render word results
    function renderWordResultsForCategory(results, searchTerm) {
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
                    selectWordForCategory(wordId, wordText);
                });
                
                itemDiv.addEventListener('keydown', function(e) {
                    if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        selectWordForCategory(wordId, wordText);
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
        
        // Show option to create new word
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
                selectWordForCategory('new', searchTerm);
            });
            
            createDiv.addEventListener('keydown', function(e) {
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    selectWordForCategory('new', searchTerm);
                }
            });
            
            wordResults.appendChild(createDiv);
        }
        
        wordResults.style.display = 'block';
    }
    
    // Select word function
    async function selectWordForCategory(wordId, wordText) {
        if (wordId === 'new') {
            // Create new word
            try {
                showToastForModal('Creating new word...', 'info');
                const csrfToken = getCSRFTokenForModal() || await getCSRFTokenAsyncForModal();
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
                    showToastForModal('Word created successfully', 'success');
                    wordId = data.id;
                } else {
                    throw new Error(data.error || 'Failed to create word');
                }
            } catch (error) {
                console.error('Error creating word:', error);
                showToastForModal('Error creating word: ' + error.message, 'error');
                return;
            }
        }
        
        selectedCategoryWordId = wordId;
        selectedCategoryWordText = wordText;
        wordInput.value = wordText;
        selectedWordIdInput.value = wordId;
        wordResults.style.display = 'none';
        wordInput.focus();
    }
    
    // Show modal and initialize
    modal.show();
    
    // Wait for modal to be fully shown
    modalElement.addEventListener('shown.bs.modal', function onShown() {
        modalElement.removeEventListener('shown.bs.modal', onShown);
        initializeWordSearch();
        
        // Add click outside handler
        const wordInputContainer = wordInput.closest('.position-relative') || wordInput.parentElement;
        categoryClickOutsideHandler = function(e) {
            const target = e.target;
            if (wordInputContainer && wordResults && 
                !wordInputContainer.contains(target) && 
                !wordResults.contains(target)) {
                wordResults.style.display = 'none';
            }
        };
        setTimeout(() => {
            document.addEventListener('click', categoryClickOutsideHandler, true);
        }, 100);
    }, { once: true });
    
    // Clean up when modal is hidden
    modalElement.addEventListener('hidden.bs.modal', function onHidden() {
        if (categoryClickOutsideHandler) {
            document.removeEventListener('click', categoryClickOutsideHandler, true);
            categoryClickOutsideHandler = null;
        }
        
        if (categoryWordSearchTimeout) {
            clearTimeout(categoryWordSearchTimeout);
            categoryWordSearchTimeout = null;
        }
        
        if (categoryWordInputHandler && wordInput) {
            wordInput.removeEventListener('input', categoryWordInputHandler);
            categoryWordInputHandler = null;
        }
        if (categoryWordKeydownHandler && wordInput) {
            wordInput.removeEventListener('keydown', categoryWordKeydownHandler);
            categoryWordKeydownHandler = null;
        }
        
        if (wordInput) wordInput.value = '';
        if (selectedWordIdInput) selectedWordIdInput.value = '';
        if (wordResults) {
            wordResults.style.display = 'none';
            wordResults.innerHTML = '';
        }
        
        selectedCategoryWordId = null;
        selectedCategoryWordText = null;
    }, { once: true });
}

/**
 * Save the word-as-category form in ``#addCategoryModal`` (FMAS page).
 *
 * Named apart from the Categories page's own ``saveCategory``: both used to
 * publish ``window.saveCategory``, and the last module to load won. On
 * ``/categories`` that was this one, which looks for ``#categoryWordInput`` -
 * the dialog there has no such input - so the Save button answered
 * "Form elements not found" and never issued a request.
 */
export async function saveAddCategoryModal() {
    const wordInput = document.getElementById('categoryWordInput');
    const selectedWordIdInput = document.getElementById('selectedCategoryWordId');
    
    if (!wordInput || !selectedWordIdInput) {
        showToastForModal('Form elements not found', 'error');
        return;
    }
    
    const wordText = wordInput.value.trim();
    const wordId = selectedWordIdInput.value;
    
    if (!wordText || !wordId) {
        showToastForModal('Please select or enter a word', 'warning');
        return;
    }
    
    try {
        showToastForModal('Adding category...', 'info');
        const csrfToken = getCSRFTokenForModal() || await getCSRFTokenAsyncForModal();
        const response = await fetch('/category/add', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken,
                'X-Requested-With': 'XMLHttpRequest'
            },
            body: JSON.stringify({
                category_name: wordText,
                csrf_token: csrfToken
            })
        });
        
        const contentType = response.headers.get('content-type') || '';
        let data;
        if (contentType.includes('application/json')) {
            data = await response.json();
        } else {
            data = { success: true, message: 'Category added successfully' };
        }
        
        if (data.success) {
            showToastForModal(data.message || 'Category added successfully', 'success');
            
            // Close modal
            const modalElement = document.getElementById('addCategoryModal');
            if (modalElement && typeof bootstrap !== 'undefined') {
                const modal = bootstrap.Modal.getInstance(modalElement);
                if (modal) {
                    modal.hide();
                }
            }
            
            // Reload page or refresh view
            setTimeout(() => {
                if (window.loadRootView) {
                    window.loadRootView();
                } else if (typeof window.refreshMainContent === 'function') {
                    window.refreshMainContent();
                } else {
                    window.location.reload();
                }
            }, 500);
        } else {
            showToastForModal(data.error || 'Error adding category', 'error');
        }
    } catch (error) {
        console.error('Error adding category:', error);
        showToastForModal('Error adding category: ' + error.message, 'error');
    }
}

// Words-Categorys modal state
let wordsCategorysWordSearchTimeout = null;
let wordsCategorysCategorySearchTimeout = null;
let wordsCategorysWordInputHandler = null;
let wordsCategorysCategoryInputHandler = null;
let wordsCategorysWordKeydownHandler = null;
let wordsCategorysCategoryKeydownHandler = null;
let wordsCategorysClickOutsideHandler = null;
let selectedWordsCategorysWordId = null;
let selectedWordsCategorysWordText = null;
let selectedWordsCategorysCategoryId = null;
let selectedWordsCategorysCategoryText = null;

/**
 * Open Add Words-Categorys Modal (Bootstrap version)
 */
export function openAddWordsCategorysModal() {
    const modalElement = document.getElementById('addWordsCategorysModal');
    if (!modalElement) {
        console.error('Add words-categorys modal not found');
        return;
    }
    
    if (typeof bootstrap === 'undefined' || !bootstrap.Modal) {
        console.error('Bootstrap not available');
        return;
    }
    
    const modal = new bootstrap.Modal(modalElement);
    const wordInput = document.getElementById('wordsCategorysWordInput');
    const categoryInput = document.getElementById('wordsCategorysCategoryInput');
    const wordResults = document.getElementById('wordsCategorysWordSearchResults');
    const categoryResults = document.getElementById('wordsCategorysCategorySearchResults');
    const selectedWordIdInput = document.getElementById('selectedWordsCategorysWordId');
    const selectedCategoryIdInput = document.getElementById('selectedWordsCategorysCategoryId');
    
    if (!wordInput || !categoryInput || !wordResults || !categoryResults || !selectedWordIdInput || !selectedCategoryIdInput) {
        console.error('Required modal elements not found');
        return;
    }
    
    // Clean up handlers
    if (wordsCategorysClickOutsideHandler) {
        document.removeEventListener('click', wordsCategorysClickOutsideHandler, true);
        wordsCategorysClickOutsideHandler = null;
    }
    if (wordsCategorysWordInputHandler && wordInput) {
        wordInput.removeEventListener('input', wordsCategorysWordInputHandler);
        wordsCategorysWordInputHandler = null;
    }
    if (wordsCategorysCategoryInputHandler && categoryInput) {
        categoryInput.removeEventListener('input', wordsCategorysCategoryInputHandler);
        wordsCategorysCategoryInputHandler = null;
    }
    if (wordsCategorysWordKeydownHandler && wordInput) {
        wordInput.removeEventListener('keydown', wordsCategorysWordKeydownHandler);
        wordsCategorysWordKeydownHandler = null;
    }
    if (wordsCategorysCategoryKeydownHandler && categoryInput) {
        categoryInput.removeEventListener('keydown', wordsCategorysCategoryKeydownHandler);
        wordsCategorysCategoryKeydownHandler = null;
    }
    if (wordsCategorysWordSearchTimeout) {
        clearTimeout(wordsCategorysWordSearchTimeout);
        wordsCategorysWordSearchTimeout = null;
    }
    if (wordsCategorysCategorySearchTimeout) {
        clearTimeout(wordsCategorysCategorySearchTimeout);
        wordsCategorysCategorySearchTimeout = null;
    }
    
    // Reset state
    selectedWordsCategorysWordId = null;
    selectedWordsCategorysWordText = null;
    selectedWordsCategorysCategoryId = null;
    selectedWordsCategorysCategoryText = null;
    wordInput.value = '';
    categoryInput.value = '';
    selectedWordIdInput.value = '';
    selectedCategoryIdInput.value = '';
    wordResults.style.display = 'none';
    wordResults.innerHTML = '';
    categoryResults.style.display = 'none';
    categoryResults.innerHTML = '';
    
    // Initialize word search
    function initializeWordSearch() {
        if (wordsCategorysWordSearchTimeout) {
            clearTimeout(wordsCategorysWordSearchTimeout);
            wordsCategorysWordSearchTimeout = null;
        }
        
        if (wordsCategorysWordInputHandler) {
            wordInput.removeEventListener('input', wordsCategorysWordInputHandler);
        }
        if (wordsCategorysWordKeydownHandler) {
            wordInput.removeEventListener('keydown', wordsCategorysWordKeydownHandler);
        }
        
        wordsCategorysWordInputHandler = function(e) {
            const searchTerm = e.target.value.trim();
            
            if (wordsCategorysWordSearchTimeout) {
                clearTimeout(wordsCategorysWordSearchTimeout);
            }
            
            if (searchTerm.length === 0) {
                wordResults.style.display = 'none';
                wordResults.innerHTML = '';
                return;
            }
            
            wordsCategorysWordSearchTimeout = setTimeout(() => {
                searchWordsForWordsCategorys(searchTerm);
            }, 300);
        };
        wordInput.addEventListener('input', wordsCategorysWordInputHandler);
        
        wordsCategorysWordKeydownHandler = function(e) {
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                const firstItem = wordResults.querySelector('.word-result-item');
                if (firstItem) {
                    firstItem.focus();
                    firstItem.classList.add('active');
                }
            } else if (e.key === 'Escape') {
                wordResults.style.display = 'none';
            }
        };
        wordInput.addEventListener('keydown', wordsCategorysWordKeydownHandler);
        
        setTimeout(() => {
            wordInput.focus();
        }, 300);
    }
    
    // Initialize category search
    function initializeCategorySearch() {
        if (wordsCategorysCategorySearchTimeout) {
            clearTimeout(wordsCategorysCategorySearchTimeout);
            wordsCategorysCategorySearchTimeout = null;
        }
        
        if (wordsCategorysCategoryInputHandler) {
            categoryInput.removeEventListener('input', wordsCategorysCategoryInputHandler);
        }
        if (wordsCategorysCategoryKeydownHandler) {
            categoryInput.removeEventListener('keydown', wordsCategorysCategoryKeydownHandler);
        }
        
        wordsCategorysCategoryInputHandler = function(e) {
            const searchTerm = e.target.value.trim();
            
            if (wordsCategorysCategorySearchTimeout) {
                clearTimeout(wordsCategorysCategorySearchTimeout);
            }
            
            if (searchTerm.length === 0) {
                categoryResults.style.display = 'none';
                categoryResults.innerHTML = '';
                return;
            }
            
            wordsCategorysCategorySearchTimeout = setTimeout(() => {
                searchCategoriesForWordsCategorys(searchTerm);
            }, 300);
        };
        categoryInput.addEventListener('input', wordsCategorysCategoryInputHandler);
        
        wordsCategorysCategoryKeydownHandler = function(e) {
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                const firstItem = categoryResults.querySelector('.word-result-item');
                if (firstItem) {
                    firstItem.focus();
                    firstItem.classList.add('active');
                }
            } else if (e.key === 'Escape') {
                categoryResults.style.display = 'none';
            }
        };
        categoryInput.addEventListener('keydown', wordsCategorysCategoryKeydownHandler);
    }
    
    // Search words function
    async function searchWordsForWordsCategorys(searchTerm) {
        if (!searchTerm || searchTerm.length === 0) {
            wordResults.style.display = 'none';
            return;
        }
        
        try {
            const params = new URLSearchParams({
                q: searchTerm,
                page: 1,
                per_page: 20
            });
            
            const response = await fetch(`/api/words/search?${params.toString()}`);
            const data = await response.json();
            
            if (data.results && data.results.length > 0) {
                renderWordResultsForWordsCategorys(data.results, searchTerm);
            } else {
                renderWordResultsForWordsCategorys([], searchTerm);
            }
        } catch (error) {
            console.error('Error searching words:', error);
            wordResults.style.display = 'none';
        }
    }
    
    // Render word results
    function renderWordResultsForWordsCategorys(results, searchTerm) {
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
                    selectWordForWordsCategorys(wordId, wordText);
                });
                
                itemDiv.addEventListener('keydown', function(e) {
                    if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        selectWordForWordsCategorys(wordId, wordText);
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
        
        // Show option to create new word
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
                selectWordForWordsCategorys('new', searchTerm);
            });
            
            createDiv.addEventListener('keydown', function(e) {
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    selectWordForWordsCategorys('new', searchTerm);
                }
            });
            
            wordResults.appendChild(createDiv);
        }
        
        wordResults.style.display = 'block';
    }
    
    // Select word function
    async function selectWordForWordsCategorys(wordId, wordText) {
        if (wordId === 'new') {
            // Create new word
            try {
                showToastForModal('Creating new word...', 'info');
                const csrfToken = getCSRFTokenForModal() || await getCSRFTokenAsyncForModal();
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
                    showToastForModal('Word created successfully', 'success');
                    wordId = data.id;
                } else {
                    throw new Error(data.error || 'Failed to create word');
                }
            } catch (error) {
                console.error('Error creating word:', error);
                showToastForModal('Error creating word: ' + error.message, 'error');
                return;
            }
        }
        
        selectedWordsCategorysWordId = wordId;
        selectedWordsCategorysWordText = wordText;
        wordInput.value = wordText;
        selectedWordIdInput.value = wordId;
        wordResults.style.display = 'none';
        wordInput.focus();
    }
    
    // Search categories function
    async function searchCategoriesForWordsCategorys(searchTerm) {
        if (!searchTerm || searchTerm.length === 0) {
            categoryResults.style.display = 'none';
            return;
        }
        
        try {
            const params = new URLSearchParams({
                q: searchTerm,
                page: 1,
                per_page: 20
            });
            
            const response = await fetch(`/api/categories/search?${params.toString()}`);
            const data = await response.json();
            
            if (data.results && data.results.length > 0) {
                renderCategoryResultsForWordsCategorys(data.results, searchTerm);
            } else {
                renderCategoryResultsForWordsCategorys([], searchTerm);
            }
        } catch (error) {
            console.error('Error searching categories:', error);
            categoryResults.style.display = 'none';
        }
    }
    
    // Render category results
    function renderCategoryResultsForWordsCategorys(results, searchTerm) {
        categoryResults.innerHTML = '';
        
        if (results.length > 0) {
            results.forEach((item, index) => {
                const categoryId = item.id;
                const categoryName = item.name || '';
                const fileCount = item.file_count || 0;
                
                const itemDiv = document.createElement('div');
                itemDiv.className = 'word-result-item';
                itemDiv.tabIndex = 0;
                itemDiv.setAttribute('data-category-id', categoryId);
                itemDiv.setAttribute('data-category-name', categoryName);
                
                itemDiv.innerHTML = `
                    <div class="d-flex align-items-center">
                        <i class="bi bi-check-circle me-2 text-primary"></i>
                        <span class="word-text">${escapeHtml(categoryName)}</span>
                        ${fileCount > 0 ? `<small class="text-muted ms-2">(${fileCount} files)</small>` : ''}
                    </div>
                `;
                
                itemDiv.addEventListener('click', function() {
                    selectCategoryForWordsCategorys(categoryId, categoryName);
                });
                
                itemDiv.addEventListener('keydown', function(e) {
                    if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        selectCategoryForWordsCategorys(categoryId, categoryName);
                    } else if (e.key === 'ArrowDown') {
                        e.preventDefault();
                        const next = categoryResults.querySelectorAll('.word-result-item')[index + 1];
                        if (next) {
                            itemDiv.classList.remove('active');
                            next.focus();
                            next.classList.add('active');
                        }
                    } else if (e.key === 'ArrowUp') {
                        e.preventDefault();
                        if (index > 0) {
                            const prev = categoryResults.querySelectorAll('.word-result-item')[index - 1];
                            itemDiv.classList.remove('active');
                            if (prev) {
                                prev.focus();
                                prev.classList.add('active');
                            } else {
                                categoryInput.focus();
                            }
                        }
                    }
                });
                
                categoryResults.appendChild(itemDiv);
            });
        }
        
        // Show option to create new category
        const exactMatch = results.some(item => {
            const categoryName = (item.name || '').toLowerCase();
            return categoryName === searchTerm.toLowerCase();
        });
        
        if (!exactMatch && searchTerm.length > 0 && !searchTerm.match(/^\d+$/)) {
            const createDiv = document.createElement('div');
            createDiv.className = 'word-result-item word-result-create';
            createDiv.tabIndex = 0;
            createDiv.setAttribute('data-category-id', 'new');
            createDiv.setAttribute('data-category-name', searchTerm);
            
            createDiv.innerHTML = `
                <div class="d-flex align-items-center">
                    <i class="bi bi-plus-circle me-2 text-success"></i>
                    <span class="word-text">${escapeHtml(searchTerm)}</span>
                    <small class="text-muted ms-2">(create new)</small>
                </div>
            `;
            
            createDiv.addEventListener('click', function() {
                selectCategoryForWordsCategorys('new', searchTerm);
            });
            
            createDiv.addEventListener('keydown', function(e) {
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    selectCategoryForWordsCategorys('new', searchTerm);
                }
            });
            
            categoryResults.appendChild(createDiv);
        }
        
        categoryResults.style.display = 'block';
    }
    
    // Select category function
    async function selectCategoryForWordsCategorys(categoryId, categoryName) {
        if (categoryId === 'new') {
            // Create new category
            try {
                showToastForModal('Creating new category...', 'info');
                const csrfToken = getCSRFTokenForModal() || await getCSRFTokenAsyncForModal();
                const response = await fetch('/category/add', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': csrfToken
                    },
                    body: JSON.stringify({
                        category_name: categoryName.trim(),
                        csrf_token: csrfToken
                    })
                });
                
                const data = await response.json();
                if (data.success) {
                    showToastForModal('Category created successfully', 'success');
                    categoryId = data.category_id;
                } else {
                    throw new Error(data.error || 'Failed to create category');
                }
            } catch (error) {
                console.error('Error creating category:', error);
                showToastForModal('Error creating category: ' + error.message, 'error');
                return;
            }
        }
        
        selectedWordsCategorysCategoryId = categoryId;
        selectedWordsCategorysCategoryText = categoryName;
        categoryInput.value = categoryName;
        selectedCategoryIdInput.value = categoryId;
        categoryResults.style.display = 'none';
        categoryInput.focus();
    }
    
    // Show modal and initialize
    modal.show();
    
    // Wait for modal to be fully shown
    modalElement.addEventListener('shown.bs.modal', function onShown() {
        modalElement.removeEventListener('shown.bs.modal', onShown);
        initializeWordSearch();
        initializeCategorySearch();
        
        // Add click outside handler
        const wordInputContainer = wordInput.closest('.position-relative') || wordInput.parentElement;
        const categoryInputContainer = categoryInput.closest('.position-relative') || categoryInput.parentElement;
        
        wordsCategorysClickOutsideHandler = function(e) {
            const target = e.target;
            if (wordInputContainer && wordResults && 
                !wordInputContainer.contains(target) && 
                !wordResults.contains(target)) {
                wordResults.style.display = 'none';
            }
            if (categoryInputContainer && categoryResults && 
                !categoryInputContainer.contains(target) && 
                !categoryResults.contains(target)) {
                categoryResults.style.display = 'none';
            }
        };
        setTimeout(() => {
            document.addEventListener('click', wordsCategorysClickOutsideHandler, true);
        }, 100);
    }, { once: true });
    
    // Clean up when modal is hidden
    modalElement.addEventListener('hidden.bs.modal', function onHidden() {
        if (wordsCategorysClickOutsideHandler) {
            document.removeEventListener('click', wordsCategorysClickOutsideHandler, true);
            wordsCategorysClickOutsideHandler = null;
        }
        
        if (wordsCategorysWordSearchTimeout) {
            clearTimeout(wordsCategorysWordSearchTimeout);
            wordsCategorysWordSearchTimeout = null;
        }
        if (wordsCategorysCategorySearchTimeout) {
            clearTimeout(wordsCategorysCategorySearchTimeout);
            wordsCategorysCategorySearchTimeout = null;
        }
        
        if (wordsCategorysWordInputHandler && wordInput) {
            wordInput.removeEventListener('input', wordsCategorysWordInputHandler);
            wordsCategorysWordInputHandler = null;
        }
        if (wordsCategorysCategoryInputHandler && categoryInput) {
            categoryInput.removeEventListener('input', wordsCategorysCategoryInputHandler);
            wordsCategorysCategoryInputHandler = null;
        }
        if (wordsCategorysWordKeydownHandler && wordInput) {
            wordInput.removeEventListener('keydown', wordsCategorysWordKeydownHandler);
            wordsCategorysWordKeydownHandler = null;
        }
        if (wordsCategorysCategoryKeydownHandler && categoryInput) {
            categoryInput.removeEventListener('keydown', wordsCategorysCategoryKeydownHandler);
            wordsCategorysCategoryKeydownHandler = null;
        }
        
        if (wordInput) wordInput.value = '';
        if (categoryInput) categoryInput.value = '';
        if (selectedWordIdInput) selectedWordIdInput.value = '';
        if (selectedCategoryIdInput) selectedCategoryIdInput.value = '';
        if (wordResults) {
            wordResults.style.display = 'none';
            wordResults.innerHTML = '';
        }
        if (categoryResults) {
            categoryResults.style.display = 'none';
            categoryResults.innerHTML = '';
        }
        
        selectedWordsCategorysWordId = null;
        selectedWordsCategorysWordText = null;
        selectedWordsCategorysCategoryId = null;
        selectedWordsCategorysCategoryText = null;
    }, { once: true });
}

/**
 * Save Words-Categorys function
 */
export async function saveWordsCategorys() {
    const wordInput = document.getElementById('wordsCategorysWordInput');
    const categoryInput = document.getElementById('wordsCategorysCategoryInput');
    const selectedWordIdInput = document.getElementById('selectedWordsCategorysWordId');
    const selectedCategoryIdInput = document.getElementById('selectedWordsCategorysCategoryId');
    
    if (!wordInput || !categoryInput || !selectedWordIdInput || !selectedCategoryIdInput) {
        showToastForModal('Form elements not found', 'error');
        return;
    }
    
    const wordId = selectedWordIdInput.value;
    const categoryId = selectedCategoryIdInput.value;
    
    if (!wordId || !categoryId) {
        showToastForModal('Please select both a word and a category', 'warning');
        return;
    }
    
    try {
        showToastForModal('Adding relationship...', 'info');
        const csrfToken = getCSRFTokenForModal() || await getCSRFTokenAsyncForModal();
        const response = await fetch('/api/words-categorys/add', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken,
                'X-Requested-With': 'XMLHttpRequest'
            },
            body: JSON.stringify({
                word_id: parseInt(wordId),
                category_id: parseInt(categoryId),
                csrf_token: csrfToken
            })
        });
        
        const contentType = response.headers.get('content-type') || '';
        let data;
        if (contentType.includes('application/json')) {
            data = await response.json();
        } else {
            data = { success: true, message: 'Relationship added successfully' };
        }
        
        if (data.success) {
            showToastForModal(data.message || 'Relationship added successfully', 'success');
            
            // Close modal
            const modalElement = document.getElementById('addWordsCategorysModal');
            if (modalElement && typeof bootstrap !== 'undefined') {
                const modal = bootstrap.Modal.getInstance(modalElement);
                if (modal) {
                    modal.hide();
                }
            }
            
            // Reload page or refresh view
            setTimeout(() => {
                if (window.loadRootView) {
                    window.loadRootView();
                } else if (typeof window.refreshMainContent === 'function') {
                    window.refreshMainContent();
                } else {
                    window.location.reload();
                }
            }, 500);
        } else {
            showToastForModal(data.error || 'Error adding relationship', 'error');
        }
    } catch (error) {
        console.error('Error adding relationship:', error);
        showToastForModal('Error adding relationship: ' + error.message, 'error');
    }
}

// Keyword modal state for archives page
let archivesKeywordWordSearchTimeout = null;
let archivesKeywordCategorySearchTimeout = null;
let archivesKeywordWordInputHandler = null;
let archivesKeywordCategoryInputHandler = null;
let archivesKeywordWordKeydownHandler = null;
let archivesKeywordCategoryKeydownHandler = null;
let archivesKeywordClickOutsideHandler = null;
let archivesSelectedWords = [];
let archivesSelectedCategoryId = null;
let archivesSelectedCategoryText = null;

/**
 * Open Add Keyword Modal from Archives page (Bootstrap version)
 */
export function openAddKeywordModalFromArchives() {
    const modalElement = document.getElementById('addKeywordModal');
    if (!modalElement) {
        console.error('Add keyword modal not found');
        return;
    }
    
    if (typeof bootstrap === 'undefined' || !bootstrap.Modal) {
        console.error('Bootstrap not available');
        return;
    }
    
    const modal = new bootstrap.Modal(modalElement);
    const wordInput = document.getElementById('keywordWordInput');
    const categoryInput = document.getElementById('keywordCategoryInput');
    const wordResults = document.getElementById('keywordWordSearchResults');
    const categoryResults = document.getElementById('keywordCategorySearchResults');
    const selectedWordsContainer = document.getElementById('selectedKeywordWordsContainer');
    const selectedWordsList = document.getElementById('selectedKeywordWordsList');
    const selectedCategoryIdInput = document.getElementById('selectedKeywordCategoryId');
    
    if (!wordInput || !categoryInput || !wordResults || !categoryResults || !selectedWordsList) {
        console.error('Required modal elements not found');
        return;
    }
    
    // Clean up handlers
    if (archivesKeywordClickOutsideHandler) {
        document.removeEventListener('click', archivesKeywordClickOutsideHandler, true);
        archivesKeywordClickOutsideHandler = null;
    }
    if (archivesKeywordWordInputHandler && wordInput) {
        wordInput.removeEventListener('input', archivesKeywordWordInputHandler);
        archivesKeywordWordInputHandler = null;
    }
    if (archivesKeywordCategoryInputHandler && categoryInput) {
        categoryInput.removeEventListener('input', archivesKeywordCategoryInputHandler);
        archivesKeywordCategoryInputHandler = null;
    }
    if (archivesKeywordWordKeydownHandler && wordInput) {
        wordInput.removeEventListener('keydown', archivesKeywordWordKeydownHandler);
        archivesKeywordWordKeydownHandler = null;
    }
    if (archivesKeywordCategoryKeydownHandler && categoryInput) {
        categoryInput.removeEventListener('keydown', archivesKeywordCategoryKeydownHandler);
        archivesKeywordCategoryKeydownHandler = null;
    }
    if (archivesKeywordWordSearchTimeout) {
        clearTimeout(archivesKeywordWordSearchTimeout);
        archivesKeywordWordSearchTimeout = null;
    }
    if (archivesKeywordCategorySearchTimeout) {
        clearTimeout(archivesKeywordCategorySearchTimeout);
        archivesKeywordCategorySearchTimeout = null;
    }
    
    // Reset state
    archivesSelectedWords = [];
    archivesSelectedCategoryId = null;
    archivesSelectedCategoryText = null;
    wordInput.value = '';
    categoryInput.value = '';
    if (selectedCategoryIdInput) selectedCategoryIdInput.value = '';
    wordResults.style.display = 'none';
    wordResults.innerHTML = '';
    categoryResults.style.display = 'none';
    categoryResults.innerHTML = '';
    if (selectedWordsContainer) selectedWordsContainer.style.display = 'none';
    if (selectedWordsList) selectedWordsList.innerHTML = '';
    
    // Initialize word search
    function initializeWordSearch() {
        if (archivesKeywordWordSearchTimeout) {
            clearTimeout(archivesKeywordWordSearchTimeout);
            archivesKeywordWordSearchTimeout = null;
        }
        
        if (archivesKeywordWordInputHandler) {
            wordInput.removeEventListener('input', archivesKeywordWordInputHandler);
        }
        if (archivesKeywordWordKeydownHandler) {
            wordInput.removeEventListener('keydown', archivesKeywordWordKeydownHandler);
        }
        
        archivesKeywordWordInputHandler = function(e) {
            const searchTerm = e.target.value.trim();
            
            if (archivesKeywordWordSearchTimeout) {
                clearTimeout(archivesKeywordWordSearchTimeout);
            }
            
            if (searchTerm.length === 0) {
                wordResults.style.display = 'none';
                wordResults.innerHTML = '';
                return;
            }
            
            archivesKeywordWordSearchTimeout = setTimeout(() => {
                searchWordsForArchivesKeyword(searchTerm);
            }, 300);
        };
        wordInput.addEventListener('input', archivesKeywordWordInputHandler);
        
        archivesKeywordWordKeydownHandler = function(e) {
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                const firstItem = wordResults.querySelector('.word-result-item');
                if (firstItem) {
                    firstItem.focus();
                    firstItem.classList.add('active');
                }
            } else if (e.key === 'Escape') {
                wordResults.style.display = 'none';
            }
        };
        wordInput.addEventListener('keydown', archivesKeywordWordKeydownHandler);
        
        setTimeout(() => {
            wordInput.focus();
        }, 300);
    }
    
    // Initialize category search
    function initializeCategorySearch() {
        if (archivesKeywordCategorySearchTimeout) {
            clearTimeout(archivesKeywordCategorySearchTimeout);
            archivesKeywordCategorySearchTimeout = null;
        }
        
        if (archivesKeywordCategoryInputHandler) {
            categoryInput.removeEventListener('input', archivesKeywordCategoryInputHandler);
        }
        if (archivesKeywordCategoryKeydownHandler) {
            categoryInput.removeEventListener('keydown', archivesKeywordCategoryKeydownHandler);
        }
        
        archivesKeywordCategoryInputHandler = function(e) {
            const searchTerm = e.target.value.trim();
            
            if (archivesKeywordCategorySearchTimeout) {
                clearTimeout(archivesKeywordCategorySearchTimeout);
            }
            
            if (searchTerm.length === 0) {
                categoryResults.style.display = 'none';
                categoryResults.innerHTML = '';
                return;
            }
            
            archivesKeywordCategorySearchTimeout = setTimeout(() => {
                searchCategoriesForArchivesKeyword(searchTerm);
            }, 300);
        };
        categoryInput.addEventListener('input', archivesKeywordCategoryInputHandler);
        
        archivesKeywordCategoryKeydownHandler = function(e) {
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                const firstItem = categoryResults.querySelector('.word-result-item');
                if (firstItem) {
                    firstItem.focus();
                    firstItem.classList.add('active');
                }
            } else if (e.key === 'Escape') {
                categoryResults.style.display = 'none';
            }
        };
        categoryInput.addEventListener('keydown', archivesKeywordCategoryKeydownHandler);
    }
    
    // Search words function
    async function searchWordsForArchivesKeyword(searchTerm) {
        if (!searchTerm || searchTerm.length === 0) {
            wordResults.style.display = 'none';
            return;
        }
        
        try {
            const params = new URLSearchParams({
                q: searchTerm,
                page: 1,
                per_page: 20
            });
            
            const response = await fetch(`/api/words/search?${params.toString()}`);
            const data = await response.json();
            
            if (data.results && data.results.length > 0) {
                renderWordResultsForArchivesKeyword(data.results, searchTerm);
            } else {
                renderWordResultsForArchivesKeyword([], searchTerm);
            }
        } catch (error) {
            console.error('Error searching words:', error);
            wordResults.style.display = 'none';
        }
    }
    
    // Render word results
    function renderWordResultsForArchivesKeyword(results, searchTerm) {
        wordResults.innerHTML = '';
        
        if (results.length > 0) {
            results.forEach((item, index) => {
                const wordId = item.id || item.word_id;
                const wordText = item.text || item.word || '';
                const usageCount = item.usage_count || 0;
                
                // Skip if already selected
                if (archivesSelectedWords.some(w => w.text === wordText)) {
                    return;
                }
                
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
                    selectWordForArchivesKeyword(wordId, wordText);
                });
                
                itemDiv.addEventListener('keydown', function(e) {
                    if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        selectWordForArchivesKeyword(wordId, wordText);
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
        
        // Show option to create new word
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
                selectWordForArchivesKeyword('new', searchTerm);
            });
            
            createDiv.addEventListener('keydown', function(e) {
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    selectWordForArchivesKeyword('new', searchTerm);
                }
            });
            
            wordResults.appendChild(createDiv);
        }
        
        wordResults.style.display = 'block';
    }
    
    // Select word function
    async function selectWordForArchivesKeyword(wordId, wordText) {
        if (wordId === 'new') {
            // Create new word
            try {
                showToastForModal('Creating new word...', 'info');
                const csrfToken = getCSRFTokenForModal() || await getCSRFTokenAsyncForModal();
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
                    showToastForModal('Word created successfully', 'success');
                    wordId = data.id;
                } else {
                    throw new Error(data.error || 'Failed to create word');
                }
            } catch (error) {
                console.error('Error creating word:', error);
                showToastForModal('Error creating word: ' + error.message, 'error');
                return;
            }
        }
        
        // Add to selected words
        if (!archivesSelectedWords.some(w => w.text === wordText)) {
            archivesSelectedWords.push({ id: wordId, text: wordText });
            updateSelectedWordsDisplay();
        }
        
        wordInput.value = '';
        wordResults.style.display = 'none';
        wordInput.focus();
    }
    
    // Update selected words display
    function updateSelectedWordsDisplay() {
        if (!selectedWordsList || !selectedWordsContainer) return;
        
        if (archivesSelectedWords.length === 0) {
            selectedWordsContainer.style.display = 'none';
            selectedWordsList.innerHTML = '';
            return;
        }
        
        selectedWordsContainer.style.display = 'block';
        selectedWordsList.innerHTML = archivesSelectedWords.map((word, index) => `
            <span class="badge bg-primary d-flex align-items-center gap-1" style="font-size: 0.875rem; padding: 0.375rem 0.75rem;" data-word-id="${word.id}" data-word-text="${escapeHtml(word.text)}">
                ${escapeHtml(word.text)}
                <button type="button" class="btn-close btn-close-white" style="font-size: 0.6rem;" data-on-click="removeArchivesSelectedWord(${index})" aria-label="Remove"></button>
            </span>
        `).join('');
    }
    
    // Remove selected word
    window.removeArchivesSelectedWord = function(index) {
        archivesSelectedWords.splice(index, 1);
        updateSelectedWordsDisplay();
    };
    
    // Search categories function
    async function searchCategoriesForArchivesKeyword(searchTerm) {
        if (!searchTerm || searchTerm.length === 0) {
            categoryResults.style.display = 'none';
            return;
        }
        
        try {
            const params = new URLSearchParams({
                q: searchTerm,
                page: 1,
                per_page: 20
            });
            
            const response = await fetch(`/api/categories/search?${params.toString()}`);
            const data = await response.json();
            
            if (data.results && data.results.length > 0) {
                renderCategoryResultsForArchivesKeyword(data.results, searchTerm);
            } else {
                renderCategoryResultsForArchivesKeyword([], searchTerm);
            }
        } catch (error) {
            console.error('Error searching categories:', error);
            categoryResults.style.display = 'none';
        }
    }
    
    // Render category results
    function renderCategoryResultsForArchivesKeyword(results, searchTerm) {
        categoryResults.innerHTML = '';
        
        if (results.length > 0) {
            results.forEach((item, index) => {
                const categoryId = item.id;
                const categoryName = item.name || '';
                const fileCount = item.file_count || 0;
                
                const itemDiv = document.createElement('div');
                itemDiv.className = 'word-result-item';
                itemDiv.tabIndex = 0;
                itemDiv.setAttribute('data-category-id', categoryId);
                itemDiv.setAttribute('data-category-name', categoryName);
                
                itemDiv.innerHTML = `
                    <div class="d-flex align-items-center">
                        <i class="bi bi-check-circle me-2 text-primary"></i>
                        <span class="word-text">${escapeHtml(categoryName)}</span>
                        ${fileCount > 0 ? `<small class="text-muted ms-2">(${fileCount} files)</small>` : ''}
                    </div>
                `;
                
                itemDiv.addEventListener('click', function() {
                    selectCategoryForArchivesKeyword(categoryId, categoryName);
                });
                
                itemDiv.addEventListener('keydown', function(e) {
                    if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        selectCategoryForArchivesKeyword(categoryId, categoryName);
                    } else if (e.key === 'ArrowDown') {
                        e.preventDefault();
                        const next = categoryResults.querySelectorAll('.word-result-item')[index + 1];
                        if (next) {
                            itemDiv.classList.remove('active');
                            next.focus();
                            next.classList.add('active');
                        }
                    } else if (e.key === 'ArrowUp') {
                        e.preventDefault();
                        if (index > 0) {
                            const prev = categoryResults.querySelectorAll('.word-result-item')[index - 1];
                            itemDiv.classList.remove('active');
                            if (prev) {
                                prev.focus();
                                prev.classList.add('active');
                            } else {
                                categoryInput.focus();
                            }
                        }
                    }
                });
                
                categoryResults.appendChild(itemDiv);
            });
        }
        
        // Show option to create new category
        const exactMatch = results.some(item => {
            const categoryName = (item.name || '').toLowerCase();
            return categoryName === searchTerm.toLowerCase();
        });
        
        if (!exactMatch && searchTerm.length > 0 && !searchTerm.match(/^\d+$/)) {
            const createDiv = document.createElement('div');
            createDiv.className = 'word-result-item word-result-create';
            createDiv.tabIndex = 0;
            createDiv.setAttribute('data-category-id', 'new');
            createDiv.setAttribute('data-category-name', searchTerm);
            
            createDiv.innerHTML = `
                <div class="d-flex align-items-center">
                    <i class="bi bi-plus-circle me-2 text-success"></i>
                    <span class="word-text">${escapeHtml(searchTerm)}</span>
                    <small class="text-muted ms-2">(create new)</small>
                </div>
            `;
            
            createDiv.addEventListener('click', function() {
                selectCategoryForArchivesKeyword('new', searchTerm);
            });
            
            createDiv.addEventListener('keydown', function(e) {
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    selectCategoryForArchivesKeyword('new', searchTerm);
                }
            });
            
            categoryResults.appendChild(createDiv);
        }
        
        categoryResults.style.display = 'block';
    }
    
    // Select category function
    async function selectCategoryForArchivesKeyword(categoryId, categoryName) {
        if (categoryId === 'new') {
            // Create new category
            try {
                showToastForModal('Creating new category...', 'info');
                const csrfToken = getCSRFTokenForModal() || await getCSRFTokenAsyncForModal();
                const response = await fetch('/category/add', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': csrfToken
                    },
                    body: JSON.stringify({
                        category_name: categoryName.trim(),
                        csrf_token: csrfToken
                    })
                });
                
                const data = await response.json();
                if (data.success) {
                    showToastForModal('Category created successfully', 'success');
                    categoryId = data.category_id;
                } else {
                    throw new Error(data.error || 'Failed to create category');
                }
            } catch (error) {
                console.error('Error creating category:', error);
                showToastForModal('Error creating category: ' + error.message, 'error');
                return;
            }
        }
        
        archivesSelectedCategoryId = categoryId;
        archivesSelectedCategoryText = categoryName;
        categoryInput.value = categoryName;
        if (selectedCategoryIdInput) selectedCategoryIdInput.value = categoryId;
        categoryResults.style.display = 'none';
        categoryInput.focus();
    }
    
    // Show modal and initialize
    modal.show();
    
    // Wait for modal to be fully shown
    modalElement.addEventListener('shown.bs.modal', function onShown() {
        modalElement.removeEventListener('shown.bs.modal', onShown);
        initializeWordSearch();
        initializeCategorySearch();
        
        // Add click outside handler
        const wordInputContainer = wordInput.closest('.position-relative') || wordInput.parentElement;
        const categoryInputContainer = categoryInput.closest('.position-relative') || categoryInput.parentElement;
        
        archivesKeywordClickOutsideHandler = function(e) {
            const target = e.target;
            if (wordInputContainer && wordResults && 
                !wordInputContainer.contains(target) && 
                !wordResults.contains(target)) {
                wordResults.style.display = 'none';
            }
            if (categoryInputContainer && categoryResults && 
                !categoryInputContainer.contains(target) && 
                !categoryResults.contains(target)) {
                categoryResults.style.display = 'none';
            }
        };
        setTimeout(() => {
            document.addEventListener('click', archivesKeywordClickOutsideHandler, true);
        }, 100);
    }, { once: true });
    
    // Clean up when modal is hidden
    modalElement.addEventListener('hidden.bs.modal', function onHidden() {
        if (archivesKeywordClickOutsideHandler) {
            document.removeEventListener('click', archivesKeywordClickOutsideHandler, true);
            archivesKeywordClickOutsideHandler = null;
        }
        
        if (archivesKeywordWordSearchTimeout) {
            clearTimeout(archivesKeywordWordSearchTimeout);
            archivesKeywordWordSearchTimeout = null;
        }
        if (archivesKeywordCategorySearchTimeout) {
            clearTimeout(archivesKeywordCategorySearchTimeout);
            archivesKeywordCategorySearchTimeout = null;
        }
        
        if (archivesKeywordWordInputHandler && wordInput) {
            wordInput.removeEventListener('input', archivesKeywordWordInputHandler);
            archivesKeywordWordInputHandler = null;
        }
        if (archivesKeywordCategoryInputHandler && categoryInput) {
            categoryInput.removeEventListener('input', archivesKeywordCategoryInputHandler);
            archivesKeywordCategoryInputHandler = null;
        }
        if (archivesKeywordWordKeydownHandler && wordInput) {
            wordInput.removeEventListener('keydown', archivesKeywordWordKeydownHandler);
            archivesKeywordWordKeydownHandler = null;
        }
        if (archivesKeywordCategoryKeydownHandler && categoryInput) {
            categoryInput.removeEventListener('keydown', archivesKeywordCategoryKeydownHandler);
            archivesKeywordCategoryKeydownHandler = null;
        }
        
        if (wordInput) wordInput.value = '';
        if (categoryInput) categoryInput.value = '';
        if (selectedCategoryIdInput) selectedCategoryIdInput.value = '';
        if (wordResults) {
            wordResults.style.display = 'none';
            wordResults.innerHTML = '';
        }
        if (categoryResults) {
            categoryResults.style.display = 'none';
            categoryResults.innerHTML = '';
        }
        if (selectedWordsList) selectedWordsList.innerHTML = '';
        if (selectedWordsContainer) selectedWordsContainer.style.display = 'none';
        
        archivesSelectedWords = [];
        archivesSelectedCategoryId = null;
        archivesSelectedCategoryText = null;
    }, { once: true });
}

/**
 * Save Keyword from Archives function
 */
export async function saveKeywordFromArchives() {
    const wordInput = document.getElementById('keywordWordInput');
    const selectedWordsList = document.getElementById('selectedKeywordWordsList');
    
    if (!wordInput || !selectedWordsList) {
        showToastForModal('Form elements not found', 'error');
        return;
    }
    
    // Get selected words from state (more reliable than parsing badges)
    const selectedWords = archivesSelectedWords.length > 0 ? archivesSelectedWords : [];
    
    // Fallback: get from badges if state is empty
    if (selectedWords.length === 0) {
        const badges = selectedWordsList.querySelectorAll('.badge');
        badges.forEach(badge => {
            const wordText = badge.getAttribute('data-word-text') || badge.textContent.trim().replace('×', '').trim();
            const wordId = badge.getAttribute('data-word-id');
            if (wordText && wordId) {
                selectedWords.push({ id: wordId, text: wordText });
            }
        });
    }
    
    if (selectedWords.length === 0) {
        showToastForModal('At least one word is required', 'warning');
        return;
    }
    
    if (selectedWords.length < 2) {
        showToastForModal('Keywords must contain at least 2 words. Please select multiple words to create a keyword phrase.', 'warning');
        return;
    }
    
    const keywordPhrase = selectedWords.map(w => w.text).join(' ');
    const selectedCategoryIdInput = document.getElementById('selectedKeywordCategoryId');
    const categoryId = selectedCategoryIdInput ? selectedCategoryIdInput.value : '1';
    
    // Check for duplicate
    try {
        const csrfToken = getCSRFTokenForModal() || await getCSRFTokenAsyncForModal();
        const checkResponse = await fetch('/api/keyword/check', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken
            },
            body: JSON.stringify({
                keyword_text: keywordPhrase,
                category_id: categoryId
            })
        });
        
        const checkData = await checkResponse.json();
        if (checkData.exists) {
            showToastForModal(checkData.message || `Keyword "${keywordPhrase}" already exists in this category`, 'error');
            return;
        }
    } catch (error) {
        console.warn('Error checking keyword duplicate:', error);
    }
    
    // Submit keyword
    try {
        showToastForModal('Adding keyword...', 'info');
        const csrfToken = getCSRFTokenForModal() || await getCSRFTokenAsyncForModal();
        const formData = new FormData();
        formData.append('keywords_text', keywordPhrase);
        formData.append('category_id', categoryId);
        
        const response = await fetch('/keywords/add', {
            method: 'POST',
            headers: {
                'X-CSRFToken': csrfToken,
                'X-Requested-With': 'XMLHttpRequest'
            },
            body: formData,
            redirect: 'follow'
        });
        
        const contentType = response.headers.get('content-type') || '';
        let data;
        if (contentType.includes('application/json')) {
            data = await response.json();
        } else {
            data = { success: true, message: 'Keyword added successfully' };
        }
        
        if (data.success) {
            showToastForModal(data.message || 'Keyword added successfully', 'success');
            
            // Close modal
            const modalElement = document.getElementById('addKeywordModal');
            if (modalElement && typeof bootstrap !== 'undefined') {
                const modal = bootstrap.Modal.getInstance(modalElement);
                if (modal) {
                    modal.hide();
                }
            }
            
            // Reload page or refresh view
            setTimeout(() => {
                if (window.loadRootView) {
                    window.loadRootView();
                } else if (typeof window.refreshMainContent === 'function') {
                    window.refreshMainContent();
                } else {
                    window.location.reload();
                }
            }, 500);
        } else {
            showToastForModal(data.error || 'Error adding keyword', 'error');
        }
    } catch (error) {
        console.error('Error adding keyword:', error);
        showToastForModal('Error adding keyword: ' + error.message, 'error');
    }
}

// Make functions available globally
if (typeof window !== 'undefined') {
    window.openAddCategoryModal = openAddCategoryModal;
    window.saveAddCategoryModal = saveAddCategoryModal;
    window.openAddWordsCategorysModal = openAddWordsCategorysModal;
    window.saveWordsCategorys = saveWordsCategorys;
    window.openAddKeywordModalFromArchives = openAddKeywordModalFromArchives;
    window.saveKeywordFromArchives = saveKeywordFromArchives;
}
