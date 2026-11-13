/**
 * Grid/List View Renderers
 * Extracted from the legacy file-management-system.js
 */

import { escapeAttribute, escapeHtml } from '../core/utils.js';
import { translations, sectionLabels } from '../core/config.js';
import { renderGeolocationExportMenu, renderGeolocationMapLink } from '../ui/section-export-menu.js';
import { renderSectionPaginationControls } from './pagination.js';

/**
 * Render grid view for section items
 */
export function renderGridView(items, section, showPagination = false, exportQuery = '') {
    const sectionLabel = sectionLabels[section] || section;
    const headerActions = getSectionHeaderActions(section, exportQuery);

    let html = '<div class="section">';
    html += '<div class="section-header">';
    html += `<div class="section-label"><i class="bi bi-${getSectionIcon(section)}"></i> ${sectionLabel}</div>`;
    html += headerActions;
    html += '</div>';
    html += '<div class="explorer-grid" id="contentGrid" style="max-height: 70vh; overflow-y: auto; overflow-x: hidden;">';

    if (items.length === 0) {
        html += `<div class="empty-state" style="grid-column: 1 / -1; text-align: center; padding: 3rem; color: #64748b;">${translations.noItemsFound || 'No items found'}</div>`;
    } else {
        let renderedCount = 0;

        items.forEach((item, index) => {
            try {
                // For addresses, use the exact database value without any transformation
                let displayName;
                if (section === 'address') {
                    displayName = item.name || 'Unknown Address';
                } else if (section === 'geolocation') {
                    // For geolocation, show coordinates as the main display name if available
                    // Helper function to format coordinates with proper direction indicators
                    const formatCoords = (lat, lon) => {
                        const latNum = parseFloat(lat);
                        const lonNum = parseFloat(lon);
                        const latDir = latNum >= 0 ? 'N' : 'S';
                        const lonDir = lonNum >= 0 ? 'E' : 'W';
                        const latFormatted = Math.abs(latNum).toFixed(6);
                        const lonFormatted = Math.abs(lonNum).toFixed(6);
                        return `📍 ${latFormatted}°${latDir}, ${lonFormatted}°${lonDir}`;
                    };
                    
                    if (item.latitude !== null && item.latitude !== undefined && 
                        item.longitude !== null && item.longitude !== undefined) {
                        displayName = formatCoords(item.latitude, item.longitude);
                    } else if (item.coordinates) {
                        const coords = item.coordinates.trim();
                        const coordMatch = coords.match(/^(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)$/);
                        if (coordMatch) {
                            displayName = formatCoords(coordMatch[1], coordMatch[2]);
                        } else {
                            displayName = `📍 GPS: ${coords}`;
                        }
                    } else {
                        displayName = item.name_display || item.name || 'Unknown Location';
                    }
                } else {
                    displayName = item.name_display || (
                        section === 'hash' && item.name && item.name.length > 32
                            ? item.name.substring(0, 32) + '...'
                            : (item.name || 'Unnamed')
                    );
                }
                const details = getItemDetails(item, section);
                const itemId = item.id || item.ID || null;
                if (!itemId) {
                    return;
                }

                const safeDisplayName = escapeHtml(displayName);
                let groupIndicator = '';
                if (item.is_group && section === 'titles') {
                    groupIndicator = `<span class="badge bg-info ms-2" title="${item.is_identical ? 'Identical' : 'Similar'} titles group">${item.group_count}</span>`;
                }

                let groupDataAttr = '';
                if (item.is_group && section === 'titles' && item.similar_titles) {
                    const titleIds = item.similar_titles.map(st => st.id).filter(id => id).join(',');
                    groupDataAttr = `data-group-title-ids="${titleIds}"`;
                }

                // Generate copy button for geolocation coordinates
                let copyButtonHtml = '';
                if (section === 'geolocation') {
                    // Check if we have valid coordinates to copy
                    const hasCoords = (item.latitude !== null && item.latitude !== undefined && 
                                      item.longitude !== null && item.longitude !== undefined) ||
                                     (item.coordinates && item.coordinates.trim());
                    
                    if (hasCoords) {
                        // Pass both latitude/longitude and raw coordinates string
                        // The copy function will normalize it properly
                        const lat = item.latitude !== null && item.latitude !== undefined ? item.latitude : '';
                        const lon = item.longitude !== null && item.longitude !== undefined ? item.longitude : '';
                        const coordsStr = item.coordinates || '';
                        
                        // Attribute context: escapeAttribute handles quotes and
                        // ampersands, so a coordinate string can never end the
                        // attribute or open a handler of its own.
                        const latAttr = escapeAttribute(String(lat));
                        const lonAttr = escapeAttribute(String(lon));
                        const coordsAttr = escapeAttribute(coordsStr);
                        
                        // If we have parsed lat/lon, pass them; otherwise pass the string
                        if (lat !== '' && lon !== '') {
                            copyButtonHtml = `
                                <button class="btn btn-sm btn-outline-secondary geolocation-copy-btn" 
                                        data-copy-coordinates="${latAttr}" data-copy-longitude="${lonAttr}"
                                        title="${translations.copyCoordinates || 'Copy coordinates'}"
                                        aria-label="${translations.copyCoordinates || 'Copy coordinates'}"
                                        style="position: absolute; top: 0.25rem; right: 0.25rem; padding: 0.25rem 0.5rem; font-size: 0.75rem; z-index: 10;">
                                    <i class="bi bi-clipboard"></i>
                                </button>`;
                        } else if (coordsStr) {
                            copyButtonHtml = `
                                <button class="btn btn-sm btn-outline-secondary geolocation-copy-btn" 
                                        data-copy-coordinates="${coordsAttr}"
                                        title="${translations.copyCoordinates || 'Copy coordinates'}"
                                        aria-label="${translations.copyCoordinates || 'Copy coordinates'}"
                                        style="position: absolute; top: 0.25rem; right: 0.25rem; padding: 0.25rem 0.5rem; font-size: 0.75rem; z-index: 10;">
                                    <i class="bi bi-clipboard"></i>
                                </button>`;
                        }
                    }
                }
                
                html += `
                    <div class="explorer-item" 
                         data-section="${section}" 
                         data-item-id="${itemId}" 
                         data-item-name="${safeDisplayName.replace(/"/g, '&quot;')}"
                         ${item.is_group ? 'data-is-group="true"' : ''}
                         ${groupDataAttr}
                         style="cursor: pointer; position: relative;"
                         role="button"
                         tabindex="0"
                         title="${sectionLabel}: ${safeDisplayName}"
                         aria-label="${sectionLabel}: ${safeDisplayName}">
                        <div class="explorer-item-icon"><i class="bi bi-${getSectionIcon(section)}" aria-hidden="true"></i></div>
                        ${copyButtonHtml}
                        <div class="explorer-item-name" contenteditable="false" data-editable="true" data-item-id="${itemId}" data-section="${section}" data-field="name" data-on-blur="saveItemField?.(this)" data-on-dblclick="enableItemEdit?.(this)" style="padding: 2px 4px; border-radius: 2px; min-height: 1.2em;">
                            ${safeDisplayName}${groupIndicator}
                        </div>
                        <div class="explorer-item-details" contenteditable="false" data-editable="true" data-item-id="${itemId}" data-section="${section}" data-field="details" data-on-blur="saveItemField?.(this)" data-on-dblclick="enableItemEdit?.(this)" style="padding: 2px 4px; border-radius: 2px;">${escapeHtml(details)}</div>
                        ${item.is_group && item.similar_titles ? `
                            <div class="similar-titles-preview" style="margin-top: 0.5rem; font-size: 0.75rem; color: #64748b;">
                                <i class="bi bi-arrow-down-circle" style="cursor: pointer;" data-on-click="toggleSimilarTitles?.(this, ${item.group_id})"></i>
                                <span>${item.group_count} ${item.is_identical ? 'identical' : 'similar'} titles</span>
                                <div class="similar-titles-list" id="similar-titles-${item.group_id}" style="display: none; margin-top: 0.5rem; padding-left: 1rem;">
                                    ${item.similar_titles.map(st => `
                                        <div style="margin: 0.25rem 0;">
                                            <span class="badge bg-secondary">${st.file_count || 0}</span>
                                            ${escapeHtml(st.name || 'Unknown')}
                                        </div>
                                    `).join('')}
                                </div>
                            </div>
                        ` : ''}
                    </div>
                `;
                renderedCount++;
            } catch (error) {
                console.error(`Error rendering item ${index}:`, error);
            }
        });
        console.log(`Rendered ${renderedCount} items in grid view`);
    }

    html += '</div>'; // explorer-grid
    if (showPagination) {
        html += renderSectionPaginationControls(section);
    }
    html += '</div>'; // section
    return html;
}

/**
 * Render list view for section items
 */
export function renderListView(items, section, showPagination = false, exportQuery = '') {
    const sectionLabel = sectionLabels[section] || section;
    const headerActions = getSectionHeaderActions(section, exportQuery);

    let html = '<div class="section">';
    html += '<div class="section-header">';
    html += `<div class="section-label"><i class="bi bi-${getSectionIcon(section)}"></i> ${sectionLabel}</div>`;
    html += headerActions;
    html += '</div>';
    html += '<div class="files-list-view" style="max-height: 70vh; overflow-y: auto; overflow-x: hidden;">';

    if (items.length === 0) {
        html += `<div class="empty-state" style="text-align: center; padding: 3rem; color: #64748b;">${translations.noItemsFound || 'No items found'}</div>`;
    } else {
        items.forEach(item => {
            const displayName = item.name_display || (
                section === 'hash' && item.name && item.name.length > 32
                    ? item.name.substring(0, 32) + '...'
                    : (item.name || 'Unnamed')
            );
            const details = getItemDetails(item, section);
            const itemId = item.id || item.ID || null;
            if (!itemId) return;

            const safeDisplayName = escapeHtml(displayName).replace(/"/g, '&quot;');
            let groupIndicator = '';
            if (item.is_group && section === 'titles') {
                groupIndicator = `<span class="badge bg-info ms-2" title="${item.is_identical ? 'Identical' : 'Similar'} titles group">${item.group_count}</span>`;
            }

            let groupDataAttr = '';
            if (item.is_group && section === 'titles' && item.similar_titles) {
                const titleIds = item.similar_titles.map(st => st.id).filter(id => id).join(',');
                groupDataAttr = `data-group-title-ids="${titleIds}"`;
            }

            html += `
                <div class="file-row-item" 
                     data-section="${section}" 
                     data-item-id="${itemId}" 
                     data-item-name="${safeDisplayName}"
                     ${item.is_group ? 'data-is-group="true"' : ''}
                     ${groupDataAttr}
                     style="cursor: pointer; position: relative;"
                     role="button"
                     tabindex="0"
                     title="${sectionLabel}: ${escapeHtml(displayName)}"
                     aria-label="${sectionLabel}: ${escapeHtml(displayName)}">
                    <div class="file-row-info" style="flex: 1;">
                        <div class="file-row-icon"><i class="bi bi-${getSectionIcon(section)}" aria-hidden="true"></i></div>
                        <div class="file-row-details" style="flex: 1;">
                            <div class="file-row-name" contenteditable="false" data-editable="true" data-item-id="${itemId}" data-section="${section}" data-field="name" data-on-blur="saveItemField?.(this)" data-on-dblclick="enableItemEdit?.(this)" style="padding: 2px 4px; border-radius: 2px; min-height: 1.2em;">
                                ${escapeHtml(displayName)}${groupIndicator}
                            </div>
                            <div class="file-row-meta" contenteditable="false" data-editable="true" data-item-id="${itemId}" data-section="${section}" data-field="details" data-on-blur="saveItemField?.(this)" data-on-dblclick="enableItemEdit?.(this)" style="padding: 2px 4px; border-radius: 2px;">${escapeHtml(details)}</div>
                            ${item.is_group && item.similar_titles ? `
                                <div class="similar-titles-preview" style="margin-top: 0.5rem; font-size: 0.75rem; color: #64748b;">
                                    <i class="bi bi-arrow-down-circle" style="cursor: pointer;" data-on-click="toggleSimilarTitles?.(this, ${item.group_id})"></i>
                                    <span>${item.group_count} ${item.is_identical ? 'identical' : 'similar'} titles</span>
                                    <div class="similar-titles-list" id="similar-titles-${item.group_id}" style="display: none; margin-top: 0.5rem; padding-left: 1rem;">
                                        ${item.similar_titles.map(st => `
                                            <div style="margin: 0.25rem 0;">
                                                <span class="badge bg-secondary">${st.file_count || 0}</span>
                                                ${escapeHtml(st.name || 'Unknown')}
                                            </div>
                                        `).join('')}
                                    </div>
                                </div>
                            ` : ''}
                        </div>
                    </div>
                </div>
            `;
        });
    }

    html += '</div>'; // list view
    if (showPagination) {
        html += renderSectionPaginationControls(section);
    }
    html += '</div>'; // section
    return html;
}

export function getSectionIcon(section) {
    const icons = {
        category: 'folder',
        keywords: 'tags',
        titles: 'file-text',
        sources: 'people',
        sides: 'diagram-3',
        hash: 'hash',
        geolocation: 'geo-alt-fill',
        filetypes: 'file-earmark-binary'
    };
    return icons[section] || 'file';
}

export function getItemDetails(item, section) {
    if (section === 'filetypes') {
        const count = (item.file_count || 0) + ' ' + (translations.files || 'files');
        if (item.total_size) {
            const mb = item.total_size / (1024 * 1024);
            const sizeLabel = mb >= 1
                ? `${mb.toFixed(1)} MB`
                : `${Math.max(1, Math.round(item.total_size / 1024))} KB`;
            return `${count} • ${sizeLabel}`;
        }
        return count;
    } else if (section === 'category') {
        const fileCount = (item.file_count || 0) + ' ' + (translations.files || 'Files');
        const wordCount = (item.word_count || 0) + ' ' + (translations.words || 'Words');
        return fileCount + ' • ' + wordCount;
    } else if (section === 'keywords' || section === 'hash') {
        return (item.file_count || 0) + ' ' + (translations.files || 'files');
    } else if (section === 'address') {
        // For addresses we only want to show the textual address
        // as the main label; suppress numeric counters here.
        return '';
    } else if (section === 'geolocation') {
        // Format geolocation coordinates clearly with proper direction indicators
        const parts = [];
        
        // Helper function to format coordinates with direction
        const formatCoordinates = (lat, lon) => {
            const latNum = parseFloat(lat);
            const lonNum = parseFloat(lon);
            const latDir = latNum >= 0 ? 'N' : 'S';
            const lonDir = lonNum >= 0 ? 'E' : 'W';
            const latFormatted = Math.abs(latNum).toFixed(6);
            const lonFormatted = Math.abs(lonNum).toFixed(6);
            return `📍 ${latFormatted}°${latDir}, ${lonFormatted}°${lonDir}`;
        };
        
        // Format coordinates with clear labels
        if (item.latitude !== null && item.latitude !== undefined && 
            item.longitude !== null && item.longitude !== undefined) {
            // Use parsed latitude/longitude if available
            parts.push(formatCoordinates(item.latitude, item.longitude));
        } else if (item.coordinates) {
            // Fallback to raw coordinates string
            const coords = item.coordinates.trim();
            // Try to parse and format if it's in "lat, lon" format
            const coordMatch = coords.match(/^(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)$/);
            if (coordMatch) {
                parts.push(formatCoordinates(coordMatch[1], coordMatch[2]));
            } else {
                parts.push(`📍 GPS: ${coords}`);
            }
        }
        
        // Add file type if available
        if (item.file_type) {
            parts.push(`📄 ${item.file_type}`);
        }
        
        // Add file count if available
        if (item.file_count !== undefined && item.file_count !== null) {
            parts.push(`(${item.file_count} ${translations.files || 'files'})`);
        }
        
        return parts.join(' • ') || '';
    } else if (section === 'sources') {
        return (item.job || '') + ' - ' + (item.country || '') + ' (' + (item.file_count || 0) + ' ' + (translations.files || 'files') + ')';
    } else if (section === 'sides') {
        return `${translations.importance || 'Importance'}: ${item.importance ? item.importance.toFixed(2) : '0.00'} (${item.file_count || 0} ${translations.files || 'files'})`;
    } else if (section === 'titles') {
        return item.status || '';
    }
    return '';
}

function getSectionHeaderActions(section, exportQuery) {
    const parts = [];
    if (section === 'geolocation') {
        parts.push(renderGeolocationMapLink());
        parts.push(renderGeolocationExportMenu(exportQuery));
    }
    const addButton = getAddButtonHtml(section);
    if (addButton) parts.push(addButton);
    if (!parts.length) return '';
    return `<div class="section-header-actions" style="display:flex;gap:0.5rem;align-items:center;margin-left:auto;">${parts.join('')}</div>`;
}

function getAddButtonHtml(section) {
    const modalMap = {
        sources: 'addSourceModal',
        sides: 'addSideModal',
        keywords: 'addKeywordModal',
        category: 'addCategoryModal'
    };

    const modalId = modalMap[section];
    if (!modalId) return '';

    const buttonLabels = {
        sources: translations.addSource || 'Add Source',
        sides: translations.addSide || 'Add Side',
        keywords: translations.addKeyword || 'Add Keyword',
        category: translations.addCategory || 'Add Category'
    };

    const icons = {
        sources: 'bi-people',
        sides: 'bi-diagram-3',
        keywords: 'bi-key',
        category: 'bi-tags'
    };

    if (section === 'keywords') {
        return `<div style="display: flex; gap: 0.5rem;">
                <button class="add-item-btn" data-on-click="openAddItemModal?.('addKeywordModal', 'keywords')" title="${translations.addKeyword || 'Add Keyword'}" aria-label="${translations.addKeyword || 'Add Keyword'}">
                    <i class="bi bi-key"></i>
                    <span>${translations.addKeyword || 'Add Keyword'}</span>
                </button>
                <button class="add-item-btn" data-on-click="updateKeywordAssociationsShared?.()" id="updateKeywordsBtn" title="${translations.updateKeywordAssociations || 'Update keyword associations for all files'}" aria-label="${translations.updateKeywordAssociations || 'Update keyword associations for all files'}" style="background-color: #10b981;">
                    <i class="bi bi-arrow-repeat"></i>
                    <span>${translations.updateKeywords || 'Update Keywords'}</span>
                </button>
            </div>`;
    }

    return `<button class="add-item-btn" data-on-click="openAddItemModal?.('${modalId}', '${section}')" title="${buttonLabels[section] || 'Add'}" aria-label="${buttonLabels[section] || 'Add'}">
            <i class="bi ${icons[section] || 'bi-plus-circle'}"></i>
            <span>${buttonLabels[section] || 'Add'}</span>
        </button>`;
}

