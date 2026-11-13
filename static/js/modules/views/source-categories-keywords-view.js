/**
 * Source/Side Categories and Keywords View
 * Shows categories and keywords for a source or side, allowing navigation to files
 */

import { navigationState } from '../core/state.js';
import { sectionLabels, translations } from '../core/config.js';
import { endpoints } from '../api/endpoints.js';
import { apiGet } from '../api/api-client.js';
import { escapeHtml } from '../core/utils.js';
import { loadItemFilesWithFilters } from './item-view.js';

/**
 * Load and display categories and keywords for a source or side
 */
export async function loadSourceCategoriesKeywordsView(section, itemId, itemName = null, page = 1) {
    const contentView = document.getElementById('unifiedContentView');
    if (!contentView) return;
    
    contentView.innerHTML = `<div class="content-loading"><i class="bi bi-arrow-repeat"></i><div>${translations.loading || 'Loading...'}</div></div>`;

    try {
        // Determine which endpoint to use based on section
        const endpoint = section === 'sources' 
            ? endpoints.sourceCategoriesKeywords(itemId, { page })
            : endpoints.sideCategoriesKeywords(itemId, { page });
        
        console.log('Loading categories/keywords from:', endpoint);
        
        const data = await apiGet(endpoint);
        console.log('Categories/Keywords data received:', data);
        
        if (!data.success) {
            throw new Error(data.error || 'Failed to load categories and keywords');
        }

        // Store context for navigation
        navigationState.currentSourceId = section === 'sources' ? itemId : null;
        navigationState.currentSideId = section === 'sides' ? itemId : null;
        navigationState.currentSourceSection = section;
        navigationState.currentSourceItemId = itemId;
        navigationState.currentSourceItemName = itemName;

        // Render the view
        renderSourceCategoriesKeywordsView(
            data.categories || [],
            data.keywords || [],
            section,
            itemId,
            itemName || sectionLabels[section] || 'Item',
            data.pagination || {}
        );
    } catch (error) {
        console.error('Error loading categories/keywords:', error);
        contentView.innerHTML = `<div class="empty-state">${translations.errorLoadingItems || 'Error loading items'}: ${error.message}</div>`;
    }
}

/**
 * Render categories and keywords view
 */
function renderSourceCategoriesKeywordsView(categories, keywords, section, itemId, itemName, pagination) {
    const contentView = document.getElementById('unifiedContentView');
    if (!contentView) return;

    const sectionLabel = sectionLabels[section] || section;
    const sectionType = section === 'sources' ? 'source' : 'side';
    const sectionIdParam = section === 'sources' ? 'source_id' : 'side_id';

    let html = `
        <div class="source-categories-keywords-view" style="padding: 1.5rem;">
            <div class="view-header" style="margin-bottom: 2rem; border-bottom: 2px solid var(--border-color, #e2e8f0); padding-bottom: 1rem;">
                <h2 style="margin: 0; font-size: 1.5rem; font-weight: 600;">
                    <i class="bi bi-${section === 'sources' ? 'people' : 'diagram-3'}"></i>
                    ${escapeHtml(itemName)}
                </h2>
                <p style="margin: 0.5rem 0 0 0; color: var(--text-muted, #64748b);">
                    ${translations.categories || 'Categories'} & ${translations.keywords || 'Keywords'}
                </p>
            </div>

            <div class="categories-section" style="margin-bottom: 3rem;">
                <h3 style="font-size: 1.25rem; font-weight: 600; margin-bottom: 1rem; display: flex; align-items: center; gap: 0.5rem;">
                    <i class="bi bi-folder-fill" style="color: var(--primary-color, #3b82f6);"></i>
                    ${translations.categories || 'Categories'}
                    <span class="badge bg-secondary" style="margin-left: 0.5rem;">${categories.length}</span>
                </h3>
    `;

    if (categories.length === 0) {
        html += `
            <div class="empty-state" style="padding: 2rem; text-align: center; color: var(--text-muted, #64748b); background: var(--bg-section, #f8fafc); border-radius: 8px;">
                <i class="bi bi-folder-x" style="font-size: 2rem; margin-bottom: 0.5rem; opacity: 0.5;"></i>
                <p>${translations.noCategoriesAssigned || 'No categories found for this ' + sectionType}</p>
            </div>
        `;
    } else {
        html += '<div class="categories-grid" style="display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 1rem;">';
        
        categories.forEach(category => {
            const categoryName = escapeHtml(category.name || 'Unnamed Category');
            const fileCount = category.file_count || 0;
            
            html += `
                <div class="category-card" 
                     data-section="category" 
                     data-item-id="${category.id}" 
                     data-item-name="${categoryName}"
                     data-${sectionIdParam}="${itemId}"
                     style="border: 1px solid var(--border-color, #e2e8f0); border-radius: 8px; padding: 1rem; cursor: pointer; transition: all 0.2s; background: var(--bg-section, #ffffff);"
                     data-on-mouseover="this.style.borderColor='var(--primary-color, #3b82f6)'; this.style.boxShadow='0 2px 8px rgba(59, 130, 246, 0.1)';"
                     data-on-mouseout="this.style.borderColor='var(--border-color, #e2e8f0)'; this.style.boxShadow='none';">
                    <div style="display: flex; align-items: center; gap: 0.75rem; margin-bottom: 0.75rem;">
                        <i class="bi bi-folder-fill" style="font-size: 1.5rem; color: var(--primary-color, #3b82f6);"></i>
                        <h4 style="margin: 0; font-size: 1rem; font-weight: 600; flex: 1;">${categoryName}</h4>
                    </div>
                    <div style="display: flex; align-items: center; gap: 0.5rem; color: var(--text-muted, #64748b); font-size: 0.875rem;">
                        <i class="bi bi-file-earmark"></i>
                        <span>${fileCount} ${translations.files || 'files'}</span>
                    </div>
                </div>
            `;
        });
        
        html += '</div>';
    }

    html += `
            </div>

            <div class="keywords-section">
                <h3 style="font-size: 1.25rem; font-weight: 600; margin-bottom: 1rem; display: flex; align-items: center; gap: 0.5rem;">
                    <i class="bi bi-tag-fill" style="color: var(--primary-color, #3b82f6);"></i>
                    ${translations.keywords || 'Keywords'}
                    <span class="badge bg-secondary" style="margin-left: 0.5rem;">${keywords.length}</span>
                </h3>
    `;

    if (keywords.length === 0) {
        html += `
            <div class="empty-state" style="padding: 2rem; text-align: center; color: var(--text-muted, #64748b); background: var(--bg-section, #f8fafc); border-radius: 8px;">
                <i class="bi bi-tag" style="font-size: 2rem; margin-bottom: 0.5rem; opacity: 0.5;"></i>
                <p>${translations.noKeywordsFound || 'No keywords found for this ' + sectionType}</p>
            </div>
        `;
    } else {
        html += '<div class="keywords-grid" style="display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 1rem;">';
        
        keywords.forEach(keyword => {
            const keywordName = escapeHtml(keyword.name || 'Unnamed Keyword');
            const fileCount = keyword.file_count || 0;
            
            html += `
                <div class="keyword-card" 
                     data-section="keywords" 
                     data-item-id="${keyword.id}" 
                     data-item-name="${keywordName}"
                     data-${sectionIdParam}="${itemId}"
                     style="border: 1px solid var(--border-color, #e2e8f0); border-radius: 8px; padding: 1rem; cursor: pointer; transition: all 0.2s; background: var(--bg-section, #ffffff);"
                     data-on-mouseover="this.style.borderColor='var(--primary-color, #3b82f6)'; this.style.boxShadow='0 2px 8px rgba(59, 130, 246, 0.1)';"
                     data-on-mouseout="this.style.borderColor='var(--border-color, #e2e8f0)'; this.style.boxShadow='none';">
                    <div style="display: flex; align-items: center; gap: 0.75rem; margin-bottom: 0.75rem;">
                        <i class="bi bi-tag-fill" style="font-size: 1.5rem; color: var(--primary-color, #3b82f6);"></i>
                        <h4 style="margin: 0; font-size: 1rem; font-weight: 600; flex: 1;">${keywordName}</h4>
                    </div>
                    <div style="display: flex; align-items: center; gap: 0.5rem; color: var(--text-muted, #64748b); font-size: 0.875rem;">
                        <i class="bi bi-file-earmark"></i>
                        <span>${fileCount} ${translations.files || 'files'}</span>
                    </div>
                </div>
            `;
        });
        
        html += '</div>';
    }

    html += `
            </div>
        </div>
    `;

    contentView.innerHTML = html;

    // Add click handlers for categories and keywords
    setupCategoryKeywordClickHandlers();
}

/**
 * Setup click handlers for category and keyword cards
 */
function setupCategoryKeywordClickHandlers() {
    const cards = document.querySelectorAll('.category-card, .keyword-card');
    
    cards.forEach(card => {
        card.addEventListener('click', function(e) {
            e.preventDefault();
            e.stopPropagation();
            
            const section = this.getAttribute('data-section');
            const itemId = parseInt(this.getAttribute('data-item-id'));
            const itemName = this.getAttribute('data-item-name');
            const sourceId = this.getAttribute('data-source_id');
            const sideId = this.getAttribute('data-side_id');
            
            console.log('Category/Keyword clicked:', { section, itemId, itemName, sourceId, sideId });
            
            // Use loadItemFilesWithFilters with filters
            const sourceFilter = sourceId ? { source_id: parseInt(sourceId) } : null;
            const sideFilter = sideId ? { side_id: parseInt(sideId) } : null;
            
            loadItemFilesWithFilters(section, itemId, itemName, sourceFilter, sideFilter, 1);
        });
    });
}

