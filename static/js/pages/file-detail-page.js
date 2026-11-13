/**
 * File Detail Page JavaScript
 * Extracted from file/file_detail.html
 */

import { buildPattern, highlight, clearHighlights, getMarks, setCurrentMatch }
    from '../modules/content-highlighter.js';
import { initOriginalContentTab } from '../modules/original-content-tab.js';
import { onPageTeardown } from '../modules/core/page-lifecycle.js';

// Load translations from JSON script tag
let translations = {};

// Global variables
let fileId = null;
let fileName = '';
let currentPage = 1;
let totalPages = 0;
let perPage = 10;
let totalChars = 0;
let searchQuery = '';
let caseSensitive = false;
let wholeWord = false;
let percentages = [];
let wordFrequencies = [];
let searchResults = [];
let currentSearchIndex = 0;
let chartInstances = {};
let chartsInitialized = false;

function loadFileDetailPageData() {
    const pageDataEl = document.getElementById('file-detail-page-data');
    if (pageDataEl) {
        try {
            const data = JSON.parse(pageDataEl.textContent);
            translations = data.translations || {};
            // Load page data
            if (data.fileId !== undefined) fileId = data.fileId;
            if (data.fileName) fileName = data.fileName;
            if (data.currentPage !== undefined) currentPage = data.currentPage;
            if (data.totalPages !== undefined) totalPages = data.totalPages;
            if (data.perPage !== undefined) perPage = data.perPage;
            if (data.totalChars !== undefined) totalChars = data.totalChars;
            if (data.searchQuery !== undefined) searchQuery = data.searchQuery;
            if (data.caseSensitive !== undefined) caseSensitive = data.caseSensitive;
            if (data.wholeWord !== undefined) wholeWord = data.wholeWord;
            if (data.percentages) {
                // Ensure percentages is an object/dict, not an array
                if (Array.isArray(data.percentages)) {
                    console.warn('Percentages is an array, converting to object');
                    percentages = {};
                } else {
                    percentages = data.percentages;
                }
            }
            if (data.wordFrequencies) wordFrequencies = data.wordFrequencies;
        } catch (e) {
            console.error('Error parsing file detail page data:', e);
        }
    }
}

/** @type {ResizeObserver|null} */
let fileAnalystChromeObserver = null;
/** @type {(() => void)|null} */
let fileAnalystChromeResizeHandler = null;
/** @type {((ev: Event) => void)|null} */
let fileAnalystMetadataToggleHandler = null;

/**
 * Size each pane scroll region from its top edge to the viewport bottom so
 * long documents scroll inside #fileContentScroll / #fileAnalysisScroll.
 */
function fileNavDockClearance() {
    const dock = document.getElementById('fileNavDock');
    if (!dock) return 16;
    const rect = dock.getBoundingClientRect();
    const gap = 12;
    return Math.max(16, Math.ceil(window.innerHeight - rect.top + gap));
}

/** Reserve space under the fixed file-set navigation dock. */
export function syncFileNavDockInset() {
    const page = document.getElementById('fileAnalystPage');
    const dock = document.getElementById('fileNavDock');
    if (!page || !dock) return;
    const height = Math.ceil(dock.getBoundingClientRect().height + 20);
    page.style.setProperty('--file-nav-dock-height', `${height}px`);
}

export function syncFileAnalystWorkspaceHeight() {
    const page = document.getElementById('fileAnalystPage');
    if (!page) return;

    syncFileNavDockInset();

    const bottomMargin = fileNavDockClearance();
    const scrollIds = ['fileContentScroll', 'fileAnalysisScroll'];

    scrollIds.forEach((id) => {
        const el = document.getElementById(id);
        if (!el) return;
        const top = el.getBoundingClientRect().top;
        const maxH = Math.max(240, window.innerHeight - top - bottomMargin);
        el.style.setProperty('--file-analyst-scroll-max', `${Math.round(maxH)}px`);
    });
}

function bindFileAnalystWorkspaceHeight() {
    const page = document.getElementById('fileAnalystPage');
    if (!page || page.dataset.fileAnalystScrollBound === '1') return;
    page.dataset.fileAnalystScrollBound = '1';

    const scheduleSync = () => {
        requestAnimationFrame(syncFileAnalystWorkspaceHeight);
    };

    scheduleSync();

    fileAnalystChromeResizeHandler = scheduleSync;
    window.addEventListener('resize', fileAnalystChromeResizeHandler, { passive: true });

    const meta = page.querySelector('.file-analyst-metadata');
    if (meta) {
        fileAnalystMetadataToggleHandler = scheduleSync;
        meta.addEventListener('toggle', fileAnalystMetadataToggleHandler);
    }

    const contentTools = page.querySelector('.file-analyst-content-tools');
    if (contentTools) {
        contentTools.addEventListener('toggle', scheduleSync);
        const searchInput = document.getElementById('searchInput');
        if (searchInput?.value?.trim()) {
            contentTools.open = true;
        }
    }

    const fileNavDock = document.getElementById('fileNavDock');
    if (fileNavDock) {
        fileNavDock.addEventListener('transitionend', scheduleSync);
    }

    if (typeof ResizeObserver !== 'undefined') {
        fileAnalystChromeObserver = new ResizeObserver(scheduleSync);
        const header = page.querySelector('.file-analyst-header');
        if (header) fileAnalystChromeObserver.observe(header);
        if (meta) fileAnalystChromeObserver.observe(meta);
        if (contentTools) fileAnalystChromeObserver.observe(contentTools);
        if (fileNavDock) fileAnalystChromeObserver.observe(fileNavDock);
    }
}

function unbindFileAnalystWorkspaceHeight() {
    const page = document.getElementById('fileAnalystPage');
    if (fileAnalystChromeResizeHandler) {
        window.removeEventListener('resize', fileAnalystChromeResizeHandler);
        fileAnalystChromeResizeHandler = null;
    }
    if (page && fileAnalystMetadataToggleHandler) {
        const meta = page.querySelector('.file-analyst-metadata');
        meta?.removeEventListener('toggle', fileAnalystMetadataToggleHandler);
        fileAnalystMetadataToggleHandler = null;
    }
    fileAnalystChromeObserver?.disconnect();
    fileAnalystChromeObserver = null;
    if (page) {
        delete page.dataset.fileAnalystScrollBound;
    }
    ['fileContentScroll', 'fileAnalysisScroll'].forEach((id) => {
        document.getElementById(id)?.style.removeProperty('--file-analyst-scroll-max');
    });
}

/** Dual-pane workstation: charts load once on mount (no tab switching). */
function initFileDetailWorkstation() {
    const root = document.getElementById('fileAnalystPage');
    if (!root || root.dataset.fileWorkstationBound === '1') return;
    root.dataset.fileWorkstationBound = '1';
    bindFileAnalystWorkspaceHeight();
    syncFileAnalystWorkspaceHeight();
    if (!chartsInitialized) {
        setTimeout(initializeCharts, 120);
    }
}

function teardownFileDetailPage() {
    Object.values(chartInstances).forEach((chart) => {
        try {
            chart?.destroy?.();
        } catch (_error) { /* chart already torn down */ }
    });
    if (window.chartInstances && typeof window.chartInstances === 'object') {
        Object.values(window.chartInstances).forEach((chart) => {
            try {
                chart?.destroy?.();
            } catch (_error) { /* */ }
        });
        window.chartInstances = {};
    }
    chartInstances = {};
    chartsInitialized = false;
    docMatches = [];
    currentDocMatch = -1;
    searchResults = [];
    currentSearchIndex = 0;
    unbindFileAnalystWorkspaceHeight();
    const root = document.getElementById('fileAnalystPage');
    if (root) delete root.dataset.fileWorkstationBound;
    if (document.body) delete document.body.dataset.fileDetailMounted;
}

onPageTeardown('file-detail', teardownFileDetailPage);

/** Mount file detail UI (full load and soft-nav). */
function mountFileDetailPage() {
    teardownFileDetailPage();
    loadFileDetailPageData();

    console.log('File detail page loaded');
    console.log(`Total chars: ${totalChars}, Total pages: ${totalPages}`);

    // Extracted/Original tabs — the original file renders in place, where
    // the extracted text is (same pop-up viewer the analysis uses).
    initOriginalContentTab(fileId);
    
    // Format content based on file type
    const contentViewer = document.getElementById('contentViewer');
    if (contentViewer) {
        const fileType = contentViewer.getAttribute('data-file-type') || '';
        const filePath = contentViewer.getAttribute('data-file-path') || '';
        const fileIdAttr = contentViewer.getAttribute('data-file-id');
        const fileId = fileIdAttr ? parseInt(fileIdAttr, 10) : null;
        const contentData = contentViewer.getAttribute('data-content') || '';
        
        // Import and apply formatter
        import('../modules/content-formatter.js').then(module => {
            // Localize accessibility labels before the first render
            if (module.setContentFormatterTranslations) {
                module.setContentFormatterTranslations(translations);
            }
            const contentElement = document.getElementById('contentText');
            if (contentElement && contentData) {
                console.log('Formatting content:', {
                    fileType: fileType,
                    filePath: filePath,
                    fileId: fileId,
                    contentLength: contentData.length,
                    preview: contentData.substring(0, 200)
                });
                
                const formattedContent = module.formatContentByType(contentData, fileType, filePath, fileId);
                if (formattedContent) {
                    console.log('✅ Content formatted successfully, length:', formattedContent.length);
                    contentElement.innerHTML = formattedContent;
                    // Signal the in-document search that formatted DOM is ready
                    // (highlighting before this point would be wiped).
                    contentElement.dataset.formatted = 'true';
                    contentElement.dispatchEvent(new CustomEvent('content:formatted'));
                    // Ensure proper styling for readability
                    contentElement.style.width = '100%';
                    contentElement.style.wordWrap = 'break-word';
                    contentElement.style.overflowWrap = 'break-word';
                    contentElement.style.whiteSpace = 'pre-wrap';
                    // Store original content for search functionality
                    contentElement.setAttribute('data-original-content', contentData);
                    
                    // Attach image error handlers after DOM insertion
                    // Use setTimeout to ensure DOM is fully updated
                    setTimeout(() => {
                        attachImageErrorHandlers(contentElement);
                    }, 10);
                } else {
                    console.warn('⚠️ Formatter returned empty result');
                    // Ensure proper styling even if formatter fails
                    contentElement.style.width = '100%';
                    contentElement.style.wordWrap = 'break-word';
                    contentElement.style.overflowWrap = 'break-word';
                    contentElement.style.whiteSpace = 'pre-wrap';
                }
            } else {
                console.warn('⚠️ Content element or data not found:', {
                    hasElement: !!contentElement,
                    hasData: !!contentData
                });
            }
        }).catch(err => {
            console.error('Error loading content formatter:', err);
        });
    }
    
    // Verify content element and store original content
    const contentElement = document.getElementById('contentText');
    if (contentElement) {
        // Raw-text fallback rendering counts as formatted for search purposes.
        if (!contentElement.dataset.formatted) {
            contentElement.dataset.formatted = 'true';
            contentElement.dispatchEvent(new CustomEvent('content:formatted'));
        }
        const contentLength = contentElement.textContent.length;
        console.log(`Content element found with ${contentLength} characters`);
        
        // Store original content for search functionality (if not already stored)
        if (!contentElement.getAttribute('data-original-content')) {
            const preElement = contentElement.querySelector('pre.content-text');
            if (preElement) {
                contentElement.setAttribute('data-original-content', preElement.textContent);
            } else {
                contentElement.setAttribute('data-original-content', contentElement.textContent);
            }
        }
        
        if (contentLength === 0 && totalChars > 0) {
            console.warn('Content element empty but total_chars indicates content exists');
        }
        
        // Verify content is visible
        if (contentLength > 0) {
            console.log('✅ Content is available and visible');
            
            // Force visibility with explicit styles
            contentElement.style.display = 'block';
            contentElement.style.visibility = 'visible';
            contentElement.style.opacity = '1';
            contentElement.style.color = 'var(--text-dark)';
            contentElement.style.background = 'var(--bg-white)';
            contentElement.style.whiteSpace = 'pre-wrap';
            contentElement.style.wordWrap = 'break-word';
            contentElement.style.overflowWrap = 'break-word';
            contentElement.style.padding = '1rem';
            contentElement.style.margin = '0';
            contentElement.style.boxSizing = 'border-box';
            
            // Use scrollHeight to get actual content height and set it explicitly
            setTimeout(function() {
                const scrollHeight = contentElement.scrollHeight;
                if (scrollHeight > 0) {
                    contentElement.style.height = scrollHeight + 'px';
                    console.log('Set height from scrollHeight:', scrollHeight);
                } else {
                    // Fallback: estimate from content
                    const charCount = contentElement.textContent.length;
                    const estimatedHeight = Math.max(400, Math.ceil(charCount / 4)); // ~4 chars per pixel
                    contentElement.style.height = estimatedHeight + 'px';
                    console.log('Set estimated height:', estimatedHeight);
                }
            }, 10);
            
            // Also ensure parent is visible
            const contentViewer = document.getElementById('contentViewer');
            if (contentViewer) {
                contentViewer.style.display = 'block';
                contentViewer.style.visibility = 'visible';
                contentViewer.style.opacity = '1';
                console.log('✅ Content viewer container made visible');
            }
            
            // Check if content is actually visible
            const computedStyle = window.getComputedStyle(contentElement);
            const rect = contentElement.getBoundingClientRect();
            console.log('Content element computed styles:', {
                display: computedStyle.display,
                visibility: computedStyle.visibility,
                opacity: computedStyle.opacity,
                color: computedStyle.color,
                height: computedStyle.height,
                width: computedStyle.width,
                boundingRect: { width: rect.width, height: rect.height, top: rect.top, left: rect.left }
            });
            
            // If element has no dimensions, force them
            if (rect.height === 0 || rect.width === 0) {
                console.log('Element text content length:', contentElement.textContent.length);
                console.log('Element innerHTML length:', contentElement.innerHTML.length);
                
                // Force explicit dimensions
                contentElement.style.minHeight = '200px';
                contentElement.style.height = 'auto';
                contentElement.style.width = '100%';
                contentElement.style.position = 'relative';
                contentElement.style.whiteSpace = 'pre-wrap';
                contentElement.style.padding = '1rem';
                contentElement.style.margin = '0';
                contentElement.style.boxSizing = 'border-box';
                
                // Force parent dimensions too
                if (contentViewer) {
                    contentViewer.style.minHeight = '400px';
                    contentViewer.style.height = 'auto';
                    contentViewer.style.width = '100%';
                    contentViewer.style.position = 'relative';
                    contentViewer.style.overflow = 'visible';
                }
                
                // Try to force a reflow
                void contentElement.offsetHeight;
                
                // Check again after forcing
                setTimeout(function() {
                    const newRect = contentElement.getBoundingClientRect();
                    console.log('After forcing dimensions:', {
                        width: newRect.width,
                        height: newRect.height,
                        top: newRect.top,
                        left: newRect.left
                    });
                    
                    if (newRect.height === 0) {
                  
                        // Last resort: set explicit height based on content
                        const text = contentElement.textContent || '';
                        const lineCount = text.split('\n').length;
                        const charCount = text.length;
                        // Estimate: ~80 chars per line, ~20px per line
                        const estimatedLines = Math.max(lineCount, Math.ceil(charCount / 80));
                        const estimatedHeight = Math.max(400, estimatedLines * 20);
                        contentElement.style.height = estimatedHeight + 'px';
                        contentElement.style.overflowY = 'auto';
                        console.log('Set explicit height to:', estimatedHeight, 'px (lines:', estimatedLines, ', chars:', charCount, ')');
                        
                        // Also try setting scrollHeight if available
                        if (contentElement.scrollHeight > 0) {
                            contentElement.style.height = contentElement.scrollHeight + 'px';
                            console.log('Using scrollHeight:', contentElement.scrollHeight);
                        }
                    }
                }, 50);
            }
            
            // Last resort: try to make it absolutely visible
            setTimeout(function() {
                contentElement.scrollIntoView({ behavior: 'auto', block: 'start' });
                console.log('Attempted to scroll content into view');
            }, 100);
        } else {
            console.log('⚠️ Content element is empty');
        }
    } else {
        console.warn('Content element not found - this is expected if no content is available');
    }
    
    // Initialize search state
    restoreSearchState();

    bindFileRecordActions();
    hydrateRichFileDetails().finally(() => initFileDetailWorkstation());
    if (document.body) document.body.dataset.fileDetailMounted = '1';
}

/** Load the same rich metadata/analysis panels as the FMAS file modal. */
async function hydrateRichFileDetails() {
    if (!fileId) return;
    try {
        const { hydrateStandaloneFileDetailPage } = await import(
            '../modules/file-operations/file-details-panels.js'
        );
        await hydrateStandaloneFileDetailPage({
            fileId,
            translations,
            onLineageClick: (id) => {
                const url = `/file/${id}`;
                if (window.swapNavigate) window.swapNavigate(url);
                else window.location.href = url;
            },
        });
        syncFileAnalystWorkspaceHeight();
    } catch (err) {
        console.error('Could not load rich file details:', err);
        const meta = document.getElementById('pageFileMetadataSection');
        if (meta) {
            meta.innerHTML = `<div class="empty-state error">${escapeHtml(
                translations.errorLoadingFileDetails || 'Error loading file details'
            )}</div>`;
        }
    }
}

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

export default function initFileDetailPage() {
    mountFileDetailPage();
}

// ==================== SEARCH (whole document, format-preserving) =========
// The in-document search is precise across the WHOLE file: match positions
// come from /file/<id>/search (literal matching, absolute offsets), the
// current page is highlighted in-place with the shared DOM-safe highlighter
// (type-specific formatting is NEVER destroyed), and next/previous navigate
// across pages - landing exactly on the requested match.

// Whole-document state
let docMatches = [];        // [{start, end, text, line}] absolute offsets
let currentDocMatch = -1;   // index into docMatches

function pageStartOffset() {
    return (currentPage - 1) * perPage;
}

/** Wait until the type-aware formatter has finished rendering the content
 *  (highlighting before formatting would be wiped by the formatter). */
function waitForFormattedContent() {
    return new Promise(resolve => {
        const el = document.getElementById('contentText');
        if (!el) return resolve();
        if (el.dataset.formatted === 'true') return resolve();
        const timeout = setTimeout(resolve, 2000); // never block forever
        el.addEventListener('content:formatted', () => {
            clearTimeout(timeout);
            resolve();
        }, { once: true });
    });
}

async function performSearch() {
    const input = document.getElementById('searchInput');
    const query = input ? input.value.trim() : '';
    const csEl = document.getElementById('caseSensitive');
    const wwEl = document.getElementById('wholeWord');
    const caseSensitiveFlag = csEl ? csEl.checked : false;
    const wholeWordFlag = wwEl ? wwEl.checked : false;

    const contentElement = document.getElementById('contentText');
    if (!contentElement) return;

    if (!query) {
        clearSearch();
        return;
    }

    const pattern = buildPattern(query, { caseSensitive: caseSensitiveFlag, wholeWord: wholeWordFlag });
    clearHighlights(contentElement);

    // Whole-document matches from the server (literal, precise offsets).
    try {
        const params = new URLSearchParams({
            q: query,
            case_sensitive: String(caseSensitiveFlag),
            whole_word: String(wholeWordFlag),
            per_page: String(perPage),
        });
        const res = await fetch(`/file/${fileId}/search?${params.toString()}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        docMatches = data.matches || [];
    } catch (e) {
        console.error('Whole-document search failed:', e);
        docMatches = [];
    }

    await waitForFormattedContent();

    // Visual highlight of the rendered page (DOM-safe).
    highlight(contentElement, pattern);

    // Where to land: a pending cross-page target, else the first match.
    let target = -1;
    try {
        const pending = JSON.parse(sessionStorage.getItem('fileDetailSearchTarget') || 'null');
        sessionStorage.removeItem('fileDetailSearchTarget');
        if (pending && pending.query === query && Number.isInteger(pending.docMatchIndex)) {
            target = pending.docMatchIndex;
        }
    } catch (e) { /* ignore corrupt state */ }

    if (docMatches.length === 0) {
        currentDocMatch = -1;
    } else if (target >= 0 && target < docMatches.length) {
        currentDocMatch = target;
    } else {
        currentDocMatch = firstMatchOnPage(pageStartOffset()) ?? 0;
    }

    const searchResultsDiv = document.getElementById('searchResults');
    if (searchResultsDiv) searchResultsDiv.style.display = 'block';

    updateSearchUI();
    focusCurrentMatch();
}

/** Index of the first whole-document match that begins on the current page. */
function firstMatchOnPage(startOffset) {
    for (let i = 0; i < docMatches.length; i++) {
        if (docMatches[i].start >= startOffset &&
            docMatches[i].start < startOffset + perPage) {
            return i;
        }
    }
    return null;
}

/** Highlight/scroll to the current match, navigating pages if needed. */
function focusCurrentMatch() {
    if (docMatches.length === 0 || currentDocMatch < 0) return;
    const match = docMatches[currentDocMatch];
    const pageOfMatch = Math.floor(match.start / perPage) + 1;

    if (pageOfMatch !== currentPage && pageOfMatch >= 1 && pageOfMatch <= totalPages) {
        // Cross-page navigation: persist the exact target match, then load
        // that page (the search state travels via the URL parameters).
        sessionStorage.setItem('fileDetailSearchTarget', JSON.stringify({
            query: document.getElementById('searchInput').value.trim(),
            docMatchIndex: currentDocMatch,
        }));
        goToPage(pageOfMatch);
        return;
    }

    // Same page: map the match to its mark on the rendered page. The Nth
    // mark corresponds to the Nth match whose start falls inside this page.
    const startOffset = pageStartOffset();
    let markIndex = 0;
    for (let i = 0; i < docMatches.length; i++) {
        if (docMatches[i].start >= startOffset && docMatches[i].start < startOffset + perPage) {
            if (i === currentDocMatch) break;
            markIndex++;
        }
    }
    const contentElement = document.getElementById('contentText');
    const marks = getMarks(contentElement);
    if (marks.length === 0) return;
    setCurrentMatch(contentElement, Math.min(markIndex, marks.length - 1));

    // Precise location context: worksheet + cell address / slide / page,
    // falling back to the line number.
    const badge = document.getElementById('currentMatch');
    if (badge) badge.textContent += currentMatchLocationText();
}

function updateSearchUI() {
    const resultCount = document.getElementById('resultCount');
    const currentMatch = document.getElementById('currentMatch');

    const total = docMatches.length;
    if (total === 0) {
        resultCount.textContent = translations.noMatches || '0 results';
        resultCount.className = 'badge bg-secondary';
        currentMatch.textContent = translations.matchOf
            ? translations.matchOf.replace('{current}', 0).replace('{total}', 0)
            : 'Match 0 of 0';
        currentMatch.className = 'badge bg-secondary';
    } else {
        resultCount.textContent = (translations.matchesFound || '{count} matches')
            .replace('{count}', total.toLocaleString());
        resultCount.className = 'badge bg-primary';
        let text = (translations.matchOf || 'Match {current} of {total}')
            .replace('{current}', currentDocMatch + 1).replace('{total}', total);
        currentMatch.textContent = text;
        currentMatch.className = 'badge bg-info';
    }
}

/**
 * Structured location of the current in-document match: for spreadsheets
 * the worksheet + exact cell address (e.g. "Budget · B3"); for decks and
 * PDFs the slide/page number. Falls back to the plain line number.
 */
function currentMatchLocationText() {
    const match = docMatches[currentDocMatch];
    if (!match) return '';

    const mark = document.querySelector('#contentText mark.content-hl.current');
    if (mark) {
        const cell = mark.closest('td[data-cell-addr]');
        if (cell) {
            const section = cell.closest('.sheet-section');
            const titleEl = section ? section.querySelector('.sheet-section-title') : null;
            const sheetName = titleEl ? titleEl.textContent.trim() : '';
            return (sheetName ? ` · ${sheetName} · ` : ' · ') + cell.dataset.cellAddr;
        }
        const slide = mark.closest('.formatted-slide');
        if (slide && slide.dataset.slideNumber) {
            return (translations.slideLabel || ' · Slide {n}').replace('{n}', slide.dataset.slideNumber);
        }
        const page = mark.closest('.formatted-page');
        if (page) {
            const chip = page.querySelector('.page-number-chip');
            if (chip) return (translations.pageLabel || ' · Page {n}').replace('{n}', chip.textContent.trim());
        }
    }

    return match.line ? (translations.onLine || ' · line {line}').replace('{line}', match.line) : '';
}

function findNext() {
    if (docMatches.length === 0) return;
    currentDocMatch = (currentDocMatch + 1) % docMatches.length;
    updateSearchUI();
    focusCurrentMatch();
}

function findPrevious() {
    if (docMatches.length === 0) return;
    currentDocMatch = (currentDocMatch - 1 + docMatches.length) % docMatches.length;
    updateSearchUI();
    focusCurrentMatch();
}

function restoreSearchState() {
    try {
        const input = document.getElementById('searchInput');
        const csEl = document.getElementById('caseSensitive');
        const wwEl = document.getElementById('wholeWord');
        if (typeof searchQuery === 'string' && searchQuery.length > 0 && input) {
            input.value = searchQuery;
            if (csEl) csEl.checked = caseSensitive;
            if (wwEl) wwEl.checked = wholeWord;
            // Give the formatter a moment, then run the whole-document search.
            setTimeout(performSearch, 150);
        }
    } catch (error) {
        console.error('Error restoring search state:', error);
    }
}

function clearSearch() {
    docMatches = [];
    currentDocMatch = -1;

    const searchInput = document.getElementById('searchInput');
    const searchResultsDiv = document.getElementById('searchResults');

    if (searchInput) searchInput.value = '';
    if (searchResultsDiv) searchResultsDiv.style.display = 'none';

    // Remove marks WITHOUT touching the formatted content.
    const contentElement = document.getElementById('contentText');
    if (contentElement) {
        clearHighlights(contentElement);
    }
}

// Expose handlers for the inline onclick attributes in the template
// (this file is an ES module, so top-level functions are not global).
window.performSearch = performSearch;
window.clearSearch = clearSearch;
window.findNext = findNext;
window.findPrevious = findPrevious;
window.goToPage = goToPage;
window.changePageSize = changePageSize;
window.jumpToPage = jumpToPage;
window.copyContent = copyContent;
window.downloadContent = downloadContent;
// The Full screen button is wired with data-on-click, which resolves names on
// window; a module's functions are not global (it never worked inline either).
window.toggleFullscreen = toggleFullscreen;

// ==================== PAGINATION ====================

function goToPage(page) {
    if (page < 1 || page > totalPages) return;
    const url = new URL(window.location);
    url.searchParams.set('page', page);
    url.searchParams.set('per_page', perPage);
    // Keep the live in-document search state across page navigation so the
    // term stays located precisely while paging through the document.
    const input = document.getElementById('searchInput');
    const csEl = document.getElementById('caseSensitive');
    const wwEl = document.getElementById('wholeWord');
    if (input && input.value.trim()) {
        url.searchParams.set('search', input.value.trim());
        url.searchParams.set('case_sensitive', csEl ? String(csEl.checked) : 'false');
        url.searchParams.set('whole_word', wwEl ? String(wwEl.checked) : 'false');
    } else {
        url.searchParams.delete('search');
        url.searchParams.delete('case_sensitive');
        url.searchParams.delete('whole_word');
    }
    if (window.swapNavigate) { window.swapNavigate(url.toString()); } else { window.location.href = url.toString(); }
}

function changePageSize(newPerPage) {
    const url = new URL(window.location);
    url.searchParams.set('page', 1);
    url.searchParams.set('per_page', newPerPage);
    if (window.swapNavigate) { window.swapNavigate(url.toString()); } else { window.location.href = url.toString(); }
}

function jumpToPage() {
    const pageInput = document.getElementById('pageJump');
    const page = parseInt(pageInput.value);
    
    if (page >= 1 && page <= totalPages) {
        goToPage(page);
    } else {
        alert(`Please enter a page number between 1 and ${totalPages}`);
        pageInput.value = currentPage;
    }
}

// ==================== CHARTS ====================

function initializeCharts() {
    // Prevent multiple initializations
    if (chartsInitialized) {
        console.log('Charts already initialized, skipping...');
        return;
    }
    
    console.log('Initializing charts...');
    
    if (typeof Chart === 'undefined') {
        console.error('Chart.js not loaded!');
        setTimeout(() => {
            if (typeof Chart !== 'undefined') {
                initializeCharts();
            }
        }, 500);
        return;
    }
    
    Object.values(chartInstances).forEach(chart => {
        if (chart && typeof chart.destroy === 'function') {
            try {
                chart.destroy();
            } catch (e) {
                console.log('Error destroying chart:', e);
            }
        }
    });
    chartInstances = {};
    
    createClassificationChart();
    createFrequencyChart();
    
    // Mark as initialized
    chartsInitialized = true;
    console.log('Charts initialized successfully');
}

function createClassificationChart() {
    const ctx = document.getElementById('classificationChart');
    if (!ctx) {
        console.warn('Classification chart canvas not found');
        return;
    }
    
    if (typeof Chart === 'undefined') {
        ctx.parentElement.innerHTML = `<div class="text-center py-5"><i class="bi bi-exclamation-circle display-4 text-danger"></i><p class="text-danger mt-3">${translations.chartLibraryNotLoaded}</p></div>`;
        return;
    }
    
    const chartPercentages = percentages;
    console.log('Classification percentages data:', chartPercentages);
    
    if (!chartPercentages || Object.keys(chartPercentages).length === 0) {
        console.warn('No classification data available for chart');
        ctx.parentElement.innerHTML = `<div class="text-center py-5"><i class="bi bi-graph-down display-4 text-muted"></i><p class="text-muted mt-3">${translations.noDataAvailable}</p></div>`;
        return;
    }
    
    try {
        const chartLabels = Object.keys(chartPercentages);
        const chartData = Object.values(chartPercentages).map(p => {
            if (typeof p === 'object' && p !== null && 'percentage' in p) {
                return p.percentage || 0;
            }
            return p || 0;
        });
        const colors = generateColors(chartLabels.length);
        
        chartInstances.classification = new Chart(ctx, {
            type: 'pie',
            data: {
                labels: chartLabels,
                datasets: [{
                    data: chartData,
                    backgroundColor: colors,
                    borderColor: colors.map(c => adjustBrightness(c, -20)),
                    borderWidth: 2
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        position: 'bottom',
                        labels: {
                            padding: 10,
                            usePointStyle: true,
                            font: { size: 11 }
                        }
                    },
                    tooltip: {
                        callbacks: {
                            label: (context) => context.label + ': ' + context.parsed.toFixed(1) + '%'
                        }
                    }
                }
            }
        });
        
        // Attach chart controls
        if (window.ChartExport) {
            const chartContainer = ctx.closest('.chart-container, .section-card');
            if (chartContainer) {
                setTimeout(() => {
                    window.ChartExport.attachExportButtonsToCharts(chartContainer);
                }, 100);
            }
        }
    } catch (error) {
        console.error('Error creating classification chart:', error);
        ctx.parentElement.innerHTML = '<div class="text-center py-5"><i class="bi bi-exclamation-circle display-4 text-danger"></i><p class="text-danger mt-3">Error: ' + error.message + '</p></div>';
    }
}

function createFrequencyChart() {
    const ctx = document.getElementById('frequencyChart');
    if (!ctx) {
        console.warn('Frequency chart canvas not found');
        return;
    }
    
    if (typeof Chart === 'undefined') {
        console.error('Chart.js not loaded');
        ctx.parentElement.innerHTML = `<div class="text-center py-5"><i class="bi bi-exclamation-circle display-4 text-danger"></i><p class="text-danger mt-3">${translations.chartLibraryNotLoaded}</p></div>`;
        return;
    }
    
    const wordFreqData = wordFrequencies;
    console.log('Word frequency data:', wordFreqData);
    
    if (!wordFreqData || (Array.isArray(wordFreqData) && wordFreqData.length === 0)) {
        console.log('No word frequency data available');
        ctx.parentElement.innerHTML = `<div class="text-center py-5"><i class="bi bi-graph-down display-4 text-muted"></i><p class="text-muted mt-3">${translations.noFrequencyDataAvailable}</p></div>`;
        return;
    }
    
    try {
        // Handle multiple data formats flexibly
        let topWords = [];
        
        if (Array.isArray(wordFreqData)) {
            topWords = wordFreqData.slice(0, 15);
        } else if (wordFreqData && typeof wordFreqData === 'object') {
            // Convert object to array format
            topWords = Object.entries(wordFreqData)
                .map(([word, count]) => [word, count])
                .sort((a, b) => (b[1] || 0) - (a[1] || 0))
                .slice(0, 15);
        } else {
            console.warn('Unexpected word frequency data format:', typeof wordFreqData);
            ctx.parentElement.innerHTML = `<div class="text-center py-5"><i class="bi bi-graph-down display-4 text-muted"></i><p class="text-muted mt-3">${translations.invalidDataFormat}</p></div>`;
            return;
        }
        
        const freqLabels = topWords.map(w => {
            if (Array.isArray(w)) {
                return String(w[0] || w.word || '');
            }
            if (w && typeof w === 'object') {
                return String(w.word || w.text || w.keyword || '');
            }
            return String(w || '');
        }).filter(label => label.length > 0);
        
        const freqData = topWords.map(w => {
            if (Array.isArray(w)) {
                return Number(w[1] || w.count || w.frequency || 0);
            }
            if (w && typeof w === 'object') {
                return Number(w.count || w.frequency || w.word_count || w.occurrences || 0);
            }
            return Number(w) || 0;
        }).filter(val => !isNaN(val) && val > 0);
        
        if (freqLabels.length === 0 || freqData.length === 0) {
            console.warn('No valid frequency data extracted');
            ctx.parentElement.innerHTML = `<div class="text-center py-5"><i class="bi bi-graph-down display-4 text-muted"></i><p class="text-muted mt-3">${translations.noValidFrequencyData}</p></div>`;
            return;
        }
        
        // Ensure arrays have same length
        const minLength = Math.min(freqLabels.length, freqData.length);
        const finalLabels = freqLabels.slice(0, minLength);
        const finalData = freqData.slice(0, minLength);
        
        console.log(`Creating frequency chart with ${finalLabels.length} items`);
        
        // Create gradient using theme colors
        const chartCtx = ctx.getContext('2d');
        const gradient = chartCtx.createLinearGradient(0, 0, 0, 400);
        const primaryColor = getComputedStyle(document.documentElement).getPropertyValue('--primary-color').trim() || '#4f46e5';
        const secondaryColor = getComputedStyle(document.documentElement).getPropertyValue('--secondary-color').trim() || '#06b6d4';
        gradient.addColorStop(0, primaryColor);
        gradient.addColorStop(1, secondaryColor);
        
        chartInstances.frequency = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: finalLabels,
                datasets: [{
                    label: 'Occurrences',
                    data: finalData,
                    backgroundColor: gradient,
                    borderColor: primaryColor,
                    borderWidth: 1,
                    borderRadius: 4
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            label: (context) => 'Count: ' + context.parsed.y
                        }
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        ticks: { precision: 0 }
                    },
                    x: {
                        ticks: {
                            maxRotation: 45,
                            minRotation: 45
                        }
                    }
                }
            }
        });
        
        // Attach chart controls
        if (window.ChartExport) {
            const chartContainer = ctx.closest('.chart-container, .section-card');
            if (chartContainer) {
                setTimeout(() => {
                    window.ChartExport.attachExportButtonsToCharts(chartContainer);
                }, 100);
            }
        }
        
        console.log('✅ Frequency chart created successfully');
    } catch (error) {
        console.error('Error creating frequency chart:', error);
        console.error('Error stack:', error.stack);
        ctx.parentElement.innerHTML = '<div class="text-center py-5"><i class="bi bi-exclamation-circle display-4 text-danger"></i><p class="text-danger mt-3">Error: ' + (error.message || 'Unknown error') + '</p></div>';
    }
}

// ==================== UTILITY FUNCTIONS ====================

function generateColors(count) {
    const colors = [];
    const hueStep = 360 / count;
    for (let i = 0; i < count; i++) {
        colors.push(`hsl(${i * hueStep}, 70%, 60%)`);
    }
    return colors;
}

function adjustBrightness(color, amount) {
    const usePound = color[0] === '#';
    const col = usePound ? color.slice(1) : color;
    const num = parseInt(col, 16);
    let r = (num >> 16) + amount;
    let g = (num >> 8 & 0x00FF) + amount;
    let b = (num & 0x0000FF) + amount;
    
    r = r > 255 ? 255 : r < 0 ? 0 : r;
    g = g > 255 ? 255 : g < 0 ? 0 : g;
    b = b > 255 ? 255 : b < 0 ? 0 : b;
    
    return (usePound ? '#' : '') + (r << 16 | g << 8 | b).toString(16);
}

// ==================== CONTENT ACTIONS ====================

function copyContent() {
    const content = document.getElementById('contentText').textContent;
    navigator.clipboard.writeText(content).then(() => {
        alert(translations.contentCopiedToClipboard);
    }).catch(err => {
        console.error('Failed to copy:', err);
    });
}

function downloadContent() {
    const content = document.getElementById('contentText').textContent;
    const blob = new Blob([content], { type: 'text/plain' });
    const suggested = `${fileName || 'file'}_page_${currentPage}_of_${totalPages}.txt`;
    if (!window.FileActions || typeof window.FileActions.saveFile !== 'function') {
        if (window.Toast) window.Toast.error(translations.error);
        return;
    }
    window.FileActions.saveFile({ blob, suggestedName: suggested, extension: '.txt' });
}

// ==================== FULLSCREEN & SHARE ====================

/**
 * Full screen and print: how the page is shown, not what the record is.
 *
 * `shareFile()` and `exportFile()` are gone with the rest of the dead
 * controls. Share copied a link to `/file/<id>/share`, which no route ever
 * served; export navigated to `/file/<id>/export`, which no route served
 * either. What replaced them is real: the record's actions are prepared by the
 * server from the Action Registry and bound above, and "Export Extracted Text"
 * is one of them.
 */
function toggleFullscreen() {
    const elem = document.documentElement;
    if (!document.fullscreenElement) {
        elem.requestFullscreen().catch(err => {
            if (window.Toast) {
                window.Toast.error(translations.error);
            }
        });
    } else {
        document.exitFullscreen();
    }
}

/**
 * Attach image error handlers to all images in the content
 * Global function so it can be used from other modules
 * @param {HTMLElement} container - Container element with images
 */
window.attachImageErrorHandlers = function attachImageErrorHandlers(container) {
    if (!container) return;

    const isSafeFallbackSource = (source) => {
        if (typeof source !== 'string' || !source) return false;
        try {
            const url = new URL(source, window.location.origin);
            return url.origin === window.location.origin && (
                url.pathname === '/api/file/serve' || /^\/api\/file\/\d+\/serve$/.test(url.pathname)
            );
        } catch (_) {
            return false;
        }
    };

    const images = container.querySelectorAll('.formatted-image');
    images.forEach(img => {
        if (img.dataset.errorHandlersBound === 'true') return;
        const errorDiv = img.nextElementSibling;
        if (!errorDiv || !errorDiv.classList.contains('image-load-error')) return;
        img.dataset.errorHandlersBound = 'true';

        const filePath = img.getAttribute('data-file-path') || '';
        const fallbackSources = img.getAttribute('data-fallback-sources');

        img.addEventListener('load', function() {
            errorDiv.style.display = 'none';
        });

        img.addEventListener('error', function() {
            let currentIndex = Number.parseInt(img.getAttribute('data-current-source-index') || '0', 10);
            if (!Number.isSafeInteger(currentIndex) || currentIndex < 0) currentIndex = 0;

            // Only follow same-origin file-serving routes generated by the
            // formatter. A corrupted data attribute must not become an
            // arbitrary navigation or remote image request.
            if (fallbackSources) {
                try {
                    const sources = JSON.parse(fallbackSources);
                    const nextIndex = currentIndex + 1;
                    if (Array.isArray(sources) && nextIndex < sources.length &&
                        isSafeFallbackSource(sources[nextIndex])) {
                        img.setAttribute('data-current-source-index', String(nextIndex));
                        img.src = sources[nextIndex];
                        return;
                    }
                } catch (error) {
                    console.error('Error parsing fallback sources:', error);
                }
            }

            img.style.display = 'none';
            errorDiv.style.display = 'block';
            const errorMsg = errorDiv.querySelector('.error-message');
            if (!errorMsg) return;

            // Paths originate in the database and are not HTML. Build the
            // error message as text and DOM nodes rather than parsing markup.
            errorMsg.replaceChildren();
            if (filePath) {
                errorMsg.append(document.createTextNode('Image could not be loaded from: '));
                const path = document.createElement('code');
                path.textContent = filePath;
                errorMsg.append(path, document.createElement('br'));
                const note = document.createElement('small');
                note.textContent = 'Please ensure the file exists and is accessible.';
                errorMsg.append(note);
            } else {
                errorMsg.textContent = 'Image could not be loaded. Please ensure the file exists and is accessible.';
            }
        });
    });
};

/**
 * This record's actions, on the shared surface.
 *
 * The surface draws what the server prepared from the Action Registry: the
 * question, the scope, the group, and whether the record currently allows it.
 * This script owns only what a server cannot do: the request, the redirect,
 * and what the reader is told when it ends. It names the one action whose
 * outcome changes where the reader goes; every address, method and question is
 * read off the element, because the server decided all of them.
 */
function bindFileRecordActions() {
    const surface = document.getElementById('fileRecordActions');
    if (!surface || !window.RecordActions) return;

    window.RecordActions.bind(surface, async function (actionId, context) {
        const element = context.element;
        const endpoint = element.getAttribute('data-record-endpoint');
        const method = element.getAttribute('data-record-method') || 'POST';

        if (actionId === 'files.delete') {
            const response = await fetch(endpoint, {
                method: method,
                headers: {
                    'X-CSRFToken': (window.CSRF && window.CSRF.getToken) ? window.CSRF.getToken() : '',
                    'Accept': 'application/json',
                },
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || payload.success === false) {
                return {ok: false, detail: null};
            }
            // The record is gone: the reader goes back to the library rather
            // than staying on a page describing something that no longer
            // exists.
            if (window.swapNavigate) { window.swapNavigate('/files'); } else { window.location.href = '/files'; }
            return {ok: true, handled: true};
        }

        // Downloads and exports are files, not pages: they never go through
        // the content swap (the server answers with an attachment, which the
        // swap cannot parse). The reader names the file first, then saves it.
        const displayName = (document.querySelector('.file-analyst-title')
            || {}).textContent || 'file';
        const stem = displayName.replace(/\.[^.]+$/, '') || 'file';

        if (actionId === 'files.view_original') {
            // Shown in its own tab, as the element says (target=_blank).
            window.open(context.href || element.getAttribute('href'), '_blank', 'noopener');
            return {ok: true, handled: true};
        }

        if (actionId === 'files.download_original') {
            return window.FileActions.saveFile({
                url: context.href || element.getAttribute('href'),
                suggestedName: displayName,
            });
        }

        if (actionId === 'files.export_content' && endpoint) {
            return window.FileActions.saveFile({
                url: endpoint,
                suggestedName: stem + '_extracted_text.txt',
                extension: '.txt',
            });
        }

        // Anything else the server prepared as a plain link: follow it.
        if (endpoint) {
            if (window.swapNavigate) { window.swapNavigate(endpoint); } else { window.location.href = endpoint; }
            return {ok: true, handled: true};
        }
        return {ok: true, handled: false};
    });
}

