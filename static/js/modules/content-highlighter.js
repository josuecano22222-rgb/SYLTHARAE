/**
 * Content Highlighter - shared, DOM-safe match highlighting for every
 * content display surface (File Detail, Full Content Reader, previews).
 *
 * Design goals (content-display consistency requirements):
 *   1. NEVER destroy type-specific formatting. Earlier implementations
 *      replaced the container's innerHTML with flat text, which flattened
 *      formatted tables/slides/email structure the moment a search ran.
 *      This module walks the LIVE DOM text nodes instead and wraps matches
 *      in <mark> elements, leaving every other node untouched.
 *   2. Identical behavior on every surface: same mark classes, same
 *      current-match semantics, same scroll-to-match behavior.
 *   3. Literal, precise matching: the query is escaped, so "C++ (2026)"
 *      finds exactly that text and never throws / never acts as a regex.
 *
 * Public API:
 *   buildPattern(query, {caseSensitive, wholeWord}) -> RegExp | null
 *   highlight(root, pattern)            -> number of marks created
 *   clearHighlights(root)               -> removes marks, restores text
 *   setCurrentMatch(root, index)        -> marks marks[index] as current + scrolls
 *   getMarks(root)                      -> NodeListOf<mark>
 */

/** Escape a literal string for safe embedding in a RegExp. */
function escapeRegExp(text) {
    return String(text).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/**
 * Build the match pattern for a query.
 * Literal substring semantics by default; optional whole-word boundaries.
 * Returns null for empty queries.
 */
export function buildPattern(query, options = {}) {
    const q = String(query || '').trim();
    if (!q) return null;
    const { caseSensitive = false, wholeWord = false } = options;
    let source = escapeRegExp(q);
    if (wholeWord) {
        // Unicode-aware word boundaries where supported; ASCII fallback.
        try {
            new RegExp(`(?<=\\p{L}\\p{N})|(?=\\p{L}\\p{N})`, 'u');
            source = `(?<![\\p{L}\\p{N}_])${source}(?![\\p{L}\\p{N}_])`;
        } catch (e) {
            source = `\\b${source}\\b`;
        }
    }
    try {
        return new RegExp(source, caseSensitive ? 'g' : 'gi');
    } catch (e) {
        return null;
    }
}

/** Collect (in document order) every text node under root, skipping
 *  script/style content and existing marks. */
function textNodes(root) {
    const nodes = [];
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
        acceptNode(node) {
            const parentName = node.parentNode ? node.parentNode.nodeName : '';
            if (parentName === 'SCRIPT' || parentName === 'STYLE' ||
                parentName === 'MARK' || parentName === 'NOSCRIPT') {
                return NodeFilter.FILTER_REJECT;
            }
            return node.nodeValue && node.nodeValue.length
                ? NodeFilter.FILTER_ACCEPT
                : NodeFilter.FILTER_SKIP;
        },
    });
    let n;
    while ((n = walker.nextNode())) nodes.push(n);
    return nodes;
}

/**
 * Highlight every match of pattern inside root without altering any other
 * DOM structure. Returns the number of marks created.
 */
export function highlight(root, pattern) {
    if (!root || !pattern) return 0;
    clearHighlights(root);

    let count = 0;
    // Snapshot the node list first: splitting nodes during the walk would
    // otherwise revisit matches.
    for (const node of textNodes(root)) {
        const text = node.nodeValue;
        pattern.lastIndex = 0;
        if (!pattern.test(text)) continue;
        pattern.lastIndex = 0;

        const fragment = document.createDocumentFragment();
        let last = 0;
        let m;
        while ((m = pattern.exec(text)) !== null) {
            if (m[0].length === 0) { pattern.lastIndex++; continue; }
            if (m.index > last) {
                fragment.appendChild(document.createTextNode(text.slice(last, m.index)));
            }
            const mark = document.createElement('mark');
            mark.className = 'content-hl';
            mark.textContent = m[0];
            fragment.appendChild(mark);
            count++;
            last = m.index + m[0].length;
        }
        if (last < text.length) {
            fragment.appendChild(document.createTextNode(text.slice(last)));
        }
        if (fragment.childNodes.length) {
            node.parentNode.replaceChild(fragment, node);
        }
    }
    return count;
}

/** Remove every highlight mark under root, restoring plain text nodes. */
export function clearHighlights(root) {
    if (!root) return;
    const marks = root.querySelectorAll('mark.content-hl');
    marks.forEach(mark => {
        const parent = mark.parentNode;
        if (!parent) return;
        // Merge with adjacent text nodes so the DOM stays clean.
        parent.replaceChild(document.createTextNode(mark.textContent), mark);
        parent.normalize();
    });
}

/** All highlight marks under root, in document order. */
export function getMarks(root) {
    return root ? root.querySelectorAll('mark.content-hl') : [];
}

/**
 * Flag one mark as the current match and scroll it into view.
 * Returns the mark element (or null).
 */
export function setCurrentMatch(root, index) {
    const marks = getMarks(root);
    marks.forEach(m => m.classList.remove('current'));
    const mark = marks[index];
    if (mark) {
        mark.classList.add('current');
        mark.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
    return mark || null;
}

/**
 * Count matches in a plain string (used for whole-document search where
 * only a slice is rendered). Same literal semantics as buildPattern.
 */
export function countMatchesInText(text, pattern) {
    if (!text || !pattern) return 0;
    const rx = new RegExp(pattern.source, pattern.flags.includes('g') ? pattern.flags : pattern.flags + 'g');
    let count = 0;
    let m;
    while ((m = rx.exec(text)) !== null) {
        if (m[0].length === 0) rx.lastIndex++;
        count++;
    }
    return count;
}

/**
 * Find matches in a plain string with absolute offsets.
 * Returns [{start, end, text}] — the same shape as the server-side
 * /file/<id>/search endpoint, so client and server agree on positions.
 */
export function findMatchesInText(text, pattern) {
    if (!text || !pattern) return [];
    const rx = new RegExp(pattern.source, pattern.flags.includes('g') ? pattern.flags : pattern.flags + 'g');
    const out = [];
    let m;
    while ((m = rx.exec(text)) !== null) {
        if (m[0].length === 0) { rx.lastIndex++; continue; }
        out.push({ start: m.index, end: m.index + m[0].length, text: m[0] });
    }
    return out;
}

// Expose for non-module scripts (both page scripts are ES modules, but keep
// a global for any surface that still uses classic scripts).
window.contentHighlighter = {
    buildPattern,
    highlight,
    clearHighlights,
    getMarks,
    setCurrentMatch,
    countMatchesInText,
    findMatchesInText,
};
