/**
 * Global Event Delegation System
 * Handles clicks on dynamically rendered items for better performance
 * 
 * This matches the old code's behavior - event listener is attached immediately
 * when the module loads, not waiting for DOMContentLoaded
 */

/**
 * Initialize global event delegation
 * Exported for explicit initialization if needed
 */
export function initEventDelegation() {
    // This function is kept for compatibility, but the listener is already attached
    console.log('Event delegation already active (attached at module load)');
}

// Attach event listener immediately when module loads (like old code)
// This ensures it's available even before DOMContentLoaded
if (typeof document !== 'undefined') {
    console.log('Attaching global click event delegation (immediate)...');
    
    // Use capture phase to catch events early (like old code)
    document.addEventListener('click', handleGlobalClick, true);
    
    console.log('Global click event delegation attached');
}

/**
 * Clicks on real controls and action regions — do not intercept in capture phase.
 */
function shouldBypassGlobalDelegation(e) {
    const t = e.target;
    if (!t || typeof t.closest !== 'function') return false;
    if (t.closest(
        'a[href], button, input, select, textarea, label, '
        + '[data-bulk-action], [data-original-action], '
        + '.original-file-actions, .file-grid-actions, .file-card-actions, '
        + '.file-row-actions, .fmas-bulk-actions'
    )) {
        return true;
    }
    return false;
}

/**
 * Handle global click events with delegation
 */
function handleGlobalClick(e) {
    if (shouldBypassGlobalDelegation(e)) {
        return;
    }
    // Debug: log all clicks to help troubleshoot
    // console.log('Global click:', e.target, e.target.className, e.target.tagName);
    
    // Copy-coordinates buttons.
    //
    // These used to call copyGeolocationCoordinates('${coords}', this) from an
    // inline handler - the same shape that killed the file cards: the value is
    // escaped with escapeHtml, which leaves quotes alone, so a coordinate
    // string containing a quote produced an uncompilable handler. The value is
    // carried in attributes (escaped with escapeAttribute) and read back here.
    const copyCoordsBtn = e.target.closest('[data-copy-coordinates]');
    if (copyCoordsBtn) {
        const coordinates = copyCoordsBtn.getAttribute('data-copy-coordinates');
        const longitude = copyCoordsBtn.getAttribute('data-copy-longitude');
        const copy = (window.fms && window.fms.fileOperations
            && window.fms.fileOperations.fileExport
            && window.fms.fileOperations.fileExport.copyGeolocationCoordinates)
            || window.copyGeolocationCoordinates;
        if (typeof copy === 'function') {
            e.preventDefault();
            e.stopPropagation();
            copy(coordinates, copyCoordsBtn, longitude === null ? undefined : longitude);
            return;
        }
    }
    
    // Handle sidebar-item clicks (navigation to sections)
    const sidebarItem = e.target.closest('.sidebar-item[data-section]');
    if (sidebarItem) {
        const section = sidebarItem.getAttribute('data-section');
        if (section) {
            // Check if functions are available before preventing default
            const hasFunction = !!(window.fms?.navigation?.navigateToSection || window.navigateToSection);
            
            if (hasFunction) {
                e.preventDefault();
                e.stopPropagation();
                
                console.log('✅ Delegated click on sidebar-item:', { section });
                
                if (window.fms?.navigation?.navigateToSection) {
                    window.fms.navigation.navigateToSection(section);
                } else if (window.navigateToSection) {
                    window.navigateToSection(section);
                }
                return;
            } else {
                // Functions not available yet, let onclick handler work
                console.warn('⚠️ navigateToSection not available, letting onclick handler work');
            }
        }
    }
    
    // Handle explorer-item clicks (BOTH root cards AND section items)
    // Root cards have data-section but NO data-item-id
    // Section items have both data-section AND data-item-id
    const explorerItem = e.target.closest('.explorer-item');
    if (explorerItem) {
        // Don't handle if clicking on editable fields, buttons, or links
        if (e.target.closest('[contenteditable="true"]') || 
            e.target.closest('button') || 
            e.target.closest('a') ||
            e.target.closest('.similar-titles-preview') ||
            e.target.closest('.similar-titles-list')) {
            return;
        }
        
        const section = explorerItem.getAttribute('data-section');
        const itemId = explorerItem.getAttribute('data-item-id');
        const itemName = explorerItem.getAttribute('data-item-name');
        const isGroup = explorerItem.getAttribute('data-is-group') === 'true';
        
        // Handle grouped titles
        if (isGroup && section === 'titles') {
            const groupTitleIds = explorerItem.getAttribute('data-group-title-ids');
            if (groupTitleIds) {
                e.preventDefault();
                e.stopPropagation();
                console.log('Delegated click on grouped title:', { section, itemId, itemName, groupTitleIds });
                
                if (window.fms?.views?.item?.loadGroupedTitleFiles) {
                    window.fms.views.item.loadGroupedTitleFiles(groupTitleIds, itemName || 'Grouped Titles');
                } else if (window.loadGroupedTitleFiles) {
                    window.loadGroupedTitleFiles(groupTitleIds, itemName || 'Grouped Titles');
                }
                return;
            }
        }
        
        // Handle other grouped items (don't navigate)
        if (isGroup) {
            e.preventDefault();
            e.stopPropagation();
            console.log('Delegated click on grouped item (cannot navigate):', { section, itemId, itemName });
            return;
        }
        
        // If section AND itemId exist, navigate to item
        if (section && itemId) {
            // Numeric IDs for most sections; string type keys for Browse by file type
            let navId = null;
            if (!itemId.toString().startsWith('group_')) {
                if (section === 'filetypes') {
                    navId = itemId.toString();
                } else {
                    const parsedId = parseInt(itemId, 10);
                    if (Number.isFinite(parsedId) && !Number.isNaN(parsedId)) {
                        navId = parsedId;
                    }
                }
            }
            if (navId !== null && navId !== '') {
                // Check if functions are available before preventing default
                const hasFunction = !!(window.fms?.navigation?.navigateToItem || window.navigateToItem);
                
                if (hasFunction) {
                    e.preventDefault();
                    e.stopPropagation();
                    console.log('✅ Delegated click on explorer-item (navigate to item):', { section, itemId: navId, itemName });
                    
                    if (window.fms?.navigation?.navigateToItem) {
                        window.fms.navigation.navigateToItem(section, navId, itemName || 'Item');
                    } else if (window.navigateToItem) {
                        window.navigateToItem(section, navId, itemName || 'Item');
                    }
                    return;
                } else {
                    // Functions not available yet, let onclick handler work
                    console.warn('⚠️ navigateToItem not available, letting onclick handler work');
                }
            } else {
                console.warn('Invalid item ID for navigation:', { section, itemId });
            }
        } 
        // If section exists but NO itemId, this is a root card - navigate to section
        else if (section && !itemId) {
            // Check if functions are available before preventing default
            const hasFunction = !!(window.fms?.navigation?.navigateToSection || window.navigateToSection);
            
            if (hasFunction) {
                e.preventDefault();
                e.stopPropagation();
                console.log('✅ Delegated click on explorer-item (navigate to section):', { section });
                
                if (window.fms?.navigation?.navigateToSection) {
                    window.fms.navigation.navigateToSection(section);
                } else if (window.navigateToSection) {
                    window.navigateToSection(section);
                }
                return;
            } else {
                // Functions not available yet, let onclick handler work
                console.warn('⚠️ navigateToSection not available, letting onclick handler work');
            }
        }
    }
    
    // Handle file-row-item clicks (section items in list view)
    const fileRowItemSection = e.target.closest('.file-row-item[data-section]');
    if (fileRowItemSection) {
        // Don't handle if clicking on editable fields, buttons, or links
        if (e.target.closest('[contenteditable="true"]') || 
            e.target.closest('button') || 
            e.target.closest('a') ||
            e.target.closest('.similar-titles-preview') ||
            e.target.closest('.similar-titles-list')) {
            return;
        }
        
        const section = fileRowItemSection.getAttribute('data-section');
        const itemId = fileRowItemSection.getAttribute('data-item-id');
        const itemName = fileRowItemSection.getAttribute('data-item-name');
        const isGroup = fileRowItemSection.getAttribute('data-is-group') === 'true';
        
        // Handle grouped titles
        if (isGroup && section === 'titles') {
            const groupTitleIds = fileRowItemSection.getAttribute('data-group-title-ids');
            if (groupTitleIds) {
                e.preventDefault();
                e.stopPropagation();
                console.log('Delegated click on grouped title in list view:', { section, itemId, itemName, groupTitleIds });
                
                if (window.fms?.views?.item?.loadGroupedTitleFiles) {
                    window.fms.views.item.loadGroupedTitleFiles(groupTitleIds, itemName || 'Grouped Titles');
                } else if (window.loadGroupedTitleFiles) {
                    window.loadGroupedTitleFiles(groupTitleIds, itemName || 'Grouped Titles');
                }
                return;
            }
        }
        
        // Handle other grouped items (don't navigate)
        if (isGroup) {
            e.preventDefault();
            e.stopPropagation();
            console.log('Delegated click on grouped item in list view (cannot navigate):', { section, itemId, itemName });
            return;
        }
        
        // If section AND itemId exist, navigate to item
        if (section && itemId) {
            let navId = null;
            if (!itemId.toString().startsWith('group_')) {
                if (section === 'filetypes') {
                    navId = itemId.toString();
                } else {
                    const parsedId = parseInt(itemId, 10);
                    if (Number.isFinite(parsedId) && !Number.isNaN(parsedId)) {
                        navId = parsedId;
                    }
                }
            }
            if (navId !== null && navId !== '') {
                // Check if functions are available before preventing default
                const hasFunction = !!(window.fms?.navigation?.navigateToItem || window.navigateToItem);
                
                if (hasFunction) {
                    e.preventDefault();
                    e.stopPropagation();
                    console.log('✅ Delegated click on file-row-item (navigate to item):', { section, itemId: navId, itemName });
                    
                    if (window.fms?.navigation?.navigateToItem) {
                        window.fms.navigation.navigateToItem(section, navId, itemName || 'Item');
                    } else if (window.navigateToItem) {
                        window.navigateToItem(section, navId, itemName || 'Item');
                    }
                    return;
                } else {
                    // Functions not available yet, let onclick handler work
                    console.warn('⚠️ navigateToItem not available, letting onclick handler work');
                }
            } else {
                console.warn('Invalid item ID for navigation:', { section, itemId });
            }
        }
    }
    
    // Handle file-row-item clicks for FILES (not section items)
    // This must come BEFORE the file-row-item[data-section] check
    const fileRowItemFile = e.target.closest('.file-row-item[data-file-id]');
    if (fileRowItemFile) {
        // Don't handle if clicking on action buttons, links, or checkboxes
        if (e.target.closest('.file-row-actions') || 
            e.target.closest('.file-select-checkbox') ||
            e.target.closest('a') ||
            e.target.closest('button') ||
            e.target.tagName === 'A' ||
            e.target.tagName === 'BUTTON') {
            return;
        }
        
        const fileId = fileRowItemFile.getAttribute('data-file-id');
        const fileName = fileRowItemFile.getAttribute('data-file-name');
        const fileIndex = parseInt(fileRowItemFile.getAttribute('data-file-index') || '-1');
        
        if (fileId) {
            e.preventDefault();
            e.stopPropagation();
            e.stopImmediatePropagation();
            
            console.log('Delegated click on file-row-item (file):', { fileId, fileName, fileIndex });
            
            // Get all files from current page for navigation
            const fileRows = document.querySelectorAll('.file-row-item[data-file-id]');
            const filesList = Array.from(fileRows).map(row => ({
                id: parseInt(row.getAttribute('data-file-id')),
                name: row.getAttribute('data-file-name') || 'File'
            }));
            
            if (window.fms?.fileOperations?.details?.showFileDetails) {
                window.fms.fileOperations.details.showFileDetails(
                    parseInt(fileId), 
                    fileName || 'File', 
                    filesList, 
                    fileIndex
                );
            } else if (window.showFileDetails) {
                window.showFileDetails(parseInt(fileId), fileName || 'File', filesList, fileIndex);
            }
            return;
        }
    }
    
    // Handle preview/view details buttons on files FIRST (before other handlers)
    const previewBtn = e.target.closest('.preview-btn[data-file-id]');
    if (previewBtn) {
        const fileId = previewBtn.getAttribute('data-file-id');
        const fileName = previewBtn.getAttribute('data-file-name');
        const fileIndex = previewBtn.getAttribute('data-file-index');
        if (fileId) {
            e.preventDefault();
            e.stopPropagation();
            e.stopImmediatePropagation();
            console.log('Delegated click on preview button:', { fileId, fileName, fileIndex });
            
            // Get all files from current page for navigation
            const fileRows = document.querySelectorAll('.file-row-item[data-file-id]');
            const filesList = Array.from(fileRows).map(row => ({
                id: parseInt(row.getAttribute('data-file-id')),
                name: row.getAttribute('data-file-name') || 'File'
            }));
            
            if (window.fms?.fileOperations?.details?.showFileDetails) {
                window.fms.fileOperations.details.showFileDetails(
                    parseInt(fileId), 
                    fileName || 'File', 
                    filesList, 
                    parseInt(fileIndex) || -1
                );
            } else if (window.showFileDetails) {
                window.showFileDetails(parseInt(fileId), fileName || 'File', filesList, parseInt(fileIndex) || -1);
            }
            return;
        }
    }
    
    // Handle file-grid-item clicks (grid view in item view)
    const fileGridItem = e.target.closest('.file-grid-item[data-file-id]');
    if (fileGridItem) {
        if (e.target.closest('.file-grid-actions, button, a, input, .file-checkbox, .file-select-checkbox')) {
            return;
        }
        const fileId = fileGridItem.getAttribute('data-file-id');
        const fileName = fileGridItem.getAttribute('data-file-name');
        const fileIndex = parseInt(fileGridItem.getAttribute('data-file-index') || '-1', 10);
        if (fileId) {
            e.preventDefault();
            e.stopPropagation();
            const fileRows = document.querySelectorAll('.file-grid-item[data-file-id], .file-card[data-file-id]');
            const filesList = Array.from(fileRows).map((row) => ({
                id: parseInt(row.getAttribute('data-file-id'), 10),
                name: row.getAttribute('data-file-name') || 'File',
            }));
            if (window.fms?.fileOperations?.details?.showFileDetails) {
                window.fms.fileOperations.details.showFileDetails(
                    parseInt(fileId, 10), fileName || 'File', filesList, fileIndex);
            } else if (window.showFileDetails) {
                window.showFileDetails(parseInt(fileId, 10), fileName || 'File', filesList, fileIndex);
            }
            return;
        }
    }

    // Handle file-card clicks (files in item view)
    const fileCard = e.target.closest('.file-card[data-file-id]');
    if (fileCard) {
        // Don't handle if clicking on buttons, links, or checkboxes
        if (e.target.closest('button') || 
            e.target.closest('a') ||
            e.target.closest('.file-checkbox') ||
            e.target.closest('.file-card-actions')) {
            return;
        }
        
        const fileId = fileCard.getAttribute('data-file-id');
        const fileName = fileCard.getAttribute('data-file-name');
        const fileIndex = parseInt(fileCard.getAttribute('data-file-index') || '-1');
        
        if (fileId) {
            e.preventDefault();
            e.stopPropagation();
            
            console.log('Delegated click on file-card:', { fileId, fileName, fileIndex });
            
            // Get all files from current page for navigation
            const fileRows = document.querySelectorAll('.file-card[data-file-id]');
            const filesList = Array.from(fileRows).map(row => ({
                id: parseInt(row.getAttribute('data-file-id')),
                name: row.getAttribute('data-file-name') || 'File'
            }));
            
            if (window.fms?.fileOperations?.details?.showFileDetails) {
                window.fms.fileOperations.details.showFileDetails(
                    parseInt(fileId), 
                    fileName || 'File', 
                    filesList, 
                    fileIndex
                );
            } else if (window.showFileDetails) {
                window.showFileDetails(parseInt(fileId), fileName || 'File', filesList, fileIndex);
            }
            return;
        }
    }
}

