/**
 * File Details Modal
 * Handles showing file details in modal
 */

import { fileNavigationState, modalState } from '../core/state.js';
import { MODAL_ENABLED, translations } from '../core/config.js';
import { escapeAttribute, escapeHtml } from '../core/utils.js';
import { updateFileNavigationButtons } from './file-navigation.js';
import { clearModalSearch } from '../search/modal-search.js';
import { bindAnalystClassify } from '../analyst-classify.js';
import { showOriginalFile } from './original-file.js';
import {
    mountFileAnalysisSection,
    mountFileAnalysisSummary,
    mountFileMetadataSection,
    renderLineageSection,
} from './file-details-panels.js';
import { initFileModalPanels, initFileModalTooltips } from '../ui/file-modal-panels.js';

export { renderLineageSection };

/**
 * Show file details modal
 * @param {number} fileId - File ID
 * @param {string} fileName - File name
 * @param {Array} fileList - Optional list of files for navigation
 * @param {number} fileIndex - Optional index in file list
 */
export function showFileDetails(fileId, fileName, fileList = null, fileIndex = -1) {
    console.log('Showing file details for:', { fileId, fileName, fileList, fileIndex, MODAL_ENABLED });
    
    // If modal is disabled, open in new tab instead
    if (!MODAL_ENABLED) {
        console.log('Modal disabled - opening in new tab');
        window.open(`/file/${fileId}`, '_blank');
        return;
    }
    
    // Store file list context if provided
    if (fileList && Array.isArray(fileList)) {
        fileNavigationState.currentFiles = fileList;
        fileNavigationState.currentIndex = fileIndex >= 0 ? fileIndex : fileList.findIndex(f => f.id === fileId);
    } else {
        // Try to get file list from current page
        const fileRows = document.querySelectorAll('.file-row-item[data-file-id]');
        if (fileRows.length > 0) {
            fileNavigationState.currentFiles = Array.from(fileRows).map(row => ({
                id: parseInt(row.getAttribute('data-file-id')),
                name: row.getAttribute('data-file-name') || 'File'
            }));
            fileNavigationState.currentIndex = fileNavigationState.currentFiles.findIndex(f => f.id === fileId);
        }
    }
    
    // Open modal
    const modal = document.getElementById('fileModal');
    if (!modal) {
        console.error('File modal not found in DOM');
        if (window.showError) {
            window.showError('Modal element not found. Please refresh the page.');
        }
        // Fallback: open in new tab
        window.open(`/file/${fileId}`, '_blank');
        return;
    }
    
    // Update navigation buttons
    updateFileNavigationButtons();
    
    // Set modal title
    const modalTitle = document.getElementById('modalTitle');
    if (modalTitle) {
        modalTitle.textContent = fileName || (translations.fileDetails || 'File Details');
    }
    
    // Show loading state
    const contentSection = document.getElementById('fileContentSection');
    const analysisSection = document.getElementById('fileAnalysisSection');
    const metadataSection = document.getElementById('fileMetadataSection');
    
    if (contentSection) {
        contentSection.innerHTML = `<div class="content-loading">${translations.loadingContent || 'Loading content...'}</div>`;
    }
    if (analysisSection) {
        analysisSection.innerHTML = `<div class="content-loading">${translations.loadingAnalysis || 'Loading analysis...'}</div>`;
    }
    const analysisSummary = document.getElementById('fileAnalysisSummary');
    if (analysisSummary) {
        analysisSummary.hidden = false;
        analysisSummary.innerHTML = `<div class="doc-analysis-summary loading">${translations.loadingAnalysis || 'Loading analysis...'}</div>`;
    }
    modalState.currentAnalysis = null;
    if (metadataSection) {
        metadataSection.innerHTML = `<div class="content-loading">${translations.loadingMetadata || 'Loading metadata...'}</div>`;
    }
    
    // Clear any previous search state
    if (clearModalSearch) {
        clearModalSearch();
    }
    
    // Show modal
    modal.classList.add('active');
    modal.style.display = 'flex';
    modal.style.visibility = 'visible';
    modal.style.opacity = '1';
    // z-index is handled by CSS (10000)
    document.body.style.overflow = 'hidden';
    
    // Analyst (manual) categorization for the file now on screen. The pop-up
    // shows one file after another (opens, the list, its own previous/next),
    // so the card is re-pointed at every display instead of being rebuilt -
    // its listeners are bound once, its badges follow the file.
    const analystSlot = document.getElementById('modalAnalystClassify');
    const analystCard = analystSlot && analystSlot.querySelector('[data-analyst-classify]');
    if (analystCard) {
        bindAnalystClassify(analystCard, fileId).catch(err =>
            console.error('Analyst classification card could not be bound:', err));
    }

    // Load file details content
    loadFileDetailsContent(fileId, fileName);

    // The original-file pane follows the same object as the details, so it is
    // re-pointed here rather than only on first open.
    const originalPane = document.getElementById('originalFileSection');
    if (originalPane && modalContentTab === 'original') {
        showOriginalFile(fileId, originalPane).catch(err =>
            console.error('Could not render the original file:', err));
    }
    
    modalState.isOpen = true;
    modalState.currentFileId = fileId;
    modalState.currentFileName = fileName;

    initFileModalPanels();
}

/* Attribute-context escaping lives in core/utils.js next to escapeHtml: the
   two escapers are easy to confuse, both are used from several renderers, and
   the lineage block below (plus the file list) must use the attribute one.
   Re-exported here because this module was its original home. Verified by
   tests/unit/test_frontend_lineage_escaping.py. */
export { escapeAttribute };

/**
 * Load file details content into modal
 * @param {number} fileId - File ID
 * @param {string} fileName - File name
 */
export async function loadFileDetailsContent(fileId, fileName) {
    try {
        const response = await fetch(`/api/file/${fileId}/details`);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        const data = await response.json();
        
        if (!data.success || !data.details) {
            // ERROR HANDLING FIX: Properly handle API error responses
            const errorMsg = escapeHtml(data.error || (translations.errorLoadingFileDetails || 'Error loading file details'));
            const contentSection = document.getElementById('fileContentSection');
            const analysisSection = document.getElementById('fileAnalysisSection');
            const metadataSection = document.getElementById('fileMetadataSection');
            
            // Show error in all sections
            if (contentSection) contentSection.innerHTML = `<div class="empty-state error">${errorMsg}</div>`;
            if (analysisSection) analysisSection.innerHTML = `<div class="empty-state error">${errorMsg}</div>`;
            if (metadataSection) metadataSection.innerHTML = `<div class="empty-state error">${errorMsg}</div>`;
            
            // Show notification to user
            if (window.fms && window.fms.ui && window.fms.ui.notifications) {
                window.fms.ui.notifications.error(errorMsg);
            } else if (window.showError) {
                window.showError(errorMsg);
            }
            return;
        }

        const file = data.details;
        
        // Store file ID for export functionality
        window.currentModalFileId = fileId;
        
        // Populate Content Section
        const contentSection = document.getElementById('fileContentSection');
        if (contentSection) {
            const contentValue = file.content || file['content'] || '';
            const fileType = file.type || '';
            const filePath = file.path || '';
            
            if (contentValue && typeof contentValue === 'string' && contentValue.trim()) {
                // Display full content without truncation
                // SECURITY FIX: Content is already escaped in formatContentByType or we escape it here
                const displayContent = contentValue;
                
                window.currentModalFileContent = contentValue;
                window.currentModalFileName = fileName || 'file';
                
                // Format content based on file type
                import('../content-formatter.js').then(module => {
                    const formatted = module.formatContentByType(displayContent, fileType, filePath, fileId);
                    if (formatted) {
                        // SECURITY FIX: Ensure formatted content is safe - formatContentByType should handle escaping
                        // But we'll add a safety check here
                        contentSection.innerHTML = `<div id="modalContentText" class="formatted-content-wrapper">${formatted}</div>`;
                        
                        // Attach image error handlers after DOM insertion
                        setTimeout(() => {
                            const contentElement = document.getElementById('modalContentText');
                            if (contentElement && window.attachImageErrorHandlers) {
                                window.attachImageErrorHandlers(contentElement);
                            }
                        }, 10);
                    } else {
                        // SECURITY FIX: Always escape HTML content to prevent XSS
                        contentSection.innerHTML = `<pre id="modalContentText" style="margin: 0; white-space: pre-wrap; word-wrap: break-word; overflow-wrap: break-word;">${escapeHtml(displayContent)}</pre>`;
                    }
                    
                    const contentElement = document.getElementById('modalContentText');
                    if (contentElement) {
                        contentElement.setAttribute('data-original-content', displayContent);
                        contentElement.setAttribute('data-full-content', escapeHtml(contentValue));
                    }
                }).catch(err => {
                    console.error('Error formatting modal content:', err);
                    // SECURITY FIX: Always escape HTML content to prevent XSS
                    contentSection.innerHTML = `<pre id="modalContentText" style="margin: 0; white-space: pre-wrap; word-wrap: break-word; overflow-wrap: break-word;">${escapeHtml(displayContent)}</pre>`;
                    const contentElement = document.getElementById('modalContentText');
                    if (contentElement) {
                        contentElement.setAttribute('data-original-content', displayContent);
                        contentElement.setAttribute('data-full-content', escapeHtml(contentValue));
                    }
                });
            } else if (fileType.match(/\.(jpg|jpeg|png|gif|bmp|tiff|tif|webp|svg)$/i) && (filePath || fileId)) {
                // Image file without text content - show image
                import('../content-formatter.js').then(module => {
                    const formatted = module.formatContentByType('', fileType, filePath, fileId);
                    if (formatted) {
                        contentSection.innerHTML = `<div id="modalContentText" class="formatted-content-wrapper">${formatted}</div>`;
                    }
                }).catch(err => {
                    console.error('Error formatting image:', err);
                });
            } else {
                window.currentModalFileContent = '';
                window.currentModalFileName = fileName || 'file';
                contentSection.innerHTML = `<div class="empty-state">${translations.noContentAvailable || 'No content available'}</div>`;
            }
        }
        
        mountFileAnalysisSummary(
            document.getElementById('fileAnalysisSummary'),
            file.id,
            {
                translations,
                file,
                onAnalysis: (analysis) => { modalState.currentAnalysis = analysis; },
            }
        );

        mountFileAnalysisSection(document.getElementById('fileAnalysisSection'), file, {
            translations,
            loadCharts: true,
        });

        mountFileMetadataSection(document.getElementById('fileMetadataSection'), file, {
            translations,
            layout: 'compact',
            onLineageClick: (id, name) => {
                if (window.fms?.fileOperations?.details?.showFileDetails) {
                    window.fms.fileOperations.details.showFileDetails(id, name);
                }
            },
        });

        initFileModalTooltips(document.getElementById('fileModal'));
    } catch (error) {
        console.error('Error loading file details:', error);
        // SECURITY FIX: Always escape error messages to prevent XSS
        const errorMsg = escapeHtml(error.message || 'Unknown error occurred');
        const networkError = error.name === 'TypeError' && error.message.includes('fetch');
        const userFriendlyMsg = networkError 
            ? (translations.networkError || 'Network error: Unable to connect to server. Please check your connection and try again.')
            : `${translations.errorLoadingFileDetails || 'Error loading file details'}: ${errorMsg}`;
        
        const contentSection = document.getElementById('fileContentSection');
        const analysisSection = document.getElementById('fileAnalysisSection');
        const metadataSection = document.getElementById('fileMetadataSection');
        
        // ERROR HANDLING FIX: Show proper error messages in all sections
        if (contentSection) {
            contentSection.innerHTML = `<div class="empty-state error">${escapeHtml(userFriendlyMsg)}</div>`;
        }
        if (analysisSection) {
            analysisSection.innerHTML = `<div class="empty-state error">${escapeHtml(userFriendlyMsg)}</div>`;
        }
        if (metadataSection) {
            metadataSection.innerHTML = `<div class="empty-state error">${escapeHtml(userFriendlyMsg)}</div>`;
        }
        
        // Show notification to user
        if (window.fms && window.fms.ui && window.fms.ui.notifications) {
            window.fms.ui.notifications.error(userFriendlyMsg);
        } else if (window.showError) {
            window.showError(userFriendlyMsg);
        }
    }
}

//: Which pane the modal's left side is showing: 'extracted' | 'original'.
let modalContentTab = 'extracted';

/**
 * Which pane the File Content area is showing.
 * @returns {string} 'extracted' or 'original'
 */
export function getModalContentTab() {
    return modalContentTab;
}

/**
 * Switch between the extracted text and the original file.
 *
 * Both panes describe the same stored object, so switching never re-fetches
 * the details - it shows the pane already loaded, or loads the original file
 * on first use. The search controls belong to the extracted text and are
 * hidden with it.
 *
 * @param {string} tab - 'extracted' or 'original'
 */
export function switchModalContentTab(tab) {
    const wanted = tab === 'original' ? 'original' : 'extracted';
    modalContentTab = wanted;

    const extracted = document.getElementById('fileContentSection');
    const original = document.getElementById('originalFileSection');
    const search = document.getElementById('extractedContentSearch');
    const extractedTab = document.getElementById('extractedContentTab');
    const originalTab = document.getElementById('originalFileTab');

    if (extracted) extracted.style.display = wanted === 'extracted' ? '' : 'none';
    if (original) original.style.display = wanted === 'original' ? '' : 'none';
    if (search) search.style.display = wanted === 'extracted' ? '' : 'none';

    [[extractedTab, 'extracted'], [originalTab, 'original']].forEach(([el, name]) => {
        if (!el) return;
        el.classList.toggle('active', name === wanted);
        el.setAttribute('aria-selected', name === wanted ? 'true' : 'false');
    });

    if (wanted === 'original' && original) {
        loadOriginalFileInto(original);
    }
    return wanted;
}

/**
 * Load (or reload) the original file for the object currently on screen.
 * Previous/Next in the modal moves between files, so the pane is re-pointed
 * at every display - staying on whatever file the details show.
 *
 * @param {HTMLElement} container - the original-file pane
 */
function loadOriginalFileInto(container) {
    const fileId = modalState.currentFileId;
    if (!fileId || !container) return;
    showOriginalFile(fileId, container).catch(err =>
        console.error('Could not render the original file:', err));
}

/**
 * Close file modal
 */
export function closeFileModal() {
    const modal = document.getElementById('fileModal');
    if (modal) {
        modal.classList.remove('active');
        modal.style.display = 'none';
        document.body.style.overflow = '';
        
        // Clear search state
        if (clearModalSearch) {
            clearModalSearch();
        }
        
        // Clear stored content and file ID
        window.currentModalFileContent = '';
        window.currentModalFileName = '';
        window.currentModalFileId = null;
        
        modalState.isOpen = false;
        modalState.currentFileId = null;
        modalState.currentFileName = null;
    }
}

// Setup click outside to close
if (typeof document !== 'undefined') {
    document.addEventListener('click', function(e) {
        const modal = document.getElementById('fileModal');
        if (modal && modal.classList.contains('active')) {
            if (e.target === modal) {
                closeFileModal();
            }
        }
    });
    
    // Keyboard navigation
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') {
            const modal = document.getElementById('fileModal');
            if (modal && modal.classList.contains('active')) {
                closeFileModal();
            }
        }
    });
}

