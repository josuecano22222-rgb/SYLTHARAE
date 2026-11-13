/**
 * Select2 Initialization Functions
 * Handles Select2 dropdown initialization for modals
 * Extracted from file-management-system.js
 */

import { escapeHtml } from '../core/utils.js';

// Get translations from window (set by old file or config)
function getTranslations() {
    return window.translations || window.appData?.translations || {};
}

/**
 * Initialize category word select (Select2)
 */
export function initializeCategoryWordSelect() {
    const translations = getTranslations();
    
    // Check if jQuery and Select2 are available
    if (typeof jQuery === 'undefined' || typeof jQuery.fn.select2 === 'undefined') {
        console.error('jQuery or Select2 is not loaded. Cannot initialize Select2.');
        setTimeout(initializeCategoryWordSelect, 200);
        return;
    }
    
    // Destroy existing Select2 if it exists
    if ($('#categoryName').hasClass('select2-hidden-accessible')) {
        $('#categoryName').select2('destroy');
    }

    // Find the modal container for dropdownParent
    const $select = $('#categoryName');
    const $modal = $select.closest('.archives-modal, .modal, .modal-overlay');
    const dropdownParent = $modal.length > 0 ? $modal : $(document.body);

    $('#categoryName').select2({
        placeholder: translations.placeholderSearchWord || 'Type to search for a word...',
        allowClear: true,
        width: '100%',
        minimumInputLength: 0,
        dropdownParent: dropdownParent,
        language: {
            inputTooShort: function() {
                return '';
            }
        },
        ajax: {
            url: '/api/words/search',
            dataType: 'json',
            delay: 300,
            data: function (params) {
                return {
                    q: params.term || '',
                    page: params.page || 1,
                    per_page: 20
                };
            },
            processResults: function (data, params) {
                params.page = params.page || 1;
                
                const results = (data.results || []).map(function(item) {
                    const wordText = item.text || item.word || String(item.id);
                    return {
                        id: wordText,
                        text: wordText,
                        usage_count: item.usage_count || 0
                    };
                });
                
                return {
                    results: results,
                    pagination: {
                        more: (params.page * 20) < (data.pagination?.total || 0)
                    }
                };
            },
            cache: true
        },
        templateResult: function (data) {
            if (data.loading) {
                return data.text || 'Searching...';
            }
            
            const $result = $('<span>' + escapeHtml(data.text) + '</span>');
            if (data.usage_count && data.usage_count > 0) {
                $result.append(' <small class="text-muted">' + data.usage_count + ' files</small>');
            }
            return $result;
        },
        templateSelection: function (data) {
            if (typeof data === 'string') {
                return data;
            }
            if (data && data.text) {
                return data.text;
            }
            if (data && data.id) {
                return data.id;
            }
            return data || '';
        },
        escapeMarkup: function (markup) {
            return markup;
        }
    });
    
    // Load initial data when dropdown is opened
    $('#categoryName').on('select2:open', function() {
        const $select = $(this);
        const select2 = $select.data('select2');
        if (select2 && select2.dataAdapter) {
            const searchInput = select2.$dropdown.find('input.select2-search__field');
            const searchTerm = searchInput.length ? searchInput.val() || '' : '';
            
            setTimeout(function() {
                if (select2.dataAdapter && !select2.dataAdapter._currentRequest) {
                    select2.dataAdapter.query({
                        term: searchTerm,
                        page: 1
                    }, function(data) {
                        // Data will be processed by processResults automatically
                    });
                }
            }, 50);
        }
    });
    
    $('#categoryName').on('focus', function() {
        const $select = $(this);
        const select2 = $select.data('select2');
        if (select2 && select2.dataAdapter) {
            const $container = $select.next('.select2-container');
            const $results = $container.find('.select2-results__options');
            if ($results.length === 0 || $results.children().length === 0) {
                select2.dataAdapter.query({
                    term: '',
                    page: 1
                }, function(data) {
                    // Data will be processed by processResults
                });
            }
        }
    });
    
    $('#categoryName').on('select2:select', function(e) {
        const $select = $(this);
        setTimeout(function() {
            $select.trigger('change.select2');
        }, 10);
    });
}

/**
 * Initialize keyword words select (Select2 multi-select)
 */
export function initializeKeywordWordsSelect() {
    const translations = getTranslations();
    
    if (typeof jQuery === 'undefined' || typeof jQuery.fn.select2 === 'undefined') {
        console.error('jQuery or Select2 is not loaded. Cannot initialize Select2.');
        setTimeout(initializeKeywordWordsSelect, 200);
        return;
    }
    
    const $select = $('#keywordWords');
    if ($select.length === 0) {
        console.warn('keywordWords select element not found');
        return;
    }
    
    if ($select.hasClass('select2-hidden-accessible')) {
        $select.select2('destroy');
    }

    // Find the modal container for dropdownParent
    const $modal = $select.closest('.archives-modal, .modal, .modal-overlay');
    const dropdownParent = $modal.length > 0 ? $modal : $(document.body);
    
    $select.select2({
        placeholder: translations.placeholderSearchAndSelectWords || "Type to search and select words...",
        allowClear: true,
        width: '100%',
        multiple: true,
        minimumInputLength: 0,
        dropdownParent: dropdownParent,
        ajax: {
            url: '/api/words/search',
            dataType: 'json',
            delay: 300,
            data: function (params) {
                return {
                    q: params.term || '',
                    page: params.page || 1,
                    per_page: 20
                };
            },
            processResults: function (data, params) {
                params.page = params.page || 1;
                
                const results = (data.results || []).map(function(item) {
                    const wordText = item.word || item.text || String(item.id || '');
                    return {
                        id: wordText,
                        text: wordText,
                        word: wordText,
                        word_id: item.word_id || item.id || null,
                        usage_count: item.usage_count || 0
                    };
                });
                
                // Add "Create new word" option if search term doesn't match any result
                const searchTerm = params.term || '';
                if (searchTerm && searchTerm.length >= 2) {
                    const exactMatch = results.some(r => r.text.toLowerCase() === searchTerm.toLowerCase());
                    if (!exactMatch) {
                        results.unshift({
                            id: 'new:' + searchTerm,
                            text: translations.createNewWord || 'Create new word: "' + searchTerm + '"',
                            word: searchTerm,
                            isNew: true
                        });
                    }
                }
                
                return {
                    results: results,
                    pagination: {
                        more: (params.page * 20) < (data.pagination?.total || 0)
                    }
                };
            },
            transport: function (params, success, failure) {
                const $request = $.ajax(params).then(success).fail(function(jqXHR, textStatus, errorThrown) {
                    console.error('Keyword words Select2 AJAX error:', {
                        url: params.url,
                        status: jqXHR.status,
                        statusText: textStatus,
                        error: errorThrown
                    });
                    failure(jqXHR, textStatus, errorThrown);
                });
                return $request;
            },
            cache: true
        },
        templateResult: function (data) {
            if (data.loading) {
                return data.text;
            }
            
            const $result = $('<span>' + escapeHtml(data.text) + '</span>');
            if (data.isNew) {
                $result.addClass('text-primary').prepend('<i class="bi bi-plus-circle me-1"></i>');
            } else if (data.usage_count && data.usage_count > 0) {
                $result.append(' <small class="text-muted">(' + data.usage_count + ' files)</small>');
            }
            return $result;
        },
        templateSelection: function (data) {
            if (typeof data === 'string') {
                return data;
            }
            if (data && data.text) {
                return data.text;
            }
            if (data && data.id) {
                return data.id;
            }
            return data || '';
        },
        escapeMarkup: function (markup) {
            return markup;
        }
    });
    
    $select.on('select2:open', function() {
        const $thisSelect = $(this);
        const select2 = $thisSelect.data('select2');
        
        if (select2 && select2.dataAdapter) {
            setTimeout(function() {
                if (select2.dataAdapter && !select2.dataAdapter._currentRequest) {
                    select2.dataAdapter.query({
                        term: '',
                        page: 1
                    }, function(data) {
                        // Data will be processed by processResults
                    });
                }
            }, 100);
        }
    });
    
    // Handle selection of "Create new word" option
    $select.on('select2:select', function(e) {
        const data = e.params.data;
        if (data && data.isNew && data.id && data.id.startsWith('new:')) {
            const newWord = data.word || data.id.replace('new:', '');
            // Create the word via API
            createNewWord(newWord).then(function(wordId) {
                // Remove the "new:" prefix option and add the actual word
                const currentValues = $select.val() || [];
                const newValues = currentValues.filter(v => !v.startsWith('new:'));
                newValues.push(newWord);
                $select.val(newValues).trigger('change');
            }).catch(function(error) {
                console.error('Error creating new word:', error);
                // Remove the "new:" option from selection
                const currentValues = $select.val() || [];
                const newValues = currentValues.filter(v => !v.startsWith('new:'));
                $select.val(newValues).trigger('change');
            });
        }
    });
}

// Helper function to create a new word
async function createNewWord(wordText) {
    const { apiPost } = await import('../api/api-client.js');
    const response = await apiPost('/api/words', { word: wordText });
    if (response.success) {
        return response.id;
    } else {
        throw new Error(response.error || 'Failed to create word');
    }
}

/**
 * Initialize keyword category select (Select2)
 */
export function initializeKeywordCategorySelect() {
    const translations = getTranslations();
    
    if (typeof jQuery === 'undefined' || typeof jQuery.fn.select2 === 'undefined') {
        console.error('jQuery or Select2 is not loaded. Cannot initialize Select2.');
        setTimeout(initializeKeywordCategorySelect, 200);
        return;
    }
    
    const $select = $('#keywordCategory');
    if ($select.length === 0) {
        console.warn('keywordCategory select element not found');
        return;
    }
    
    if ($select.hasClass('select2-hidden-accessible')) {
        $select.select2('destroy');
    }

    // Find the modal container for dropdownParent
    const $modal = $select.closest('.archives-modal, .modal, .modal-overlay');
    const dropdownParent = $modal.length > 0 ? $modal : $(document.body);
    
    $select.select2({
        placeholder: translations.placeholderSearchCategory || "Type to search for a category...",
        allowClear: true,
        width: '100%',
        minimumInputLength: 0,
        dropdownParent: dropdownParent,
        ajax: {
            url: '/api/categories/search',
            dataType: 'json',
            delay: 300,
            data: function (params) {
                return {
                    q: params.term || '',
                    page: params.page || 1,
                    per_page: 20
                };
            },
            processResults: function (data, params) {
                params.page = params.page || 1;
                
                let items = [];
                if (Array.isArray(data)) {
                    items = data;
                } else if (data.results || data.items) {
                    items = data.results || data.items || [];
                }
                
                const results = items.map(function(item) {
                    return {
                        id: item.id,
                        text: item.name || item.word || 'Unnamed',
                        file_count: item.file_count || 0
                    };
                });
                
                // Add "Create new category" option if search term doesn't match any result
                const searchTerm = params.term || '';
                if (searchTerm && searchTerm.length >= 2) {
                    const exactMatch = results.some(r => r.text.toLowerCase() === searchTerm.toLowerCase());
                    if (!exactMatch) {
                        results.unshift({
                            id: 'new:' + searchTerm,
                            text: translations.createNewCategory || 'Create new category: "' + searchTerm + '"',
                            name: searchTerm,
                            isNew: true
                        });
                    }
                }
                
                const total = data.total || data.pagination?.total || items.length;
                
                return {
                    results: results,
                    pagination: {
                        more: (params.page * 20) < total
                    }
                };
            },
            transport: function (params, success, failure) {
                const $request = $.ajax(params).then(success).fail(function(jqXHR, textStatus, errorThrown) {
                    console.error('Keyword category Select2 AJAX error:', {
                        url: params.url,
                        status: jqXHR.status,
                        statusText: textStatus,
                        error: errorThrown
                    });
                    failure(jqXHR, textStatus, errorThrown);
                });
                return $request;
            },
            cache: true
        },
        templateResult: function (data) {
            if (data.loading) {
                return data.text;
            }
            
            const $result = $('<span>' + escapeHtml(data.text) + '</span>');
            if (data.isNew) {
                $result.addClass('text-primary').prepend('<i class="bi bi-plus-circle me-1"></i>');
            } else if (data.file_count && data.file_count > 0) {
                $result.append(' <small class="text-muted">(' + data.file_count + ' files)</small>');
            }
            return $result;
        },
        templateSelection: function (data) {
            if (typeof data === 'string') {
                return data;
            }
            if (data && data.text) {
                return data.text;
            }
            if (data && data.id) {
                return data.id;
            }
            return data || '';
        },
        escapeMarkup: function (markup) {
            return markup;
        }
    });
    
    $select.on('select2:open', function() {
        const $thisSelect = $(this);
        const select2 = $thisSelect.data('select2');
        
        if (select2 && select2.dataAdapter) {
            setTimeout(function() {
                if (select2.dataAdapter && !select2.dataAdapter._currentRequest) {
                    select2.dataAdapter.query({
                        term: '',
                        page: 1
                    }, function(data) {
                        // Data will be processed by processResults
                    });
                }
            }, 100);
        }
    });
    
    // Handle selection of "Create new category" option
    $select.on('select2:select', function(e) {
        const data = e.params.data;
        if (data && data.isNew && data.id && data.id.startsWith('new:')) {
            const newCategoryName = data.name || data.id.replace('new:', '');
            // Create the category via API
            createNewCategory(newCategoryName).then(function(categoryId) {
                // Remove the "new:" prefix option and add the actual category
                $select.val(categoryId).trigger('change');
            }).catch(function(error) {
                console.error('Error creating new category:', error);
                // Clear selection on error
                $select.val(null).trigger('change');
            });
        }
    });
}

// Helper function to create a new category
async function createNewCategory(categoryName) {
    const { apiPost } = await import('../api/api-client.js');
    
    // Check for duplicate before creating
    try {
        const checkResponse = await apiPost('/api/category/check', { category_name: categoryName });
        if (checkResponse.exists) {
            throw new Error(checkResponse.message || `Category "${categoryName}" already exists`);
        }
    } catch (error) {
        // If it's a duplicate error, throw it
        if (error.message && error.message.includes('already exists')) {
            throw error;
        }
        // Otherwise, continue (backend will catch duplicates)
        console.warn('Error checking category duplicate:', error);
    }
    
    const response = await apiPost('/category/add', { category_name: categoryName });
    if (response.success) {
        return response.category_id || response.id;
    } else {
        throw new Error(response.error || 'Failed to create category');
    }
}

/**
 * Initialize words_categorys word select (Select2)
 */
export function initializeWordsCategorysWordSelect() {
    const translations = getTranslations();
    
    if (typeof jQuery === 'undefined') {
        console.error('jQuery is not loaded. Cannot initialize Select2.');
        return;
    }
    if (typeof jQuery.fn.select2 === 'undefined') {
        console.error('Select2 is not loaded. Please wait for Select2 to load.');
        setTimeout(initializeWordsCategorysWordSelect, 200);
        return;
    }
    
    if ($('#wordsCategorysWordId').hasClass('select2-hidden-accessible')) {
        $('#wordsCategorysWordId').select2('destroy');
    }

    // Find the modal container for dropdownParent
    const $wordSelect = $('#wordsCategorysWordId');
    const $modal = $wordSelect.closest('.archives-modal, .modal, .modal-overlay');
    const dropdownParent = $modal.length > 0 ? $modal : $(document.body);

    $('#wordsCategorysWordId').select2({
        placeholder: translations.placeholderSearchWord || 'Type to search for a word...',
        allowClear: true,
        width: '100%',
        minimumInputLength: 0,
        dropdownParent: dropdownParent,
        ajax: {
            url: '/api/words/search',
            dataType: 'json',
            delay: 300,
            data: function (params) {
                return {
                    q: params.term || '',
                    page: params.page || 1,
                    per_page: 20
                };
            },
            processResults: function (data, params) {
                params.page = params.page || 1;
                
                const results = (data.results || []).map(function(item) {
                    const wordId = item.id || item.word_id;
                    const wordText = item.text || item.word || String(wordId || '');
                    
                    return {
                        id: wordId,
                        text: wordText,
                        usage_count: item.usage_count || 0
                    };
                });
                
                return {
                    results: results,
                    pagination: {
                        more: (params.page * 20) < (data.pagination?.total || 0)
                    }
                };
            },
            cache: true
        },
        templateResult: function (data) {
            if (data.loading) {
                return data.text;
            }
            
            const $result = $('<span>' + escapeHtml(data.text) + '</span>');
            if (data.usage_count && data.usage_count > 0) {
                $result.append(' <small class="text-muted">' + data.usage_count + ' files</small>');
            }
            return $result;
        },
        templateSelection: function (data) {
            if (typeof data === 'string') {
                return data;
            }
            if (data && data.text) {
                return data.text;
            }
            if (data && data.id) {
                return data.id;
            }
            return data || '';
        },
        escapeMarkup: function (markup) {
            return markup;
        }
    });
    
    $('#wordsCategorysWordId').on('select2:open', function() {
        const select2 = $(this).data('select2');
        if (select2 && select2.dataAdapter) {
            const currentData = select2.data();
            if (!currentData || currentData.length === 0) {
                $(this).trigger('input');
            }
        }
    });
    
    setTimeout(function() {
        const $select = $('#wordsCategorysWordId');
        if ($select.length && $select.data('select2')) {
            const select2 = $select.data('select2');
            if (select2 && select2.dataAdapter) {
                select2.dataAdapter.query({
                    term: '',
                    page: 1
                }, function(data) {
                    if (select2 && select2._results) {
                        select2._results.update(data);
                    }
                });
            }
        }
    }, 500);
}

/**
 * Initialize words_categorys category select (Select2)
 */
export function initializeWordsCategorysCategorySelect() {
    const translations = getTranslations();
    
    if (typeof jQuery === 'undefined') {
        console.error('jQuery is not loaded. Cannot initialize Select2.');
        return;
    }
    if (typeof jQuery.fn.select2 === 'undefined') {
        console.error('Select2 is not loaded. Please wait for Select2 to load.');
        setTimeout(initializeWordsCategorysCategorySelect, 200);
        return;
    }
    
    if ($('#wordsCategorysCategoryId').hasClass('select2-hidden-accessible')) {
        $('#wordsCategorysCategoryId').select2('destroy');
    }

    // Find the modal container for dropdownParent
    const $categorySelect = $('#wordsCategorysCategoryId');
    const $modal = $categorySelect.closest('.archives-modal, .modal, .modal-overlay');
    const dropdownParent = $modal.length > 0 ? $modal : $(document.body);

    $('#wordsCategorysCategoryId').select2({
        placeholder: translations.placeholderSearchCategory || "Type to search for a category...",
        allowClear: true,
        width: '100%',
        minimumInputLength: 0,
        dropdownParent: dropdownParent,
        ajax: {
            url: '/api/categories/search',
            dataType: 'json',
            delay: 300,
            data: function (params) {
                return {
                    q: params.term || '',
                    page: params.page || 1,
                    per_page: 20
                };
            },
            processResults: function (data, params) {
                params.page = params.page || 1;
                
                let items = [];
                if (Array.isArray(data)) {
                    items = data;
                } else if (data.results || data.items) {
                    items = data.results || data.items || [];
                }
                
                const results = items.map(function(item) {
                    return {
                        id: item.id,
                        text: item.name || item.word || 'Unnamed',
                        file_count: item.file_count || 0
                    };
                });
                
                const total = data.total || data.pagination?.total || items.length;
                
                return {
                    results: results,
                    pagination: {
                        more: (params.page * 20) < total
                    }
                };
            },
            cache: true
        },
        templateResult: function (data) {
            if (data.loading) {
                return data.text;
            }
            
            const $result = $('<span>' + escapeHtml(data.text) + '</span>');
            if (data.file_count && data.file_count > 0) {
                $result.append(' <small class="text-muted">' + data.file_count + 'files</small>');
            }
            return $result;
        },
        templateSelection: function (data) {
            if (typeof data === 'string') {
                return data;
            }
            if (data && data.text) {
                return data.text;
            }
            if (data && data.id) {
                return data.id;
            }
            return data || '';
        },
        escapeMarkup: function (markup) {
            return markup;
        }
    });
    
    $('#wordsCategorysCategoryId').on('select2:open', function() {
        const select2 = $(this).data('select2');
        if (select2 && select2.dataAdapter) {
            // Data should already be cached
        }
    });
    
    setTimeout(function() {
        const $select = $('#wordsCategorysCategoryId');
        if ($select.length && $select.data('select2')) {
            const select2 = $select.data('select2');
            if (select2 && select2.dataAdapter) {
                select2.dataAdapter.query({
                    term: '',
                    page: 1
                }, function(data) {
                    // Data is now cached
                });
            }
        }
    }, 500);
}

// Backward compatibility - expose on window
if (typeof window !== 'undefined') {
    window.initializeCategoryWordSelect = initializeCategoryWordSelect;
    window.initializeKeywordWordsSelect = initializeKeywordWordsSelect;
    window.initializeKeywordCategorySelect = initializeKeywordCategorySelect;
    window.initializeWordsCategorysWordSelect = initializeWordsCategorysWordSelect;
    window.initializeWordsCategorysCategorySelect = initializeWordsCategorysCategorySelect;
}

export default {
    initializeCategoryWordSelect,
    initializeKeywordWordsSelect,
    initializeKeywordCategorySelect,
    initializeWordsCategorysWordSelect,
    initializeWordsCategorysCategorySelect
};

