/**
 * Source Categories & Keywords Page
 * Displays categories and keywords for a specific source with file counts
 */

import { escapeHtml } from '../modules/core/utils.js';

document.addEventListener('DOMContentLoaded', function() {
    const pageDataEl = document.getElementById('source-categories-keywords-page-data');
    if (!pageDataEl) {
        console.error('Page data not found');
        return;
    }
    
    let pageData;
    try {
        pageData = JSON.parse(pageDataEl.textContent);
    } catch (e) {
        console.error('Error parsing page data:', e);
        return;
    }
    
    const sourceId = pageData.source_id;
    const translations = pageData.translations || {};
    let currentCategoriesPage = 1;
    let currentKeywordsPage = 1;
    const itemsPerPage = 10;
    
    // Load categories and keywords
    loadCategories(sourceId, currentCategoriesPage);
    loadKeywords(sourceId, currentKeywordsPage);
    
    function loadCategories(sourceId, page) {
        const container = document.getElementById('categoriesContainer');
        const paginationContainer = document.getElementById('categoriesPagination');
        
        container.innerHTML = '<div class="text-center p-4"><div class="spinner-border text-primary" role="status"><span class="visually-hidden">Loading...</span></div></div>';
        
        fetch(`/api/archives/source-categories-keywords?source_id=${sourceId}&page=${page}&limit=${itemsPerPage}`)
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    renderCategories(data.categories, container);
                    renderPagination(data.pagination.categories, paginationContainer, 'categories', page => {
                        currentCategoriesPage = page;
                        loadCategories(sourceId, page);
                    });
                } else {
                    container.innerHTML = `<div class="alert alert-warning">${escapeHtml(data.error || translations.noCategories || 'No categories found')}</div>`;
                }
            })
            .catch(error => {
                console.error('Error loading categories:', error);
                container.innerHTML = `<div class="alert alert-danger">Error loading categories: ${error.message}</div>`;
            });
    }
    
    function loadKeywords(sourceId, page) {
        const container = document.getElementById('keywordsContainer');
        const paginationContainer = document.getElementById('keywordsPagination');
        
        container.innerHTML = '<div class="text-center p-4"><div class="spinner-border text-primary" role="status"><span class="visually-hidden">Loading...</span></div></div>';
        
        fetch(`/api/archives/source-categories-keywords?source_id=${sourceId}&page=${page}&limit=${itemsPerPage}`)
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    renderKeywords(data.keywords, container);
                    renderPagination(data.pagination.keywords, paginationContainer, 'keywords', page => {
                        currentKeywordsPage = page;
                        loadKeywords(sourceId, page);
                    });
                } else {
                    container.innerHTML = `<div class="alert alert-warning">${escapeHtml(data.error || translations.noKeywords || 'No keywords found')}</div>`;
                }
            })
            .catch(error => {
                console.error('Error loading keywords:', error);
                container.innerHTML = `<div class="alert alert-danger">Error loading keywords: ${error.message}</div>`;
            });
    }
    
    function renderCategories(categories, container) {
        if (!categories || categories.length === 0) {
            container.innerHTML = `<div class="alert alert-info">${translations.noCategories || 'No categories found'}</div>`;
            return;
        }
        
        let html = '<div class="list-group">';
        categories.forEach(category => {
            html += `
                <a href="#" data-section="category" data-id="${category.id}" 
                   class="list-group-item list-group-item-action d-flex justify-content-between align-items-center category-keyword-link">
                    <div>
                        <h6 class="mb-1">${escapeHtml(category.name)}</h6>
                    </div>
                    <span class="badge bg-primary rounded-pill">${category.file_count} ${translations.files || 'files'}</span>
                </a>
            `;
        });
        html += '</div>';
        container.innerHTML = html;
        
        // Add click handlers
        container.querySelectorAll('.category-keyword-link').forEach(link => {
            link.addEventListener('click', function(e) {
                e.preventDefault();
                const section = this.getAttribute('data-section');
                const id = this.getAttribute('data-id');
                const name = this.querySelector('h6').textContent;
                
                // Try to use the unified view system if available
                if (window.fms && window.fms.loadItemView) {
                    window.fms.loadItemView(section, parseInt(id), name, 1);
                } else if (window.loadItemView) {
                    window.loadItemView(section, parseInt(id), name, 1);
                } else {
                    // Fallback: navigate to archives page with filter
                    if (window.swapNavigate) { window.swapNavigate(`/archives?section=${section}&id=${id}`); } else { window.location.href = `/archives?section=${section}&id=${id}`; }
                }
            });
        });
    }
    
    function renderKeywords(keywords, container) {
        if (!keywords || keywords.length === 0) {
            container.innerHTML = `<div class="alert alert-info">${translations.noKeywords || 'No keywords found'}</div>`;
            return;
        }
        
        let html = '<div class="list-group">';
        keywords.forEach(keyword => {
            html += `
                <a href="#" data-section="keywords" data-id="${keyword.id}" 
                   class="list-group-item list-group-item-action d-flex justify-content-between align-items-center category-keyword-link">
                    <div>
                        <h6 class="mb-1">${escapeHtml(keyword.name)}</h6>
                    </div>
                    <span class="badge bg-primary rounded-pill">${keyword.file_count} ${translations.files || 'files'}</span>
                </a>
            `;
        });
        html += '</div>';
        container.innerHTML = html;
        
        // Add click handlers
        container.querySelectorAll('.category-keyword-link').forEach(link => {
            link.addEventListener('click', function(e) {
                e.preventDefault();
                const section = this.getAttribute('data-section');
                const id = this.getAttribute('data-id');
                const name = this.querySelector('h6').textContent;
                
                // Try to use the unified view system if available
                if (window.fms && window.fms.loadItemView) {
                    window.fms.loadItemView(section, parseInt(id), name, 1);
                } else if (window.loadItemView) {
                    window.loadItemView(section, parseInt(id), name, 1);
                } else {
                    // Fallback: navigate to archives page with filter
                    if (window.swapNavigate) { window.swapNavigate(`/archives?section=${section}&id=${id}`); } else { window.location.href = `/archives?section=${section}&id=${id}`; }
                }
            });
        });
    }
    
    function renderPagination(pagination, container, type, onPageChange) {
        if (!pagination || pagination.total_pages <= 1) {
            container.innerHTML = '';
            return;
        }
        
        // Ensure container has an ID
        if (!container.id) {
            container.id = `pagination-${type}`;
        }
        
        // Use unified pagination
        import('../modules/rendering/unified-pagination.js').then(module => {
            module.renderUnifiedPagination({
                currentPage: pagination.page,
                totalPages: pagination.total_pages,
                containerId: container.id,
                onPageChange: (targetPage) => {
                    onPageChange(targetPage);
                },
                urlParams: {},
                showInfo: true,
                showJump: pagination.total_pages > 5,
                baseUrl: window.location.pathname
            });
        }).catch(err => {
            console.error('Error loading unified pagination:', err);
            // Fallback to old pagination
            renderOldPagination(pagination, container, onPageChange);
        });
    }
    
    function renderOldPagination(pagination, container, onPageChange) {
        let html = '<ul class="pagination justify-content-center">';
        if (pagination.has_prev) {
            html += `<li class="page-item"><a class="page-link" href="#" data-page="${pagination.page - 1}">Previous</a></li>`;
        } else {
            html += `<li class="page-item disabled"><span class="page-link">Previous</span></li>`;
        }
        const startPage = Math.max(1, pagination.page - 2);
        const endPage = Math.min(pagination.total_pages, pagination.page + 2);
        if (startPage > 1) {
            html += `<li class="page-item"><a class="page-link" href="#" data-page="1">1</a></li>`;
            if (startPage > 2) html += `<li class="page-item disabled"><span class="page-link">...</span></li>`;
        }
        for (let i = startPage; i <= endPage; i++) {
            if (i === pagination.page) {
                html += `<li class="page-item active"><span class="page-link">${i}</span></li>`;
            } else {
                html += `<li class="page-item"><a class="page-link" href="#" data-page="${i}">${i}</a></li>`;
            }
        }
        if (endPage < pagination.total_pages) {
            if (endPage < pagination.total_pages - 1) html += `<li class="page-item disabled"><span class="page-link">...</span></li>`;
            html += `<li class="page-item"><a class="page-link" href="#" data-page="${pagination.total_pages}">${pagination.total_pages}</a></li>`;
        }
        if (pagination.has_next) {
            html += `<li class="page-item"><a class="page-link" href="#" data-page="${pagination.page + 1}">Next</a></li>`;
        } else {
            html += `<li class="page-item disabled"><span class="page-link">Next</span></li>`;
        }
        html += '</ul>';
        container.innerHTML = html;
        container.querySelectorAll('a.page-link').forEach(link => {
            link.addEventListener('click', function(e) {
                e.preventDefault();
                const page = parseInt(this.getAttribute('data-page'));
                if (page && page !== pagination.page) {
                    onPageChange(page);
                }
            });
        });
    }
    
});

