/**
 * Full Content Page JavaScript
 * Extracted from file/full_content.html
 */

import { buildPattern, highlight, clearHighlights, getMarks, setCurrentMatch, findMatchesInText }
    from '../modules/content-highlighter.js';
import { initOriginalContentTab } from '../modules/original-content-tab.js';

// --- State ---
let translations = {};
let fileId = null;
let fileName = '';
let totalChars = 0;
let buffer = []; // array of { offset, data }
let fetching = false;
let eof = false;
let wrap = true;
let fontPx = 14;
let themeDark = false;

// DOM elements (will be initialized after DOM loads)
let elReader, elContent, elSentinel, elStatus, elQ, elPrev, elNext, elClear;
let elWrap, elCopy, elDownload, elPrint, elFontInc, elFontDec, elTheme;
let elRange, elChunkSize, optCase, optWhole;

// Initialize page
document.addEventListener('DOMContentLoaded', function() {
    console.log('Full content page loaded');
    
    // Load page data from JSON script tag
    const pageDataEl = document.getElementById('full-content-page-data');
    if (pageDataEl) {
        try {
            const data = JSON.parse(pageDataEl.textContent);
            fileId = data.fileId;
            fileName = data.fileName || '';
            totalChars = data.totalChars || 0;
            translations = data.translations || {};
        } catch (e) {
            console.error('Error parsing full content page data:', e);
        }
    }
    
    // Initialize DOM elements
    elReader = document.getElementById('reader');
    elContent = document.getElementById('content');
    elSentinel = document.getElementById('sentinel');
    elStatus = document.getElementById('status');
    elQ = document.getElementById('q');
    elPrev = document.getElementById('btnPrev');
    elNext = document.getElementById('btnNext');
    elClear = document.getElementById('btnClear');
    elWrap = document.getElementById('btnWrap');
    elCopy = document.getElementById('btnCopy');
    elDownload = document.getElementById('btnDownload');
    elPrint = document.getElementById('btnPrint');
    elFontInc = document.getElementById('btnFontInc');
    elFontDec = document.getElementById('btnFontDec');
    elTheme = document.getElementById('btnTheme');
    elRange = document.getElementById('rangeJump');
    elChunkSize = document.getElementById('chunkSize');
    optCase = document.getElementById('optCase');
    optWhole = document.getElementById('optWhole');
    
    if (!fileId) {
        console.error('File ID not found');
        if (elStatus) elStatus.textContent = 'Error: File ID not found';
        return;
    }
    
    // Initialize all functionality
    initializeReader();
    // Extracted/Original tabs — the original file renders in place, where
    // the extracted text is (same pop-up viewer the analysis uses).
    initOriginalContentTab(fileId);
});

// --- Utilities ---
const escapeHtml = t => t.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
function setStatus(s) {
    if (elStatus) elStatus.textContent = s;
}

// --- Rendering ---
async function renderBuffer() {
    if (!elContent) return;
    buffer.sort((a, b) => a.offset - b.offset);
    const text = buffer.map(c => c.data).join('');
    
    // Format content based on file type
    const reader = document.getElementById('reader');
    if (reader) {
        const fileType = reader.getAttribute('data-file-type') || '';
        const filePath = reader.getAttribute('data-file-path') || '';
        const fileIdAttr = reader.getAttribute('data-file-id');
        const fileId = fileIdAttr ? parseInt(fileIdAttr, 10) : null;
        
        try {
            const formatter = await import('../modules/content-formatter.js');
            // Localize accessibility labels before the first render
            if (formatter.setContentFormatterTranslations) {
                formatter.setContentFormatterTranslations(translations);
            }
            const formatted = formatter.formatContentByType(text, fileType, filePath, fileId);
            if (formatted) {
                elContent.innerHTML = formatted;
                // Ensure proper styling for readability
                elContent.style.width = '100%';
                elContent.style.wordWrap = 'break-word';
                elContent.style.overflowWrap = 'break-word';
                elContent.style.whiteSpace = 'pre-wrap';
                // Attach image error handlers after DOM insertion
                setTimeout(() => {
                    if (window.attachImageErrorHandlers && elContent) {
                        window.attachImageErrorHandlers(elContent);
                    }
                }, 10);
                applyHighlights();
                return;
            }
        } catch (err) {
            console.error('Error formatting content:', err);
        }
    }
    
    // Fallback to plain text with proper formatting
    elContent.innerHTML = escapeHtml(text);
    elContent.style.width = '100%';
    elContent.style.wordWrap = 'break-word';
    elContent.style.overflowWrap = 'break-word';
    elContent.style.whiteSpace = 'pre-wrap';
    applyHighlights();
}

// --- Fetching ---
async function fetchChunk(offset, limit) {
    if (!fileId) throw new Error('File ID not set');
    const url = new URL(window.location.origin + `/file/${fileId}/content`);
    url.searchParams.set('offset', offset);
    url.searchParams.set('limit', limit);
    const res = await fetch(url, { headers: { 'Accept': 'application/json' } });
    if (!res.ok) throw new Error('Fetch failed');
    return res.json();
}

async function ensureNext() {
    if (fetching || eof) return;
    fetching = true;
    const limit = parseInt(elChunkSize ? elChunkSize.value : '50000', 10);
    const nextOffset = buffer.length ? buffer[buffer.length - 1].offset + buffer[buffer.length - 1].data.length : 0;
    if (totalChars && nextOffset >= totalChars) {
        eof = true;
        setStatus('All loaded');
        fetching = false;
        return;
    }
    try {
        setStatus(`Loading ${nextOffset}…`);
        const j = await fetchChunk(nextOffset, limit);
        buffer.push({ offset: j.offset, data: j.content || '' });
        totalChars = j.total_length || totalChars;
        if (!j.has_more) {
            eof = true;
        }
        renderBuffer();
        setStatus(`Loaded ${Math.min(nextOffset + (j.content ? j.content.length : 0), totalChars)} / ${totalChars}`);
    } catch (e) {
        setStatus('Error loading');
        console.error(e);
    } finally {
        fetching = false;
    }
}

// Infinite scroll via IntersectionObserver
let io = null;
if (elReader && elSentinel) {
    io = new IntersectionObserver((entries) => {
        for (const e of entries) {
            if (e.isIntersecting) {
                ensureNext();
            }
        }
    }, { root: elReader, threshold: 0.1 });
    io.observe(elSentinel);
}

// Initialize reader
function initializeReader() {
    // Load initial content from page data
    const pageDataEl = document.getElementById('full-content-page-data');
    let initialContent = '';
    let startChar = 0;
    
    let searchQuery = '';
    let caseSensitive = false;
    let wholeWord = false;
    if (pageDataEl) {
        try {
            const data = JSON.parse(pageDataEl.textContent);
            initialContent = data.initialContent || '';
            startChar = (data.startChar || 1) - 1;
            searchQuery = data.searchQuery || '';
            caseSensitive = !!data.caseSensitive;
            wholeWord = !!data.wholeWord;
        } catch (e) {
            console.error('Error parsing initial content:', e);
        }
    }
    
    // Bootstrap initial content
    if (initialContent && initialContent.length) {
        buffer.push({ offset: startChar, data: initialContent });
        renderBuffer();
    }
    
    const chunkSize = parseInt(elChunkSize ? elChunkSize.value : '50000', 10);
    if (!initialContent || initialContent.length < chunkSize) {
        ensureNext();
    }
    
    // Setup event listeners
    setupEventListeners();
    
    // A search carried in from another interface (?q=) is located on open.
    restoreSearchState(searchQuery, caseSensitive, wholeWord);
}

// --- Search (format-preserving, whole-document aware) ---
// Matching is LITERAL and consistent with every other content interface
// (server: /file/<id>/search). Highlighting walks the rendered DOM and never
// flattens the type-specific formatting, and next/previous can jump to
// matches that are not loaded yet by fetching the chunk that contains them.
let docMatches = [];        // whole-document matches [{start, end, line}]
let currentDocMatch = -1;

/**
 * Translate a reader-page string.
 *
 * The interface catalog is keyed by the English source text (window.I18N.t,
 * fed by the locale packs and the server catalog), which is also the key the
 * DOM translation pass uses. Passing the English text here is what makes the
 * reader's own status line - "Loading…", "Match 3 of 12", "Search cleared" -
 * follow the interface language like every other string on the page instead of
 * staying English. The per-page table is still consulted first for any key a
 * template provides.
 */
function t(english, fallback) {
    if (translations[english]) { return translations[english]; }
    if (window.I18N && typeof window.I18N.t === 'function') {
        const translated = window.I18N.t(english);
        if (translated && translated !== english) { return translated; }
    }
    return fallback || english;
}

function readerPattern() {
    const q = elQ ? elQ.value.trim() : '';
    if (!q) return null;
    return buildPattern(q, {
        caseSensitive: !!(optCase && optCase.checked),
        wholeWord: !!(optWhole && optWhole.checked),
    });
}

/** Absolute offsets of the raw text currently held in the buffer. */
function loadedRange() {
    if (!buffer.length) return { start: 0, end: 0 };
    const first = buffer[0];
    const last = buffer[buffer.length - 1];
    return { start: first.offset, end: last.offset + last.data.length };
}

/** Whole-document match list (authoritative count + absolute offsets). */
async function fetchDocMatches() {
    const q = elQ ? elQ.value.trim() : '';
    if (!q) { docMatches = []; return; }
    try {
        const params = new URLSearchParams({
            q,
            case_sensitive: String(!!(optCase && optCase.checked)),
            whole_word: String(!!(optWhole && optWhole.checked)),
            per_page: '5000',
        });
        const res = await fetch(`/file/${fileId}/search?${params.toString()}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        docMatches = data.matches || [];
    } catch (e) {
        console.error('Whole-document search failed, falling back to loaded content:', e);
        const pattern = readerPattern();
        if (!pattern) { docMatches = []; return; }
        const r = loadedRange();
        const text = buffer.map(c => c.data).join('');
        docMatches = findMatchesInText(text, pattern)
            .map(m => ({ start: r.start + m.start, end: r.start + m.end, line: null }));
    }
}

/** Highlight the rendered DOM in place (called after every renderBuffer). */
function applyHighlights() {
    if (!elContent) return;
    clearHighlights(elContent);
    const pattern = readerPattern();
    if (!pattern) return;
    highlight(elContent, pattern);
    if (currentDocMatch >= 0) markCurrentInDom(false);
}

/** DOM mark index for the current whole-document match (same order). */
function currentMarkIndex() {
    const r = loadedRange();
    let markIndex = 0;
    for (let i = 0; i < docMatches.length; i++) {
        const m = docMatches[i];
        if (m.start < r.start) continue;
        if (m.start >= r.end) break;
        if (i === currentDocMatch) return markIndex;
        markIndex++;
    }
    return -1;
}

function markCurrentInDom(scroll = true) {
    if (!elContent) return;
    const marks = getMarks(elContent);
    if (!marks.length) return;
    const idx = currentMarkIndex();
    if (idx < 0) return;
    setCurrentMatch(elContent, Math.min(idx, marks.length - 1));
    const current = elContent.querySelector('mark.content-hl.current');
    if (current && scroll) {
        current.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
}


/**
 * Structured location of the current match: worksheet + exact cell address
 * (e.g. "Budget · B3"), slide or page number; falls back to the line.
 */
function readerMatchLocationText(match) {
    if (elContent) {
        const mark = elContent.querySelector('mark.content-hl.current');
        if (mark) {
            const cell = mark.closest('td[data-cell-addr]');
            if (cell) {
                const section = cell.closest('.sheet-section');
                const titleEl = section ? section.querySelector('.sheet-section-title') : null;
                const sheetName = titleEl ? titleEl.textContent.trim() : '';
                return (sheetName ? ` · ${sheetName} · ` : ' · ') + cell.dataset.cellAddr;
            }
            const slide = mark.closest('.formatted-slide');
            if (slide && slide.dataset.slideNumber) return ` · Slide ${slide.dataset.slideNumber}`;
            const page = mark.closest('.formatted-page');
            if (page) {
                const chip = page.querySelector('.page-number-chip');
                if (chip) return ` · Page ${chip.textContent.trim()}`;
            }
        }
    }
    return match && match.line ? t(' · line {line}').replace('{line}', match.line) : '';
}

/** Load the chunk containing `offset` (deduplicated against the buffer). */
async function loadUntil(offset) {
    const r = loadedRange();
    if (buffer.length && offset >= r.start && offset < r.end) return true;
    const limit = parseInt(elChunkSize ? elChunkSize.value : '50000', 10);
    try {
        setStatus(t('Loading…'));
        const chunkOffset = Math.max(0, Math.floor(offset / limit) * limit);
        const j = await fetchChunk(chunkOffset, limit);
        let data = j.content || '';
        let start = j.offset;
        const cur = loadedRange();
        if (buffer.length && cur.end > start) {
            const skip = cur.end - start;
            data = data.slice(skip);
            start = cur.end;
        }
        if (data.length) buffer.push({ offset: start, data });
        totalChars = j.total_length || totalChars;
        if (!j.has_more) eof = true;
        renderBuffer();
        return true;
    } catch (e) {
        console.error('Failed to load region for match:', e);
        setStatus(t('Error loading'));
        return false;
    }
}

/** Run the search and land on the first match (Enter key). */
async function runSearch() {
    currentDocMatch = -1;
    await fetchDocMatches();
    applyHighlights();
    if (docMatches.length === 0) {
        setStatus(t('No matches'));
        return;
    }
    await gotoMatch(0);
}

async function gotoMatch(idx) {
    if (docMatches.length === 0) {
        setStatus(t('No matches'));
        return;
    }
    currentDocMatch = ((idx % docMatches.length) + docMatches.length) % docMatches.length;
    const m = docMatches[currentDocMatch];
    if (m.start < loadedRange().start || m.start >= loadedRange().end) {
        const ok = await loadUntil(m.start);
        if (!ok) return;
    }
    markCurrentInDom(true);
    let status = t('Match {current} of {total}')
        .replace('{current}', currentDocMatch + 1)
        .replace('{total}', docMatches.length);
    status += readerMatchLocationText(m);
    setStatus(status);
}

/** Restore a search carried in from another interface (?q=/link). */
async function restoreSearchState(query, caseSensitive, wholeWord) {
    if (!query || !elQ) return;
    elQ.value = query;
    if (optCase) optCase.checked = !!caseSensitive;
    if (optWhole) optWhole.checked = !!wholeWord;
    // Wait for the initial render before highlighting.
    setTimeout(runSearch, 300);
}

// Setup event listeners
function setupEventListeners() {
    if (elQ) {
        elQ.addEventListener('keydown', e => {
            if (e.key === 'Enter') {
                runSearch();
            }
        });
    }
    
    if (elNext) elNext.addEventListener('click', () => gotoMatch(currentDocMatch + 1));
    if (elPrev) elPrev.addEventListener('click', () => gotoMatch(currentDocMatch - 1));
    if (elClear) {
        elClear.addEventListener('click', () => {
            if (elQ) elQ.value = '';
            currentDocMatch = -1;
            docMatches = [];
            if (elContent) clearHighlights(elContent);
            setStatus(t('Search cleared'));
        });
    }
    if (optCase) optCase.addEventListener('change', () => runSearch());
    if (optWhole) optWhole.addEventListener('change', () => runSearch());
    
    // --- Controls ---
    if (elWrap) {
        elWrap.addEventListener('click', () => {
            wrap = !wrap;
            if (elContent) {
                elContent.classList.toggle('pre-wrap', wrap);
                elContent.classList.toggle('pre', !wrap);
            }
        });
    }
    if (elFontInc) {
        elFontInc.addEventListener('click', () => {
            fontPx = Math.min(24, fontPx + 1);
            if (elContent) elContent.style.fontSize = fontPx + 'px';
        });
    }
    if (elFontDec) {
        elFontDec.addEventListener('click', () => {
            fontPx = Math.max(10, fontPx - 1);
            if (elContent) elContent.style.fontSize = fontPx + 'px';
        });
    }
    if (elTheme) {
        elTheme.addEventListener('click', () => {
            themeDark = !themeDark;
            document.body.classList.toggle('dark', themeDark);
            elTheme.innerHTML = themeDark 
                ? '<i class="bi bi-brightness-high me-1"></i>Light' 
                : '<i class="bi bi-moon me-1"></i>Dark';
        });
    }
    
    const btnTop = document.getElementById('btnTop');
    if (btnTop && elReader) {
        btnTop.addEventListener('click', () => elReader.scrollTo({ top: 0, behavior: 'smooth' }));
    }
    
    const btnBottom = document.getElementById('btnBottom');
    if (btnBottom && elReader) {
        btnBottom.addEventListener('click', () => elReader.scrollTo({ top: elReader.scrollHeight, behavior: 'smooth' }));
    }
    
    if (elRange && elChunkSize) {
        elRange.addEventListener('input', async (e) => {
            const target = parseInt(e.target.value || '0', 10);
            buffer = [];
            eof = false;
            fetching = false;
            setStatus('Jumping…');
            if (elContent) elContent.innerHTML = '';
            const j = await fetchChunk(target, parseInt(elChunkSize.value, 10) || 50000);
            buffer.push({ offset: j.offset, data: j.content || '' });
            totalChars = j.total_length || totalChars;
            eof = !j.has_more;
            renderBuffer();
            if (elReader) elReader.scrollTop = 0;
        });
    }
    
    // Copy/Download/Print
    if (elCopy) {
        elCopy.addEventListener('click', async () => {
            setStatus('Fetching all for copy…');
            await fetchAll();
            if (elContent) {
                navigator.clipboard.writeText(elContent.textContent || '');
            }
            setStatus('Copied to clipboard');
        });
    }
    
    if (elDownload) {
        elDownload.addEventListener('click', async () => {
            setStatus('Preparing download…');
            const text = await getFullText();
            const blob = new Blob([text], { type: 'text/plain' });
            const a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = `${encodeURIComponent(fileName || 'file')}.txt`;
            document.body.appendChild(a);
            a.click();
            a.remove();
            setStatus('Download started');
        });
    }
    
    if (elPrint) {
        elPrint.addEventListener('click', async () => {
            const text = await getFullText();
            const w = window.open('', '_blank');
            if (!w) return;
            w.document.write(`<pre style="white-space:pre-wrap;font-family:monospace;">${escapeHtml(text)}</pre>`);
            w.document.close();
            w.focus();
            w.print();
        });
    }
}

async function fetchAll() {
    const limit = 100000;
    let offset = 0;
    const parts = [];
    while (!totalChars || offset < totalChars) {
        const j = await fetchChunk(offset, limit);
        parts.push(j.content || '');
        offset += (j.content ? j.content.length : 0);
        totalChars = j.total_length || totalChars;
        if (!j.has_more) break;
    }
    buffer = [{ offset: 0, data: parts.join('') }];
    eof = true;
    renderBuffer();
}

async function getFullText() {
    if (!eof) {
        await fetchAll();
    }
    return elContent ? elContent.textContent || '' : '';
}