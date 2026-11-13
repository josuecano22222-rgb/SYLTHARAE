/**
 * Modal Search Functionality
 * Handles search within modal content
 */

import { escapeHtml } from '../core/utils.js';
import { syncFileModalSearchToggles } from '../ui/file-modal-panels.js';

// Global state for modal search
let modalSearchResults = [];
let modalCurrentSearchIndex = 0;

/**
 * Perform search within modal content
 */
export function performModalSearch() {
    const query = document.getElementById('modalSearchInput')?.value.trim();
    if (!query) {
        clearModalSearch();
        return;
    }
    
    const caseSensitive = document.getElementById('modalCaseSensitive')?.checked || false;
    const wholeWord = document.getElementById('modalWholeWord')?.checked || false;
    
    // Try to find the content element - it might be inside fileContentSection
    let contentElement = document.getElementById('modalContentText');
    if (!contentElement) {
        // Wait a bit for content to load, then try again
        setTimeout(() => {
            contentElement = document.getElementById('modalContentText');
            if (contentElement) {
                performModalSearch();
            } else {
                console.warn('Content element not found for search');
            }
        }, 100);
        return;
    }
    
    // Get full content for searching - use stored content or element text
    const fullContent = window.currentModalFileContent || contentElement.textContent || contentElement.innerText || '';
    if (!fullContent) {
        console.warn('No content available for search');
        return;
    }
    
    modalSearchResults = [];
    modalCurrentSearchIndex = 0;
    
    let pattern = query;
    if (wholeWord) {
        pattern = `\\b${pattern.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`;
    } else {
        pattern = pattern.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    }
    
    const flags = caseSensitive ? 'g' : 'gi';
    const regex = new RegExp(pattern, flags);
    
    let match;
    while ((match = regex.exec(fullContent)) !== null) {
        modalSearchResults.push({ 
            index: match.index, 
            length: match[0].length,
            text: match[0]
        });
    }
    
    updateModalSearchUI();
    highlightModalMatches();
    
    const searchResultsDiv = document.getElementById('modalSearchResults');
    if (searchResultsDiv) {
        searchResultsDiv.classList.add('is-visible');
    }
    
    if (modalSearchResults.length > 0) {
        scrollToModalMatch(modalSearchResults[0]);
    }
}

/**
 * Update modal search UI
 */
function updateModalSearchUI() {
    const resultCount = document.getElementById('modalResultCount');
    const currentMatch = document.getElementById('modalCurrentMatch');
    
    if (modalSearchResults.length === 0) {
        if (resultCount) {
            resultCount.textContent = '0 results';
            resultCount.className = 'badge bg-secondary';
        }
        if (currentMatch) {
            currentMatch.textContent = 'No matches';
            currentMatch.className = 'badge bg-secondary';
        }
    } else {
        if (resultCount) {
            resultCount.textContent = `${modalSearchResults.length} result${modalSearchResults.length > 1 ? 's' : ''}`;
            resultCount.className = 'badge bg-primary';
        }
        if (currentMatch) {
            currentMatch.textContent = `Match ${modalCurrentSearchIndex + 1} of ${modalSearchResults.length}`;
            currentMatch.className = 'badge bg-info';
        }
    }
}

/**
 * Highlight search matches in modal content
 */
function highlightModalMatches() {
    const contentElement = document.getElementById('modalContentText');
    if (!contentElement || modalSearchResults.length === 0) {
        if (contentElement) {
            const originalContent = contentElement.getAttribute('data-original-content');
            if (originalContent !== null) {
                contentElement.innerHTML = originalContent;
            }
        }
        return;
    }
    
    const fullContent = window.currentModalFileContent || contentElement.textContent;
    
    if (!contentElement.getAttribute('data-original-content')) {
        contentElement.setAttribute('data-original-content', contentElement.innerHTML);
    }
    
    let highlightedContent = '';
    let lastIndex = 0;
    
    const sortedMatches = [...modalSearchResults].sort((a, b) => a.index - b.index);
    
    sortedMatches.forEach((match, index) => {
        highlightedContent += escapeHtml(fullContent.substring(lastIndex, match.index));
        
        const matchText = fullContent.substring(match.index, match.index + match.length);
        const highlightClass = index === modalCurrentSearchIndex ? 'search-highlight current-match' : 'search-highlight';
        highlightedContent += `<span class="${highlightClass}" style="background-color: #ffeb3b; padding: 2px 0; border-radius: 2px;">${escapeHtml(matchText)}</span>`;
        
        lastIndex = match.index + match.length;
    });
    
    highlightedContent += escapeHtml(fullContent.substring(lastIndex));
    contentElement.innerHTML = highlightedContent;
}

/**
 * Find next match
 */
export function findModalNext() {
    if (modalSearchResults.length === 0) return;
    modalCurrentSearchIndex = (modalCurrentSearchIndex + 1) % modalSearchResults.length;
    scrollToModalMatch(modalSearchResults[modalCurrentSearchIndex]);
    updateModalSearchUI();
    highlightModalMatches();
}

/**
 * Find previous match
 */
export function findModalPrevious() {
    if (modalSearchResults.length === 0) return;
    modalCurrentSearchIndex = (modalCurrentSearchIndex - 1 + modalSearchResults.length) % modalSearchResults.length;
    scrollToModalMatch(modalSearchResults[modalCurrentSearchIndex]);
    updateModalSearchUI();
    highlightModalMatches();
}

/**
 * Scroll to match in modal
 */
function scrollToModalMatch(result) {
    const highlights = document.querySelectorAll('#modalContentText .search-highlight');
    if (highlights.length > modalCurrentSearchIndex) {
        highlights[modalCurrentSearchIndex].scrollIntoView({ 
            behavior: 'smooth', 
            block: 'center'
        });
    }
}

/**
 * Clear modal search
 */
export function clearModalSearch() {
    modalSearchResults = [];
    modalCurrentSearchIndex = 0;
    
    const searchInput = document.getElementById('modalSearchInput');
    const caseSensitive = document.getElementById('modalCaseSensitive');
    const wholeWord = document.getElementById('modalWholeWord');
    const searchResultsDiv = document.getElementById('modalSearchResults');
    
    if (searchInput) searchInput.value = '';
    if (caseSensitive) caseSensitive.checked = false;
    if (wholeWord) wholeWord.checked = false;
    if (searchResultsDiv) searchResultsDiv.classList.remove('is-visible');
    syncFileModalSearchToggles();
    
    // Restore original content
    const contentElement = document.getElementById('modalContentText');
    if (contentElement) {
        const originalContent = contentElement.getAttribute('data-original-content');
        if (originalContent !== null) {
            contentElement.innerHTML = originalContent;
        } else {
            const highlightedContent = contentElement.innerHTML;
            const cleanContent = highlightedContent.replace(/<span class="search-highlight[^"]*"[^>]*>([^<]*)<\/span>/g, '$1');
            contentElement.innerHTML = cleanContent;
        }
    }
}

