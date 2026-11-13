/**
 * DocumentAnalysis — shared categories / category terms / keywords model.
 *
 * Fetches the structured analysis payload and renders the chip summary used
 * by the file modal and selection “View analysis” action.
 */

import { escapeHtml, formatFileSize } from '../core/utils.js';

function t(key, fallback) {
    return (window.translations && window.translations[key]) || fallback;
}

/**
 * @param {number|string} fileId
 * @returns {Promise<object|null>}
 */
export async function fetchDocumentAnalysis(fileId) {
    const id = Number(fileId);
    if (!Number.isFinite(id) || id <= 0) return null;
    const response = await fetch(`/api/file/${id}/analysis`, { credentials: 'same-origin' });
    if (!response.ok) {
        const problem = await response.json().catch(() => ({}));
        throw new Error(problem.error || `HTTP ${response.status}`);
    }
    const data = await response.json();
    if (!data.success || !data.analysis) {
        throw new Error(data.error || 'Analysis unavailable');
    }
    return data.analysis;
}

/**
 * @param {Array<number|string>} fileIds
 * @returns {Promise<object>}
 */
export async function fetchSelectionAnalysis(fileIds) {
    const ids = (fileIds || [])
        .map((v) => Number(v))
        .filter((n) => Number.isFinite(n) && n > 0);
    if (!ids.length) {
        throw new Error(t('pleaseSelectFiles', 'Please select files'));
    }
    const response = await fetch('/api/files/analysis', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.content || '',
        },
        body: JSON.stringify({ file_ids: ids }),
    });
    if (!response.ok) {
        const problem = await response.json().catch(() => ({}));
        throw new Error(problem.error || `HTTP ${response.status}`);
    }
    return response.json();
}

function chipList(items, textKey) {
    if (!items || !items.length) {
        return `<span class="doc-analysis-empty">${escapeHtml(t('none', 'None'))}</span>`;
    }
    return items.map((item) => {
        const text = item[textKey] || item.name || item.term || item.phrase || '';
        const category = item.category ? ` title="${escapeHtml(item.category)}"` : '';
        return `<span class="doc-analysis-chip"${category}>${escapeHtml(String(text))}</span>`;
    }).join('<span class="doc-analysis-sep" aria-hidden="true">·</span>');
}

function statusTone(status) {
    const s = String(status || '').toLowerCase();
    if (s.includes('fail') || s.includes('error')) return 'danger';
    if (s.includes('read') || s.includes('done') || s.includes('process') || s.includes('ok')) return 'success';
    return 'neutral';
}

function statusPill(icon, label, tone = 'neutral') {
    const toneClass = tone === 'neutral' ? '' : ` doc-status-pill--${tone}`;
    return `
        <span class="doc-status-pill${toneClass}">
            <i class="bi ${icon}" aria-hidden="true"></i>
            <span>${escapeHtml(String(label))}</span>
        </span>`;
}

function analysisLane(icon, laneClass, label, chipsHtml) {
    return `
                <section class="doc-analysis-lane ${laneClass}">
                    <div class="doc-analysis-lane__icon" aria-hidden="true"><i class="bi ${icon}"></i></div>
                    <div class="doc-analysis-lane__body">
                        <div class="doc-analysis-label">${escapeHtml(label)}</div>
                        <div class="doc-analysis-chips">${chipsHtml}</div>
                    </div>
                </section>`;
}

/**
 * Full modal status strip: identity/context pills plus all classification lanes.
 *
 * @param {object|null} analysis
 * @param {object|null} file — `/api/file/:id/details` record (optional)
 * @param {object} [options]
 * @returns {string}
 */
export function renderDocumentStatusSummary(analysis, file = null, options = {}) {
    if (!analysis && !file) {
        return `<div class="doc-analysis-summary empty">${escapeHtml(
            t('noAnalysisAvailable', 'No analysis available')
        )}</div>`;
    }

    const meta = analysis?.metadata || {};
    const status = file?.status || meta.status || '—';
    const fileType = file?.type || meta.type || '—';
    const sizeRaw = file?.size ?? meta.size;
    const sizeLabel = sizeRaw != null && sizeRaw !== '' ? formatFileSize(sizeRaw) : '—';
    const dateRaw = file?.file_date || meta.date;
    const dateLabel = dateRaw ? new Date(dateRaw).toLocaleDateString() : '—';
    const words = file?.word_count != null ? Number(file.word_count).toLocaleString() : null;
    const source = file?.source || meta.source || '';
    const side = file?.side || meta.side || '';
    const processing = file?.processing?.status
        ? String(file.processing.status).replace(/_/g, ' ')
        : '';

    const pills = [
        statusPill('bi-activity', status, statusTone(status)),
        statusPill('bi-file-earmark', fileType),
        statusPill('bi-hdd', sizeLabel),
        statusPill('bi-calendar3', dateLabel),
    ];
    if (words != null) {
        pills.push(statusPill('bi-fonts', `${words} ${t('words', 'words')}`));
    }
    if (source) pills.push(statusPill('bi-box-arrow-in-down', source));
    if (side) pills.push(statusPill('bi-layers', side));
    if (processing) pills.push(statusPill('bi-cpu', processing));

    const categories = analysis?.categories || [];
    const terms = analysis?.category_terms || [];
    const keywords = analysis?.keywords || [];

    const analystItems = (file?.analyst_categories || [])
        .map((c) => (typeof c === 'string' ? { name: c } : { name: c?.name }))
        .filter((c) => c.name);

    const smartFromFile = (file?.categories || [])
        .map((c) => ({ name: c?.name || c }))
        .filter((c) => c.name);

    let lanes = '';
    lanes += analysisLane('bi-diagram-3', 'doc-analysis-lane--categories', t('categories', 'Categories'), chipList(categories, 'name'));
    lanes += analysisLane('bi-tags', 'doc-analysis-lane--terms', t('categoryTerms', 'Category terms'), chipList(terms, 'term'));
    lanes += analysisLane('bi-key', 'doc-analysis-lane--keywords', t('keywords', 'Keywords'), chipList(keywords, 'phrase'));

    if (analystItems.length) {
        lanes += analysisLane(
            'bi-person-badge',
            'doc-analysis-lane--analyst',
            t('analystCategories', 'Analyst categories'),
            chipList(analystItems, 'name'),
        );
    }

    if (smartFromFile.length && !categories.length) {
        lanes += analysisLane(
            'bi-bookmarks',
            'doc-analysis-lane--smart',
            t('smartCategories', 'Smart categories'),
            chipList(smartFromFile, 'name'),
        );
    }

    if (file?.title) {
        lanes += analysisLane(
            'bi-file-text',
            'doc-analysis-lane--title',
            t('fileTitle', 'File title'),
            `<span class="doc-status-text">${escapeHtml(String(file.title))}</span>`,
        );
    }

    const heading = options.title || t('documentStatus', 'Document status');
    const fileCount = options.fileCount ?? analysis?.file_count ?? (analysis?.file_id ? 1 : null);
    let headingText = heading;
    if (fileCount && fileCount > 1) {
        headingText = `${heading} (${fileCount})`;
    }
    const showInlineHeading = !options.panelSection;

    return `
        <div class="doc-analysis-summary doc-document-status doc-analysis-summary--stacked" data-doc-status>
            <div class="doc-status-header">
                ${showInlineHeading ? `<div class="doc-analysis-heading">${escapeHtml(headingText)}</div>` : ''}
                <div class="doc-status-pills" role="list">${pills.join('')}</div>
            </div>
            <div class="doc-analysis-lanes">
                ${lanes}
            </div>
        </div>
    `;
}

/**
 * Render Categories / Category terms / Keywords chip summary.
 * Accepts either a single-file analysis or an aggregate object.
 *
 * @param {object} analysis
 * @param {{ title?: string, fileCount?: number }} [options]
 * @returns {string} HTML
 */
export function renderAnalysisSummary(analysis, options = {}) {
    if (!analysis) {
        return `<div class="doc-analysis-summary empty">${escapeHtml(
            t('noAnalysisAvailable', 'No analysis available')
        )}</div>`;
    }

    const categories = analysis.categories || [];
    const terms = analysis.category_terms || [];
    const keywords = analysis.keywords || [];
    const fileCount = options.fileCount
        ?? analysis.file_count
        ?? (analysis.file_id ? 1 : null);

    let heading = options.title || t('documentAnalysis', 'Document analysis');
    if (fileCount && fileCount > 1) {
        heading = `${heading} (${fileCount})`;
    }

    return `
        <div class="doc-analysis-summary doc-analysis-summary--stacked" data-doc-analysis>
            <div class="doc-analysis-heading">${escapeHtml(heading)}</div>
            <div class="doc-analysis-lanes">
                <section class="doc-analysis-lane doc-analysis-lane--categories">
                    <div class="doc-analysis-lane__icon" aria-hidden="true"><i class="bi bi-diagram-3"></i></div>
                    <div class="doc-analysis-lane__body">
                        <div class="doc-analysis-label">${escapeHtml(t('categories', 'Categories'))}</div>
                        <div class="doc-analysis-chips">${chipList(categories, 'name')}</div>
                    </div>
                </section>
                <section class="doc-analysis-lane doc-analysis-lane--terms">
                    <div class="doc-analysis-lane__icon" aria-hidden="true"><i class="bi bi-tags"></i></div>
                    <div class="doc-analysis-lane__body">
                        <div class="doc-analysis-label">${escapeHtml(t('categoryTerms', 'Category terms'))}</div>
                        <div class="doc-analysis-chips">${chipList(terms, 'term')}</div>
                    </div>
                </section>
                <section class="doc-analysis-lane doc-analysis-lane--keywords">
                    <div class="doc-analysis-lane__icon" aria-hidden="true"><i class="bi bi-key"></i></div>
                    <div class="doc-analysis-lane__body">
                        <div class="doc-analysis-label">${escapeHtml(t('keywords', 'Keywords'))}</div>
                        <div class="doc-analysis-chips">${chipList(keywords, 'phrase')}</div>
                    </div>
                </section>
            </div>
        </div>
    `;
}

/**
 * Mount summary HTML into a container element.
 * @param {HTMLElement|null} container
 * @param {object} analysis
 * @param {object} [options]
 */
export function mountAnalysisSummary(container, analysis, options = {}) {
    if (!container) return;
    const html = options.file != null
        ? renderDocumentStatusSummary(analysis, options.file, options)
        : renderAnalysisSummary(analysis, options);
    container.innerHTML = html;
    container.hidden = false;
}

/**
 * Empty / loading helpers for the modal slot.
 */
export function setAnalysisSummaryLoading(container) {
    if (!container) return;
    container.hidden = false;
    container.innerHTML = `<div class="doc-analysis-summary loading">${escapeHtml(
        t('loadingAnalysis', 'Loading analysis...')
    )}</div>`;
}

export function setAnalysisSummaryError(container, message) {
    if (!container) return;
    container.hidden = false;
    container.innerHTML = `<div class="doc-analysis-summary error">${escapeHtml(
        message || t('errorLoadingAnalysis', 'Could not load analysis')
    )}</div>`;
}
