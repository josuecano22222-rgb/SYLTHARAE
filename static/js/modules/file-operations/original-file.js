/**
 * Original File Viewer
 *
 * Side of the comparison the examiner asks for: "show me the file this text
 * was extracted from". File Details renders the stored extracted text; this
 * module renders the SOURCE file next to it - the picture, the PDF pages, the
 * raw text - or says plainly why it cannot, so a missing source is never
 * mistaken for an empty document.
 *
 * What it does NOT do is guess: the server describes the file (kind, mime,
 * whether it is still on disk) and this module picks the matching viewer.
 * Bytes come from /api/file/<id>/original/content, which decides disposition
 * and content type server-side (see Api/services/original_file.py).
 */

import { escapeAttribute, escapeHtml } from '../core/utils.js';
import { translations } from '../core/config.js';

/** Human-readable size, local to avoid pulling the whole utils surface. */
function humanSize(bytes) {
    if (bytes === null || bytes === undefined) return '';
    if (bytes === 0) return '0 Bytes';
    const units = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
    return `${Math.round((bytes / Math.pow(1024, i)) * 100) / 100} ${units[i]}`;
}

/**
 * HTML for the action row above the viewer.
 * @param {object} info - payload from /api/file/<id>/original
 * @returns {string} HTML
 */
function renderActionBar(info) {
    const nameAttr = escapeAttribute(info.name || 'file');
    const size = humanSize(info.size === null || info.size === undefined
        ? info.stored_size : info.size);
    const bits = [];
    if (size) bits.push(escapeHtml(size));
    if (info.extension) bits.push(escapeHtml(info.extension));
    if (info.available === false) bits.push(escapeHtml(info.reason || 'unavailable'));

    let html = '<div class="original-file-bar">';
    html += `<div class="original-file-identity"><i class="bi bi-file-earmark" aria-hidden="true"></i> `
        + `<span class="original-file-name" title="${nameAttr}">${escapeHtml(info.name || 'file')}</span>`;
    if (bits.length) html += `<span class="original-file-meta">${bits.join(' · ')}</span>`;
    html += '</div>';
    html += '<div class="original-file-actions">';
    if (info.available) {
        html += `<a class="btn btn-sm btn-outline-primary" href="${escapeAttribute(info.serve_url)}" target="_blank" rel="noopener" data-original-action="open">`
            + '<i class="bi bi-box-arrow-up-right me-1" aria-hidden="true"></i>'
            + `${escapeHtml(translations.originalOpen || 'Open in new tab')}</a>`;
        html += `<a class="btn btn-sm btn-outline-secondary" href="${escapeAttribute(info.download_url)}" data-original-action="download">`
            + '<i class="bi bi-download me-1" aria-hidden="true"></i>'
            + `${escapeHtml(translations.originalDownload || 'Download original')}</a>`;
    }
    html += '</div></div>';
    return html;
}

/**
 * HTML for the source file itself, chosen by the kind the server reported.
 * @param {object} info - payload from /api/file/<id>/original
 * @returns {string} HTML
 */
export function renderOriginalFile(info) {
    if (!info) return '';
    const nameAttr = escapeAttribute(info.name || 'file');
    const url = escapeAttribute(info.serve_url || '');
    let html = renderActionBar(info);

    if (!info.available) {
        html += '<div class="original-file-unavailable" role="status">'
            + '<i class="bi bi-exclamation-triangle me-2" aria-hidden="true"></i>'
            + `<span>${escapeHtml(info.message || 'The source file is not available.')}</span>`;
        if (info.path) {
            html += `<div class="original-file-path"><code>${escapeHtml(info.path)}</code></div>`;
        }
        html += '</div>';
        return html;
    }

    switch (info.viewer) {
        case 'img':
            html += '<div class="original-file-stage">'
                + `<img class="original-file-image" src="${url}" alt="${nameAttr}" loading="lazy" `
                + 'data-original-file-image="1"></div>';
            break;
        case 'iframe':
            // The browser's own PDF viewer. The response relaxes
            // frame-ancestors to same-origin for exactly this.
            html += '<div class="original-file-stage">'
                + `<iframe class="original-file-frame" src="${url}" title="${nameAttr}" `
                + 'data-original-file-frame="1"></iframe></div>';
            break;
        case 'audio':
            html += '<div class="original-file-stage">'
                + `<audio class="original-file-audio" controls src="${url}" data-original-file-audio="1"></audio>`
                + '</div>';
            break;
        case 'video':
            html += '<div class="original-file-stage">'
                + `<video class="original-file-video" controls src="${url}" data-original-file-video="1"></video>`
                + '</div>';
            break;
        case 'text':
            // Fetched and escaped: the source text is shown as text, never as
            // markup (an uploaded .html must not run in the app's origin).
            html += '<div class="original-file-stage">'
                + `<pre class="original-file-text" data-original-file-text="${url}">`
                + `${escapeHtml(translations.originalLoading || 'Loading original file…')}</pre></div>`;
            break;
        default:
            html += '<div class="original-file-unavailable" role="status">'
                + '<i class="bi bi-info-circle me-2" aria-hidden="true"></i>'
                + `<span>${escapeHtml(translations.originalNoInline
                    || 'This format cannot be displayed in the browser. Download it or open it in a new tab.')}</span></div>`;
            break;
    }
    return html;
}

/**
 * Render the original file into a container element.
 *
 * @param {number} fileId - stored object id
 * @param {HTMLElement} container - element to render into
 * @returns {Promise<object|null>} the info payload, or null on network failure
 */
export async function showOriginalFile(fileId, container) {
    if (!container) return null;
    container.innerHTML = `<div class="content-loading">${escapeHtml(
        translations.originalLoading || 'Loading original file…')}</div>`;

    let payload;
    try {
        const response = await fetch(`/api/file/${fileId}/original`);
        payload = await response.json();
    } catch (error) {
        container.innerHTML = `<div class="empty-state error">${escapeHtml(
            translations.originalLoadFailed || 'Could not load the original file.')}</div>`;
        return null;
    }

    const info = payload && payload.original;
    if (!info) {
        container.innerHTML = `<div class="empty-state error">${escapeHtml(
            (payload && payload.error) || translations.originalLoadFailed
            || 'Could not load the original file.')}</div>`;
        return null;
    }

    container.innerHTML = renderOriginalFile(info);
    await loadTextSource(container, info);
    return info;
}

/**
 * Fill in a text viewer's body: fetch the bytes as text, escape, show.
 * @param {HTMLElement} container - element the viewer was rendered into
 * @param {object} info - the descriptor
 */
async function loadTextSource(container, info) {
    const pre = container.querySelector('[data-original-file-text]');
    if (!pre || !info || !info.available) return;
    const url = pre.getAttribute('data-original-file-text');
    try {
        const response = await fetch(url);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const text = await response.text();
        if (text.length === 0) {
            pre.textContent = translations.originalEmpty || '(the original file is empty)';
            return;
        }
        pre.textContent = text;
    } catch (error) {
        pre.textContent = translations.originalLoadFailed
            || 'Could not load the original file.';
    }
}

export { humanSize };
