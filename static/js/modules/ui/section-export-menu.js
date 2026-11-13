/**
 * Export dropdown for FMAS section views (not unified-table).
 * Mirrors templates/components/table.html export_menu for use in JS-rendered headers.
 */

import { escapeAttribute, escapeHtml } from '../core/utils.js';
import { translations } from '../core/config.js';

const GEOLOCATION_COLUMNS = [
    { key: 'coordinates', labelKey: 'exportColCoordinates', fallback: 'Coordinates' },
    { key: 'latitude', labelKey: 'exportColLatitude', fallback: 'Latitude' },
    { key: 'longitude', labelKey: 'exportColLongitude', fallback: 'Longitude' },
    { key: 'name', labelKey: 'exportColFileName', fallback: 'File Name' },
    { key: 'type', labelKey: 'exportColFileType', fallback: 'Type' },
    { key: 'path', labelKey: 'exportColFilePath', fallback: 'Path' },
];

function label(entry) {
    return translations[entry.labelKey] || entry.fallback;
}

/**
 * @param {string} exportParams - query string (search, sort) without leading ?
 */
export function renderGeolocationMapLink() {
    const label = translations.openMapViewer || 'Map';
    const title = translations.openMapViewerTitle || 'Open coordinates in the Map Viewer';
    const href = '/analytics/map-viewer';
    return `<a href="${href}" class="btn btn-sm btn-primary map-viewer-link" data-swap="off" title="${escapeAttribute(title)}" aria-label="${escapeAttribute(title)}">
        <i class="bi bi-map" aria-hidden="true"></i>
        <span>${escapeHtml(label)}</span>
    </a>`;
}

export function renderGeolocationExportMenu(exportParams = '') {
    const domId = 'geolocationExportMenu';
    const paramsAttr = escapeAttribute(exportParams || '');
    const exportLabel = escapeHtml(translations.export || 'Export');
    const columnsHeading = escapeHtml(translations.exportColumns || 'Columns');
    const selectAll = escapeHtml(translations.selectAll || 'Select all');
    const selectNone = escapeHtml(translations.selectNone || 'Select none');
    const scopeHeading = escapeHtml(translations.exportScope || 'Scope');
    const scopeView = escapeHtml(translations.exportScopeView || 'Filtered data (current view)');
    const scopeAll = escapeHtml(translations.exportScopeAll || 'All data (entire database)');
    const csvLabel = escapeHtml(translations.exportAsCsv || 'Export as CSV');
    const xlsxLabel = escapeHtml(translations.exportAsExcel || 'Export as Excel');
    const printLabel = escapeHtml(translations.print || 'Print');
    const columnsAria = escapeHtml(translations.columnsToExport || 'Columns to export');

    const columnChecks = GEOLOCATION_COLUMNS.map((col) => `<label class="dropdown-item-text ut-export-column">
                <input type="checkbox" class="form-check-input ut-export-column-check"
                       data-export-column="${escapeAttribute(col.key)}" checked>
                ${escapeHtml(label(col))}
            </label>`).join('');

    return `<div class="dropdown ut-export" id="${domId}"
     data-export-interface="geolocation"
     data-export-path="/api/archives/geolocation/export"
     data-export-params="${paramsAttr}">
    <button type="button" class="btn btn-sm btn-outline-secondary ut-export-btn"
            data-bs-toggle="dropdown" data-bs-auto-close="outside" aria-expanded="false"
            aria-label="${exportLabel}">
        <i class="bi bi-download" aria-hidden="true"></i>
        <span class="ut-export-label">${exportLabel}</span>
    </button>
    <div class="dropdown-menu dropdown-menu-end ut-export-menu">
        <div class="ut-export-columns" role="group" aria-label="${columnsAria}">
            <span class="dropdown-header ut-export-heading">${columnsHeading}</span>
            <div class="ut-export-column-actions px-3 pb-1">
                <button type="button" class="btn btn-link btn-sm p-0 me-2" data-export-columns-all>${selectAll}</button>
                <button type="button" class="btn btn-link btn-sm p-0" data-export-columns-none>${selectNone}</button>
            </div>
            ${columnChecks}
        </div>
        <div class="dropdown-divider"></div>
        <div class="ut-export-scope" role="radiogroup" aria-label="${scopeHeading}">
            <span class="dropdown-header ut-export-heading">${scopeHeading}</span>
            <label class="dropdown-item-text ut-export-scope-option">
                <input type="radio" class="form-check-input ut-export-scope-check"
                       name="${domId}-scope" value="view" checked>
                ${scopeView}
            </label>
            <label class="dropdown-item-text ut-export-scope-option">
                <input type="radio" class="form-check-input ut-export-scope-check"
                       name="${domId}-scope" value="all">
                ${scopeAll}
            </label>
        </div>
        <div class="dropdown-divider"></div>
        <button type="button" class="dropdown-item" data-export-download="csv">
            <i class="bi bi-filetype-csv me-2" aria-hidden="true"></i>${csvLabel}
        </button>
        <button type="button" class="dropdown-item" data-export-download="xlsx">
            <i class="bi bi-file-earmark-spreadsheet me-2" aria-hidden="true"></i>${xlsxLabel}
        </button>
        <div class="dropdown-divider"></div>
        <button type="button" class="dropdown-item" data-export-print>
            <i class="bi bi-printer me-2" aria-hidden="true"></i>${printLabel}
        </button>
    </div>
</div>`;
}

/** Build query string for geolocation export from section list API params. */
export function geolocationExportParamsFromApi(params) {
    const q = new URLSearchParams();
    if (params.search) q.set('search', params.search);
    if (params.sort_by) q.set('sort_by', params.sort_by);
    if (params.sort_dir) q.set('sort_dir', params.sort_dir);
    return q.toString();
}
