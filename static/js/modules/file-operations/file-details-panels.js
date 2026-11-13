/**
 * Shared File Details panels (metadata + analysis) for the FMAS modal and
 * the standalone /file/<id> page.
 */

import { escapeAttribute, escapeHtml, formatFileSize } from '../core/utils.js';
import { translations as defaultTranslations } from '../core/config.js';
import {
    fetchDocumentAnalysis,
    mountAnalysisSummary,
    setAnalysisSummaryError,
    setAnalysisSummaryLoading,
} from '../documents/document-analysis.js';
import { loadClassificationCharts } from '../charts/classification-charts.js';

function t(key, fallback, dict) {
    const tr = dict || defaultTranslations;
    return tr[key] || fallback;
}

/**
 * Format a stored file date for display in the viewer's local calendar.
 * A date-only value ("2026-10-13") must be built as a LOCAL date: passing it
 * straight to new Date() parses it as UTC midnight, which shows the previous
 * day in every timezone west of UTC (the list page showed 10/13 while the
 * detail page showed 10/12). Full timestamps are converted normally.
 */
export function formatFileDate(value, fallback = 'N/A') {
    if (!value) return fallback;
    const text = String(value);
    const dateOnly = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
    const date = dateOnly
        ? new Date(Number(dateOnly[1]), Number(dateOnly[2]) - 1, Number(dateOnly[3]))
        : new Date(text);
    return Number.isNaN(date.getTime()) ? text : date.toLocaleDateString();
}

/**
 * @param {object} file
 * @param {object} [tr]
 * @returns {string}
 */
export function renderLineageSection(file, tr = defaultTranslations) {
    const lineage = file && file.lineage;
    if (!lineage) return '';

    const ancestors = Array.isArray(lineage.ancestors) ? lineage.ancestors : [];
    const descendants = Array.isArray(lineage.descendants) ? lineage.descendants : [];
    if (!ancestors.length && !descendants.length) return '';

    const row = (item, depth, isChild) => {
        const id = Number(item.id);
        if (!Number.isInteger(id)) return '';
        const indent = isChild ? (depth - 1) * 12 : 0;
        const label = escapeHtml(String(item.name || `#${id}`));
        const labelAttr = escapeAttribute(String(item.name || `#${id}`));
        const arrow = isChild ? '\u21B3' : '\u2191';
        return `
            <div class="lineage-row" style="margin-left: ${indent}px;">
                <span class="lineage-arrow">${arrow}</span>
                <a href="#" class="lineage-link"
                   data-lineage-id="${id}"
                   data-lineage-name="${labelAttr}">${label}</a>
            </div>`;
    };

    let html = '<div class="lineage-section">';
    html += `<div class="lineage-title">${escapeHtml(t('lineage', 'Contained In / Contains', tr))}</div>`;

    if (ancestors.length) {
        html += `<div class="lineage-group">${escapeHtml(t('containedIn', 'Contained in', tr))}</div>`;
        ancestors.forEach((a) => { html += row(a, a.depth || 1, false); });
    } else {
        html += `<div class="lineage-group">${escapeHtml(t('topLevelObject', 'Top-level object', tr))}</div>`;
    }

    if (descendants.length) {
        html += `<div class="lineage-group">${escapeHtml(
            (t('contains', 'Contains', tr)) + ` (${descendants.length})`
        )}</div>`;
        descendants.forEach((d) => { html += row(d, d.depth || 1, true); });
    }

    if (Array.isArray(lineage.errors) && lineage.errors.length) {
        html += `<div class="lineage-warning">${escapeHtml(
            (t('lineageUnavailable', 'Lineage partially unavailable', tr)) +
            ': ' + lineage.errors.join(', ')
        )}</div>`;
    }

    html += '</div>';
    return html;
}

/**
 * @param {object} file
 * @param {object} [tr]
 * @returns {string}
 */
function metadataItemHtml(item, extraClass = '') {
    return `
            <div class="metadata-item ${extraClass}">
                <div class="metadata-label">${escapeHtml(item.label)}</div>
                <div class="metadata-value">${escapeHtml(String(item.value))}</div>
            </div>`;
}

function metadataBand(title, icon, bandClass, items, tr, extraBandClass = '') {
    if (!items.length) return '';
    const body = items.map((item) => metadataItemHtml(item, item.className || '')).join('');
    return `
        <section class="metadata-band ${bandClass} ${extraBandClass}" role="listitem">
            <header class="metadata-band__head">
                <i class="bi ${icon}" aria-hidden="true"></i>
                <span>${escapeHtml(title)}</span>
            </header>
            <div class="metadata-band__grid">${body}</div>
        </section>`;
}

/**
 * @param {object} file
 * @param {object} tr
 * @returns {{ title: string, icon: string, bandClass: string, items: object[] }[]}
 */
export function collectFileMetadataBands(file, tr = defaultTranslations) {
    if (!file) return [];

    const identity = [
        { label: t('fileName', 'File Name', tr), value: file.name || 'N/A' },
        { label: t('fileType', 'File Type', tr), value: file.type || 'Unknown' },
        { label: t('fileSize', 'File Size', tr), value: formatFileSize(file.size) },
        { label: t('status', 'Status', tr), value: file.status || 'Unknown', className: 'metadata-item--status' },
    ];
    if (file.id != null) {
        identity.push({ label: t('fileId', 'File ID', tr), value: String(file.id) });
    }

    const timeline = [
        {
            label: t('fileDate', 'File Date', tr),
            value: formatFileDate(file.file_date, 'N/A'),
        },
        {
            label: t('dateCreated', 'Date Created', tr),
            value: formatFileDate(file.date_creation, 'N/A'),
        },
    ];

    const context = [
        { label: t('source', 'Source', tr), value: file.source || 'Unknown' },
        { label: t('side', 'Side', tr), value: file.side || 'Unknown' },
    ];
    if (file.hierarchy_path) {
        context.push({
            label: t('hierarchyPath', 'Origin', tr),
            value: file.hierarchy_path,
        });
    }
    if (Array.isArray(file.geo_mentions) && file.geo_mentions.length) {
        const places = file.geo_mentions.slice(0, 4).map((g) => {
            const name = g.place_name || 'Place';
            const count = g.mention_count ? ` (${g.mention_count})` : '';
            return `${name}${count}`;
        });
        const extra = file.geo_mentions.length > 4
            ? ` +${file.geo_mentions.length - 4} ${t('morePlaces', 'more', tr)}`
            : '';
        context.push({
            label: t('geoMentions', 'Places Mentioned', tr),
            value: places.join('; ') + extra,
        });
    } else if (file.coordinates) {
        context.push({
            label: t('coordinates', 'Coordinates', tr),
            value: String(file.coordinates),
        });
    }

    const processing = [
        {
            label: t('wordCount', 'Word Count', tr),
            value: (file.word_count || 0).toLocaleString(),
        },
        {
            label: t('contentChunks', 'Content Chunks', tr),
            value: (file.content_chunks || 0).toLocaleString(),
        },
    ];
    if (file.processing && file.processing.status) {
        const p = file.processing;
        let statusValue = String(p.status).replace(/_/g, ' ');
        if (p.detail) statusValue += ` - ${p.detail}`;
        if (p.attempts) statusValue += ` (${p.attempts} attempts)`;
        processing.push({
            label: t('processingStatus', 'Processing Status', tr),
            value: statusValue,
            className: 'metadata-item--processing',
        });
    }
    const prov = file.extraction_provenance;
    if (prov && prov.ocr) {
        const ocrBits = [];
        if (prov.ocr.engine) ocrBits.push(prov.ocr.engine);
        if (prov.ocr.confidence != null) {
            ocrBits.push(`${Math.round(Number(prov.ocr.confidence) * 100)}% conf.`);
        }
        if (prov.ocr.derived) ocrBits.push('derived');
        processing.push({
            label: t('ocrProvenance', 'OCR Provenance', tr),
            value: ocrBits.join(' / ') || 'OCR',
        });
    }
    if (file.error_message) {
        processing.push({
            label: t('processingError', 'Processing Error', tr),
            value: file.error_message,
            className: 'metadata-item--alert',
        });
    }

    const taxonomy = [];
    if (Array.isArray(file.analyst_categories) && file.analyst_categories.length) {
        const names = file.analyst_categories
            .map((c) => (typeof c === 'string' ? c : c.name))
            .filter(Boolean)
            .join(', ');
        if (names) {
            taxonomy.push({
                label: t('analystCategories', 'Analyst Categories', tr),
                value: names,
                className: 'metadata-item--analyst',
            });
        }
    }
    if (Array.isArray(file.categories) && file.categories.length) {
        const smart = file.categories
            .map((c) => c.name || c)
            .filter(Boolean)
            .slice(0, 8)
            .join(', ');
        if (smart) {
            taxonomy.push({
                label: t('smartCategories', 'Smart Categories', tr),
                value: smart + (file.categories.length > 8 ? '…' : ''),
                className: 'metadata-item--smart',
            });
        }
    }

    const integrity = [];
    if (file.path) {
        integrity.push({ label: t('filePath', 'File Path', tr), value: file.path, className: 'metadata-item--wide' });
    }
    if (file.hash) {
        integrity.push({ label: t('hash', 'Content Hash', tr), value: file.hash, className: 'metadata-item--wide' });
    }

    return [
        { title: t('metadataIdentity', 'Identity', tr), icon: 'bi-fingerprint', bandClass: 'metadata-band--identity', items: identity },
        { title: t('metadataTimeline', 'Timeline', tr), icon: 'bi-calendar3', bandClass: 'metadata-band--timeline', items: timeline },
        { title: t('metadataContext', 'Context', tr), icon: 'bi-signpost-2', bandClass: 'metadata-band--context', items: context },
        { title: t('metadataProcessing', 'Processing', tr), icon: 'bi-cpu', bandClass: 'metadata-band--processing', items: processing },
        { title: t('metadataTaxonomy', 'Taxonomy', tr), icon: 'bi-bookmarks', bandClass: 'metadata-band--taxonomy', items: taxonomy },
        { title: t('metadataIntegrity', 'Integrity', tr), icon: 'bi-shield-check', bandClass: 'metadata-band--integrity', items: integrity },
    ].filter((band) => band.items.length);
}

/**
 * FMAS modal: grouped label/value rows — compact footer, not the full-page ribbon.
 * @param {object} file
 * @param {object} [tr]
 * @returns {string}
 */
export function buildFileMetadataCompactHtml(file, tr = defaultTranslations) {
    const bands = collectFileMetadataBands(file, tr);
    if (!bands.length && !file?.lineage) return '';

    let html = '<div class="metadata-compact-grid" role="region">';
    bands.forEach((band) => {
        const cells = band.items.map((item) => metadataItemHtml(item, item.className || '')).join('');
        html += `
            <div class="metadata-compact-row ${band.bandClass}" role="group" aria-label="${escapeAttribute(band.title)}">
                <div class="metadata-compact-row__label">
                    <i class="bi ${band.icon}" aria-hidden="true"></i>
                    <span>${escapeHtml(band.title)}</span>
                </div>
                <div class="metadata-compact-row__cells">${cells}</div>
            </div>`;
    });

    const lineage = renderLineageSection(file, tr);
    if (lineage) {
        html += `
            <div class="metadata-compact-row metadata-compact-row--lineage" role="group"
                 aria-label="${escapeAttribute(t('lineage', 'Contained In / Contains', tr))}">
                <div class="metadata-compact-row__label">
                    <i class="bi bi-diagram-2" aria-hidden="true"></i>
                    <span>${escapeHtml(t('lineage', 'Contained In / Contains', tr))}</span>
                </div>
                <div class="metadata-compact-row__cells metadata-compact-row__cells--lineage">${lineage}</div>
            </div>`;
    }
    html += '</div>';
    return html;
}

/**
 * At-a-glance metrics above the metadata band carousel (file detail page).
 * @param {object} file
 * @param {object} [tr]
 * @returns {string}
 */
function buildMetadataSpotlight(file, tr = defaultTranslations) {
    if (!file) return '';

    const name = file.name || t('unknownFile', 'Unknown file', tr);
    const shortName = name.length > 48 ? `${name.slice(0, 45)}…` : name;
    const status = file.status || '—';
    const statusLower = String(status).toLowerCase();
    let statusTone = 'neutral';
    if (statusLower.includes('fail') || statusLower.includes('error')) statusTone = 'danger';
    else if (statusLower.includes('read') || statusLower.includes('done') || statusLower.includes('process')) {
        statusTone = 'success';
    }

    const wordCount = (file.word_count || 0).toLocaleString();
    const fileType = file.type || '—';
    const fileDate = formatFileDate(file.file_date, '—');
    const size = formatFileSize(file.size);

    return `
        <div class="metadata-spotlight" aria-label="${escapeHtml(t('recordSnapshot', 'Record snapshot', tr))}">
            <div class="metadata-spotlight__lead">
                <span class="metadata-spotlight__kicker">${escapeHtml(t('recordSnapshot', 'Record snapshot', tr))}</span>
                <strong class="metadata-spotlight__name" title="${escapeHtml(name)}">${escapeHtml(shortName)}</strong>
            </div>
            <div class="metadata-spotlight__metrics">
                <span class="metadata-spotlight__pill metadata-spotlight__pill--${statusTone}">
                    <i class="bi bi-activity" aria-hidden="true"></i>
                    ${escapeHtml(status)}
                </span>
                <span class="metadata-spotlight__pill">
                    <i class="bi bi-file-earmark" aria-hidden="true"></i>
                    ${escapeHtml(fileType)}
                </span>
                <span class="metadata-spotlight__pill">
                    <i class="bi bi-hdd" aria-hidden="true"></i>
                    ${escapeHtml(size)}
                </span>
                <span class="metadata-spotlight__pill">
                    <i class="bi bi-fonts" aria-hidden="true"></i>
                    ${escapeHtml(wordCount)} ${escapeHtml(t('words', 'words', tr))}
                </span>
                <span class="metadata-spotlight__pill">
                    <i class="bi bi-calendar3" aria-hidden="true"></i>
                    ${escapeHtml(fileDate)}
                </span>
            </div>
        </div>
        <p class="metadata-deck__hint">
            <i class="bi bi-arrow-left-right" aria-hidden="true"></i>
            ${escapeHtml(t('metadataScrollHint', 'Scroll the bands below for full identity, context, and lineage', tr))}
        </p>`;
}

export function buildFileMetadataHtml(file, tr = defaultTranslations, options = {}) {
    if (!file) return '';
    if (options.layout === 'compact') {
        return buildFileMetadataCompactHtml(file, tr);
    }

    const bands = collectFileMetadataBands(file, tr);

    let metadataHtml = `<div class="metadata-deck">${buildMetadataSpotlight(file, tr)}`;
    metadataHtml += '<div class="metadata-ribbon-bands" role="list">';
    bands.forEach((band) => {
        metadataHtml += metadataBand(band.title, band.icon, band.bandClass, band.items, tr);
    });
    metadataHtml += '</div>';

    const lineage = renderLineageSection(file, tr);
    if (lineage) {
        metadataHtml += `<section class="metadata-band metadata-band--lineage"><header class="metadata-band__head"><i class="bi bi-diagram-2" aria-hidden="true"></i><span>${escapeHtml(t('lineage', 'Contained In / Contains', tr))}</span></header><div class="metadata-band__lineage">${lineage}</div></section>`;
    }
    metadataHtml += '</div>';

    return metadataHtml;
}

/**
 * @param {HTMLElement|null} section
 * @param {object} file
 * @param {{ translations?: object, layout?: 'ribbon'|'compact', onLineageClick?: (id: number, name: string) => void }} [options]
 */
export function mountFileMetadataSection(section, file, options = {}) {
    if (!section) return;
    const tr = options.translations || defaultTranslations;
    const layout = options.layout || (section.classList.contains('metadata-compact-layout') ? 'compact' : 'ribbon');
    const html = buildFileMetadataHtml(file, tr, { layout });
    section.innerHTML = html || `<div class="empty-state">${escapeHtml(
        t('noMetadataAvailable', 'No metadata available', tr)
    )}</div>`;

    const onLineage = options.onLineageClick;
    if (typeof onLineage !== 'function') return;

    section.querySelectorAll('.lineage-link').forEach((link) => {
        link.addEventListener('click', (event) => {
            event.preventDefault();
            const id = parseInt(link.getAttribute('data-lineage-id'), 10);
            const name = link.getAttribute('data-lineage-name') || 'File';
            if (Number.isInteger(id)) onLineage(id, name);
        });
    });
}

/**
 * @param {HTMLElement|null} analysisSection
 * @param {object} file
 * @param {{ translations?: object, chartContainerId?: string }} [options]
 */
export function mountFileAnalysisSection(analysisSection, file, options = {}) {
    if (!analysisSection || !file) return;
    const tr = options.translations || defaultTranslations;

    if (options.loadCharts) {
        if (window.fms?.charts?.loadClassificationCharts) {
            window.fms.charts.loadClassificationCharts(file.id);
        } else if (window.loadClassificationCharts) {
            window.loadClassificationCharts(file.id);
        } else {
            loadClassificationCharts(file.id);
        }
        return;
    }

    let analysisHtml = '';

    if (file.title) {
        analysisHtml += `
            <div class="analysis-section-title">
                <h4><i class="bi bi-file-text me-2"></i>${escapeHtml(t('titleAnalysis', 'Title Analysis', tr))}</h4>
            </div>
            <div class="analysis-item">
                <div class="analysis-label">${escapeHtml(t('fileTitle', 'File Title', tr))}:</div>
                <div class="analysis-value">${escapeHtml(file.title)}</div>
            </div>`;

        if (file.similar_titles && file.similar_titles.length > 0) {
            analysisHtml += `
                <div class="analysis-item">
                    <div class="analysis-label">${escapeHtml(t('similarTitles', 'Similar Titles', tr))}:</div>
                    <div class="similar-titles-list">`;
            file.similar_titles.forEach((similar) => {
                analysisHtml += `
                    <div class="similar-title-item">
                        <span class="similarity-badge">${similar.similarity_percent || 0}%</span>
                        <span class="similar-title-text">${escapeHtml(similar.name || 'Unknown')}</span>
                        ${similar.file_count ? `<span class="file-count-badge">${similar.file_count} files</span>` : ''}
                    </div>`;
            });
            analysisHtml += '</div></div>';
        }
        analysisHtml += '<hr style="margin: 1rem 0; border-color: #e2e8f0;">';
    }

    analysisSection.innerHTML = analysisHtml;
}

/**
 * @param {HTMLElement|null} analysisSummary
 * @param {number} fileId
 * @param {{ translations?: object, onAnalysis?: (analysis: object|null) => void }} [options]
 */
export function mountFileAnalysisSummary(analysisSummary, fileId, options = {}) {
    if (!analysisSummary || !fileId) return;
    const tr = options.translations || defaultTranslations;
    analysisSummary.hidden = false;
    setAnalysisSummaryLoading(analysisSummary);
    fetchDocumentAnalysis(fileId)
        .then((analysis) => {
            if (options.onAnalysis) options.onAnalysis(analysis);
            mountAnalysisSummary(analysisSummary, analysis, {
                title: t('documentStatus', 'Document status', tr),
                file: options.file || null,
                panelSection: analysisSummary.closest('[data-panel-id="modal-status"]') != null,
            });
        })
        .catch((err) => {
            if (options.onAnalysis) options.onAnalysis(null);
            if (options.file) {
                mountAnalysisSummary(analysisSummary, null, {
                    title: t('documentStatus', 'Document status', tr),
                    file: options.file,
                    panelSection: analysisSummary.closest('[data-panel-id="modal-status"]') != null,
                });
                const warn = analysisSummary.querySelector('[data-doc-status]');
                if (warn) {
                    const note = document.createElement('p');
                    note.className = 'doc-status-fetch-note';
                    note.textContent = err.message || t('errorLoadingAnalysis', 'Could not load analysis', tr);
                    warn.appendChild(note);
                }
            } else {
                setAnalysisSummaryError(
                    analysisSummary,
                    err.message || t('errorLoadingAnalysis', 'Could not load analysis', tr)
                );
            }
        });
}

/**
 * @param {number} fileId
 * @returns {Promise<object>}
 */
export async function fetchFileDetailsPayload(fileId) {
    const response = await fetch(`/api/file/${fileId}/details`);
    if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
    }
    const data = await response.json();
    if (!data.success || !data.details) {
        throw new Error(data.error || 'Failed to load file details');
    }
    return data.details;
}

/**
 * Apply modal-style panels to one or more DOM targets.
 *
 * @param {object} options
 * @param {object} options.file
 * @param {object} [options.translations]
 * @param {HTMLElement|HTMLElement[]|null} [options.metadataSections]
 * @param {HTMLElement|null} [options.analysisSection]
 * @param {HTMLElement|null} [options.analysisSummary]
 * @param {(id: number, name: string) => void} [options.onLineageClick]
 * @param {string} [options.chartContainerId]
 * @param {(analysis: object|null) => void} [options.onAnalysis]
 */
export function applyFileDetailsPanels(options) {
    const {
        file,
        translations: tr,
        metadataSections,
        analysisSection,
        analysisSummary,
        onLineageClick,
        chartContainerId,
        onAnalysis,
    } = options;

    if (!file) return;

    const sections = metadataSections
        ? (Array.isArray(metadataSections) ? metadataSections : [metadataSections])
        : [];
    sections.filter(Boolean).forEach((el) => {
        mountFileMetadataSection(el, file, { translations: tr, onLineageClick });
    });

    if (analysisSummary) {
        mountFileAnalysisSummary(analysisSummary, file.id, { translations: tr, onAnalysis, file });
    }
    if (analysisSection) {
        mountFileAnalysisSection(analysisSection, file, { translations: tr, chartContainerId });
    }
}

/**
 * Standalone /file/<id> page: fetch and render rich panels like the FMAS modal.
 */
export async function hydrateStandaloneFileDetailPage({
    fileId,
    translations: tr,
    metadataSectionIds = ['pageFileMetadataSection'],
    analysisSummaryId = 'pageFileAnalysisSummary',
    analysisSectionId = 'pageFileAnalysisSection',
    onLineageClick,
}) {
    const file = await fetchFileDetailsPayload(fileId);
    const metadataSections = metadataSectionIds
        .map((id) => document.getElementById(id))
        .filter(Boolean);

    applyFileDetailsPanels({
        file,
        translations: tr,
        metadataSections,
        analysisSection: document.getElementById(analysisSectionId),
        analysisSummary: document.getElementById(analysisSummaryId),
        onLineageClick,
    });

    const analysisWords = document.getElementById('analysisTabWordCount');
    if (analysisWords && file.word_count != null) {
        analysisWords.textContent = Number(file.word_count).toLocaleString();
    }

    return file;
}
