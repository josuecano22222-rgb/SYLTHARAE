/**
 * Shared operations for any interface that lists selectable files:
 * multi-type reading helpers, batch/folder export, rename-on-export,
 * open in native app, locate folder, and name/details spreadsheet export.
 *
 * Used by File Manager (FMAS), Files List, and related selectable grids.
 * Search Advanced keeps its own page wiring but the same server contracts.
 */

import { getCSRFToken } from '../core/utils.js';
import {
    chooseExportDestination,
    ensureExportExtension,
    saveExportBlob,
} from '../core/export-download.js';
import { collectSelectedIds } from './bulk-analyst-actions.js';

const DETAILS_COLUMNS = [
    { key: 'id', label: 'Id' },
    { key: 'name', label: 'File Name' },
    { key: 'path', label: 'File Path' },
    { key: 'type', label: 'File Type' },
    { key: 'category', label: 'Category' },
    { key: 'category_terms', label: 'Category Terms' },
    { key: 'keywords', label: 'Keywords' },
    { key: 'size', label: 'Size' },
    { key: 'source', label: 'Source' },
    { key: 'side', label: 'Side' },
    { key: 'status', label: 'Status' },
    { key: 'date', label: 'Date' },
    { key: 'created', label: 'Created' },
];

function t(key, fallback) {
    return (window.translations && window.translations[key]) || fallback;
}

function notify(kind, message) {
    const map = {
        success: window.showSuccess,
        error: window.showError,
        warning: window.showWarning,
        info: window.showInfo,
    };
    const fn = map[kind];
    if (typeof fn === 'function') fn(message);
    else if (kind === 'error') window.alert(message);
}

function csrf() {
    return getCSRFToken() ||
        document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
}

function desktopBridge() {
    return window.inforaxisDesktop || window.INFORAXIS_DESKTOP || null;
}

function safeAppUrl(value) {
    try {
        const parsed = new URL(String(value || ''), window.location.origin);
        return parsed.origin === window.location.origin
            ? `${parsed.pathname}${parsed.search}${parsed.hash}`
            : null;
    } catch (_error) {
        return null;
    }
}

function safeClientFilename(value, fallback) {
    let name = String(value || '').split(/[\/\\]/).pop()
        .replace(/[\u0000-\u001f<>:"|?*\\]/g, '_')
        .replace(/^\.+$/, '')
        .trim();
    if (!name || name === '.' || name === '..') name = fallback;
    return name.slice(0, 160) || fallback;
}

/** Selected values from a single- or multi-select (empty "All" skipped). */
export function selectedTypeValues(selectEl) {
    if (!selectEl) return [];
    if (selectEl.multiple) {
        return Array.from(selectEl.selectedOptions)
            .map((opt) => String(opt.value || '').trim())
            .filter(Boolean);
    }
    const value = String(selectEl.value || '').trim();
    return value ? [value] : [];
}

/** Comma-joined types for URL/API params that accept a set. */
export function selectedTypesParam(selectEl) {
    return selectedTypeValues(selectEl).join(',');
}

/** Restore multi-select from a comma string or array of values. */
export function setSelectedTypes(selectEl, values) {
    if (!selectEl) return;
    const wanted = new Set(
        (Array.isArray(values) ? values : String(values || '').split(','))
            .map((v) => String(v).trim())
            .filter(Boolean)
    );
    Array.from(selectEl.options).forEach((opt) => {
        if (!opt.value) {
            opt.selected = wanted.size === 0;
            return;
        }
        opt.selected = wanted.has(opt.value);
    });
}

function selectedIdsOrWarn() {
    const ids = collectSelectedIds();
    if (!ids.length) {
        notify('warning', t('pleaseSelectFilesToExport', 'Please select files to export'));
        return null;
    }
    return ids;
}

async function askFilename(promptText, suggested) {
    // alert-replacement.js replaces window.prompt with a Promise-based modal.
    const answer = await window.prompt(promptText, suggested);
    if (answer === null) return null;
    return String(answer).trim();
}

/**
 * POST an export and save the answer. Resolves true when a file was saved,
 * false when the reader dismissed the save picker, so callers can avoid
 * announcing success for an export that never reached the disk.
 */
async function postDownload(url, body, suggestedName) {
    let destination = null;
    try {
        destination = await chooseExportDestination(suggestedName);
        if (destination === false) return false;
    } catch (error) {
        console.warn('Save location picker unavailable:', error);
    }
    const response = await fetch(url, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrf(),
            Accept: '*/*',
        },
        body: JSON.stringify(body),
    });
    if (!response.ok) {
        const problem = await response.json().catch(() => ({}));
        throw new Error(problem.error || `HTTP ${response.status}`);
    }
    const blob = await response.blob();
    const disposition = response.headers.get('Content-Disposition') || '';
    const match = disposition.match(/filename\*?=(?:UTF-8''|")?([^";]+)/i);
    let filename = suggestedName;
    if (match) {
        try { filename = decodeURIComponent(match[1].replace(/"/g, '')); }
        catch (_e) { filename = match[1].replace(/"/g, ''); }
    }
    return (await saveExportBlob(blob, filename, destination)) !== false;
}

/** Export selected originals/text as ZIP with a chosen name and destination. */
export async function exportSelectionZip(mode = 'text') {
    const ids = selectedIdsOrWarn();
    if (!ids) return;
    const kind = mode === 'originals' ? 'originals' : 'text';
    const suggested = kind === 'originals' ? 'original_files' : 'extracted_content';
    const name = await askFilename(
        t('selectedExportPrompt', 'Name this selected-file export (leave blank for an automatic name):'),
        `${suggested}_${new Date().toISOString().slice(0, 10)}`
    );
    if (name === null) return;
    try {
        const saved = await postDownload('/files/export', {
            file_ids: ids,
            mode: kind,
            filename: name,
        }, ensureExportExtension(name || suggested, 'zip', suggested));
        if (saved === false) return;  // the reader dismissed the save dialog
        notify('success', t('selectedExportReady', 'Selected-file export ready.'));
    } catch (error) {
        notify('error', (t('selectedExportFailed', 'Selected-file export failed.') + ' ' + error.message).trim());
    }
}

/** Write selected files into an existing folder or a new subfolder. */
export async function exportSelectionToFolder(mode = 'text') {
    const ids = selectedIdsOrWarn();
    if (!ids) return;
    if (typeof window.showDirectoryPicker !== 'function') {
        notify('info', t('folderPickerUnsupported',
            'Folder export is not supported in this browser; downloading a ZIP instead.'));
        return exportSelectionZip(mode);
    }

    let parent;
    try {
        parent = await window.showDirectoryPicker({ mode: 'readwrite' });
    } catch (error) {
        if (error?.name !== 'AbortError') {
            notify('error', t('folderPickerFailed', 'Could not write to the selected folder.'));
        }
        return;
    }

    const folderName = await askFilename(
        t('folderExportSubfolderPrompt',
            'Optional: enter a new subfolder name, or leave blank to use the selected folder.'),
        ''
    );
    if (folderName === null) return;

    let targetDirectory = parent;
    const cleaned = String(folderName).trim()
        .replace(/[\u0000-\u001f<>:"\/\\|?*]/g, '_')
        .replace(/^\.+$/, '').slice(0, 80);
    if (cleaned) {
        try {
            targetDirectory = await parent.getDirectoryHandle(cleaned, { create: true });
        } catch (error) {
            notify('error', t('folderPickerFailed', 'Could not write to the selected folder.') + ` ${error.message}`);
            return;
        }
    }

    notify('info', t('folderExportProgress', 'Writing selected documents to the chosen folder…'));
    const usedNames = new Set();
    const failures = [];
    let written = 0;

    for (const fileId of ids) {
        try {
            const descriptorResponse = await fetch(`/api/file/${fileId}/original`, {
                credentials: 'same-origin',
            });
            const descriptorData = await descriptorResponse.json();
            if (!descriptorResponse.ok || !descriptorData.success) {
                throw new Error(descriptorData.error || `HTTP ${descriptorResponse.status}`);
            }
            const original = descriptorData.original || {};
            let url;
            let filename;
            if (mode === 'originals') {
                if (!original.available) throw new Error(original.message || 'Original file unavailable');
                url = safeAppUrl(original.download_url);
                filename = safeClientFilename(original.name, `file_${fileId}`);
            } else {
                url = `/api/files/${fileId}/export`;
                const sourceName = safeClientFilename(original.name, `file_${fileId}`);
                const stem = sourceName.replace(/\.[^.]+$/, '') || `file_${fileId}`;
                filename = `${stem}_extracted.txt`;
            }
            if (!url) throw new Error('Invalid download URL');

            const fileResponse = await fetch(url, { credentials: 'same-origin' });
            if (!fileResponse.ok) {
                const problem = await fileResponse.json().catch(() => ({}));
                throw new Error(problem.error || `HTTP ${fileResponse.status}`);
            }
            const dot = filename.lastIndexOf('.');
            const stem = dot > 0 ? filename.slice(0, dot) : filename;
            const extension = dot > 0 ? filename.slice(dot) : '';
            let uniqueName = filename;
            let suffix = 2;
            while (usedNames.has(uniqueName.toLocaleLowerCase())) {
                uniqueName = `${stem}_${suffix++}${extension}`;
            }
            usedNames.add(uniqueName.toLocaleLowerCase());

            const handle = await targetDirectory.getFileHandle(uniqueName, { create: true });
            const writable = await handle.createWritable();
            await writable.write(await fileResponse.blob());
            await writable.close();
            written += 1;
        } catch (error) {
            failures.push({ fileId, message: error.message });
        }
    }

    if (failures.length) {
        notify('warning',
            `${t('folderExportPartial', 'Some documents could not be saved to the chosen folder.')} (${written}/${ids.length})`);
    } else {
        notify('success',
            `${t('folderExportDone', 'Documents saved to the chosen folder.')} (${written})`);
    }
}

/** Export one file (original) and let the user rename it. */
export async function exportFileWithRename(fileId, suggestedName = '') {
    const id = parseInt(fileId, 10);
    if (!Number.isInteger(id) || id <= 0) return;
    const infoResp = await fetch(`/api/file/${id}/original`, { credentials: 'same-origin' });
    const infoData = await infoResp.json().catch(() => ({}));
    const original = (infoData && infoData.original) || {};
    const defaultName = suggestedName || original.name || `file_${id}`;
    const name = await askFilename(
        t('saveAs', 'Save As'),
        defaultName
    );
    if (name === null) return;
    const filename = safeClientFilename(name || defaultName, `file_${id}`);
    let destination = null;
    try {
        destination = await chooseExportDestination(filename);
        if (destination === false) return;
    } catch (_error) { /* fall through */ }

    const url = `/api/file/${id}/original/content?download=1&filename=${encodeURIComponent(filename)}`;
    const response = await fetch(url, { credentials: 'same-origin' });
    if (!response.ok) {
        const problem = await response.json().catch(() => ({}));
        notify('error', problem.error || t('exportFailed', 'Export failed'));
        return;
    }
    await saveExportBlob(await response.blob(), filename, destination);
    notify('success', t('exportReady', 'Export ready.'));
}

/** Open the original in the native desktop app when the companion is present. */
export async function openInNativeApp(fileId) {
    const id = parseInt(fileId, 10);
    if (!Number.isInteger(id) || id <= 0) return;
    const bridge = desktopBridge();
    const openDocument = bridge?.openDocumentForEdit || bridge?.openDocument;
    const infoResp = await fetch(`/api/file/${id}/original`, { credentials: 'same-origin' });
    const infoData = await infoResp.json().catch(() => ({}));
    const original = (infoData && infoData.original) || {};
    if (!original.available || typeof openDocument !== 'function') {
        // Fall back: download so the OS can open it.
        notify('info', t('desktopBridgeMissing',
            'Opening or editing a native file requires the trusted local SYLTHARAE companion. Downloading instead.'));
        window.location.href = `/api/file/${id}/original/content?download=1`;
        return;
    }
    try {
        await openDocument.call(bridge, {
            fileId: id,
            fileName: original.name,
            extension: original.extension,
            mimeType: original.mime_type,
            downloadUrl: safeAppUrl(original.download_url),
            edit: typeof bridge.openDocumentForEdit === 'function',
        });
    } catch (error) {
        notify('error', t('desktopBridgeFailed', 'The local SYLTHARAE companion could not complete this action.') + ` ${error.message}`);
    }
}

/** Reveal the file's folder (desktop companion) or copy/show the recorded path. */
export async function locateFile(fileId) {
    const id = parseInt(fileId, 10);
    if (!Number.isInteger(id) || id <= 0) return;
    const bridge = desktopBridge();
    if (bridge && typeof bridge.openContainingFolder === 'function') {
        try {
            const infoResp = await fetch(`/api/file/${id}/original`, { credentials: 'same-origin' });
            const infoData = await infoResp.json().catch(() => ({}));
            const original = (infoData && infoData.original) || {};
            await bridge.openContainingFolder({ fileId: id, fileName: original.name });
            return;
        } catch (error) {
            console.warn('Desktop locate failed, falling back to API:', error);
        }
    }
    try {
        const response = await fetch(`/api/file/${id}/locate`, { credentials: 'same-origin' });
        const json = await response.json();
        if (!json.success) {
            notify('error', json.error || t('locateFailed', 'The file location is not known.'));
            return;
        }
        if (json.revealed) {
            notify('success', t('folderRevealed', 'The folder is open in your file manager.'));
            return;
        }
        if (navigator.clipboard && json.path) {
            await navigator.clipboard.writeText(json.path);
            notify('info', `${json.path} ${t('pathCopied', 'Path copied.')}`);
        } else {
            notify('info', json.path || t('locateFailed', 'The file location is not known.'));
        }
    } catch (_error) {
        notify('error', t('locateFailed', 'The file location is not known.'));
    }
}

/** Export filenames (selected or all indexed) to CSV/Excel with a chosen name. */
export async function exportFileNames({
    scope = 'selected',
    format = 'csv',
    fileIds = null,
    suggestedName = null,
} = {}) {
    let ids = fileIds;
    if (scope === 'selected') {
        ids = ids || selectedIdsOrWarn();
        if (!ids) return;
    }
    const extension = format === 'excel' || format === 'xlsx' ? 'xlsx' : 'csv';
    const suggested = suggestedName
        || `${scope === 'all' ? 'all_indexed' : 'selected'}_filenames_${new Date().toISOString().slice(0, 10)}`;
    // No name prompt: the button is a one-click export and the filename is
    // already descriptive (scope + date). Ctrl/⌘-click asks for a name first,
    // for the reader who wants one. A prompt in the middle of a toolbar action
    // was the difference between "export" and "nothing happened".
    const name = '';
    try {
        const body = {
            scope,
            format: extension === 'xlsx' ? 'excel' : 'csv',
            filename: name,
        };
        if (scope === 'selected') body.file_ids = ids;
        const saved = await postDownload('/api/files/names/export', body,
            ensureExportExtension(name || suggested, extension, suggested));
        if (saved === false) return;  // the reader dismissed the save dialog
        notify('success', t('selectedNamesReady', 'Filename export ready.'));
    } catch (error) {
        notify('error', error.message || t('exportFailed', 'Export failed'));
    }
}

function ensureDetailsDialog() {
    let dialog = document.getElementById('fmasDetailsExportDialog');
    if (dialog) return dialog;
    dialog = document.createElement('div');
    dialog.id = 'fmasDetailsExportDialog';
    dialog.className = 'fmas-ops-dialog';
    dialog.hidden = true;
    dialog.setAttribute('role', 'dialog');
    dialog.setAttribute('aria-modal', 'true');
    dialog.innerHTML = `
        <div class="fmas-ops-dialog-backdrop" data-ops-dismiss></div>
        <div class="fmas-ops-dialog-panel">
            <header class="fmas-ops-dialog-header">
                <h2>${t('exportDetails', 'Export file details')}</h2>
                <button type="button" class="fmas-ops-dialog-close" data-ops-dismiss aria-label="${t('close', 'Close')}">&times;</button>
            </header>
            <form id="fmasDetailsExportForm" class="fmas-ops-dialog-form">
                <fieldset class="fmas-ops-columns">
                    <legend>${t('chooseColumns', 'Columns to include')}</legend>
                    <div class="fmas-ops-column-actions">
                        <button type="button" class="action-btn" data-ops-columns="all">${t('selectAll', 'Select All')}</button>
                        <button type="button" class="action-btn" data-ops-columns="none">${t('deselectAll', 'Select None')}</button>
                    </div>
                    <div class="fmas-ops-column-list" id="fmasDetailsColumns"></div>
                </fieldset>
                <div class="fmas-ops-field">
                    <label for="fmasDetailsFormat">${t('format', 'Format')}</label>
                    <select id="fmasDetailsFormat" class="form-select">
                        <option value="csv">CSV</option>
                        <option value="excel">Excel (XLSX)</option>
                        <option value="json">JSON (one document, keys per column)</option>
                    </select>
                </div>
                <div class="fmas-ops-field">
                    <label for="fmasDetailsScope">${t('exportScope', 'Scope')}</label>
                    <select id="fmasDetailsScope" class="form-select">
                        <option value="selected">${t('selectedFiles', 'Selected files')}</option>
                        <option value="all">${t('allIndexedFiles', 'All uploaded / indexed files')}</option>
                    </select>
                </div>
                <div class="fmas-ops-field">
                    <label for="fmasDetailsFilename">${t('fileName', 'File name')}</label>
                    <input type="text" id="fmasDetailsFilename" class="form-control"
                           placeholder="file_details_${new Date().toISOString().slice(0, 10)}" />
                </div>
                <div class="fmas-ops-dialog-actions">
                    <button type="button" class="action-btn" data-ops-dismiss>${t('cancel', 'Cancel')}</button>
                    <button type="submit" class="action-btn export-btn">${t('export', 'Export')}</button>
                </div>
            </form>
        </div>
    `;
    const list = dialog.querySelector('#fmasDetailsColumns');
    DETAILS_COLUMNS.forEach((col) => {
        const id = `fmasCol_${col.key}`;
        const label = document.createElement('label');
        label.className = 'fmas-ops-column-item';
        label.innerHTML = `<input type="checkbox" class="fmas-ops-column-check" id="${id}" value="${col.key}" ${['name', 'path', 'type', 'category', 'category_terms', 'keywords', 'source', 'date', 'size'].includes(col.key) ? 'checked' : ''}> <span>${col.label}</span>`;
        list.appendChild(label);
    });
    dialog.addEventListener('click', (event) => {
        if (event.target.closest('[data-ops-dismiss]')) {
            dialog.hidden = true;
        }
        const allNone = event.target.closest('[data-ops-columns]');
        if (allNone) {
            const on = allNone.getAttribute('data-ops-columns') === 'all';
            dialog.querySelectorAll('.fmas-ops-column-check').forEach((box) => { box.checked = on; });
        }
    });
    dialog.querySelector('#fmasDetailsExportForm').addEventListener('submit', async (event) => {
        event.preventDefault();
        const columns = Array.from(dialog.querySelectorAll('.fmas-ops-column-check:checked'))
            .map((box) => box.value);
        if (!columns.length) {
            notify('warning', t('chooseColumns', 'Choose at least one column'));
            return;
        }
        const format = dialog.querySelector('#fmasDetailsFormat').value;
        const scope = dialog.querySelector('#fmasDetailsScope').value;
        const filename = dialog.querySelector('#fmasDetailsFilename').value.trim();
        let ids = null;
        if (scope === 'selected') {
            ids = selectedIdsOrWarn();
            if (!ids) return;
        }
        const extension = format === 'excel' ? 'xlsx' : (format === 'json' ? 'json' : 'csv');
        const suggested = `${scope === 'all' ? 'all_indexed' : 'selected'}_file_details_${new Date().toISOString().slice(0, 10)}`;
        try {
            const body = {
                scope,
                columns,
                format,          // csv | excel | json, as chosen in the dialog
                filename: filename || suggested,
            };
            if (scope === 'selected') body.file_ids = ids;
            const saved = await postDownload('/api/files/details/export', body,
                ensureExportExtension(filename || suggested, extension, suggested));
            if (saved === false) return;  // the reader dismissed the save dialog
            notify('success', t('detailsExportReady', 'File details export ready.'));
            dialog.hidden = true;
        } catch (error) {
            notify('error', error.message || t('exportFailed', 'Export failed'));
        }
    });
    document.body.appendChild(dialog);
    return dialog;
}

/** Open the details export dialog (column picker + format + filename). */
export function openDetailsExportDialog({ preferAll = false } = {}) {
    const dialog = ensureDetailsDialog();
    const scope = dialog.querySelector('#fmasDetailsScope');
    if (scope) scope.value = preferAll ? 'all' : 'selected';
    const name = dialog.querySelector('#fmasDetailsFilename');
    if (name && !name.value) {
        name.placeholder = preferAll ? 'my_document_list' : 'selected_file_details';
        name.value = `${preferAll ? 'my_document_list' : 'selected_file_details'}_${new Date().toISOString().slice(0, 10)}`;
    }
    dialog.hidden = false;
}

/**
 * Resolve duplicate export basenames: file.pdf, file (2).pdf, …
 * @param {string[]} names
 * @returns {string[]}
 */
export function resolveDuplicateFilenames(names) {
    const used = new Map();
    return names.map((raw) => {
        let name = safeClientFilename(raw, 'export.bin');
        const lower = name.toLowerCase();
        const count = used.get(lower) || 0;
        used.set(lower, count + 1);
        if (count === 0) return name;
        const dot = name.lastIndexOf('.');
        if (dot > 0) {
            return `${name.slice(0, dot)} (${count + 1})${name.slice(dot)}`;
        }
        return `${name} (${count + 1})`;
    });
}

/**
 * Show an export manifest: original → export-as, with collision resolution.
 * @param {Array<{ id: number, original: string, exportAs?: string }>} entries
 * @returns {Promise<Array<{ id: number, exportAs: string }>|null>}
 */
export function showExportManifest(entries) {
    return new Promise((resolve) => {
        let dialog = document.getElementById('fmasExportManifestDialog');
        if (!dialog) {
            dialog = document.createElement('div');
            dialog.id = 'fmasExportManifestDialog';
            dialog.className = 'fmas-ops-dialog';
            dialog.setAttribute('role', 'dialog');
            dialog.setAttribute('aria-modal', 'true');
            document.body.appendChild(dialog);
        }
        const resolvedNames = resolveDuplicateFilenames(
            entries.map((e) => e.exportAs || e.original || `file_${e.id}`)
        );
        const rows = entries.map((entry, index) => {
            const exportAs = resolvedNames[index];
            return `
                <tr data-file-id="${entry.id}">
                    <td class="fmas-manifest-original" title="${escapeAttr(entry.original || '')}">${escapeAttr(entry.original || `file_${entry.id}`)}</td>
                    <td>
                        <input type="text" class="form-control form-control-sm fmas-manifest-name"
                               value="${escapeAttr(exportAs)}" data-file-id="${entry.id}" />
                    </td>
                </tr>`;
        }).join('');

        dialog.hidden = false;
        dialog.innerHTML = `
            <div class="fmas-ops-dialog-backdrop" data-manifest-cancel></div>
            <div class="fmas-ops-dialog-panel" style="max-width: 42rem;">
                <header class="fmas-ops-dialog-header">
                    <h2>${t('exportManifest', 'Export manifest')}</h2>
                    <button type="button" class="fmas-ops-dialog-close" data-manifest-cancel aria-label="${t('close', 'Close')}">&times;</button>
                </header>
                <p class="text-muted small mb-2">${t('exportManifestHint', 'Review export names. Duplicate names are numbered automatically.')}</p>
                <div style="max-height: 18rem; overflow: auto;">
                    <table class="table table-sm">
                        <thead><tr><th>${t('original', 'Original')}</th><th>${t('exportAs', 'Export as')}</th></tr></thead>
                        <tbody>${rows}</tbody>
                    </table>
                </div>
                <div class="fmas-ops-dialog-actions">
                    <button type="button" class="action-btn" data-manifest-cancel>${t('cancel', 'Cancel')}</button>
                    <button type="button" class="action-btn export-btn" data-manifest-confirm>${t('continueExport', 'Continue export')}</button>
                </div>
            </div>
        `;

        const finish = (result) => {
            dialog.hidden = true;
            resolve(result);
        };
        dialog.querySelector('[data-manifest-cancel]')?.addEventListener('click', () => finish(null), { once: true });
        dialog.querySelectorAll('[data-manifest-cancel]').forEach((el) => {
            el.addEventListener('click', () => finish(null), { once: true });
        });
        dialog.querySelector('[data-manifest-confirm]')?.addEventListener('click', () => {
            const inputs = Array.from(dialog.querySelectorAll('.fmas-manifest-name'));
            const raw = inputs.map((input) => ({
                id: Number(input.getAttribute('data-file-id')),
                exportAs: input.value.trim() || `file_${input.getAttribute('data-file-id')}`,
            }));
            const unique = resolveDuplicateFilenames(raw.map((r) => r.exportAs));
            finish(raw.map((r, i) => ({ id: r.id, exportAs: unique[i] })));
        }, { once: true });
    });
}

function escapeAttr(value) {
    return String(value == null ? '' : value)
        .replace(/&/g, '&amp;')
        .replace(/"/g, '&quot;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
}

/**
 * Batch export with an explicit rename manifest before the ZIP download.
 */
export async function exportSelectionWithManifest(mode = 'text') {
    const ids = selectedIdsOrWarn();
    if (!ids) return;

    // Build original names from the DOM when possible.
    const entries = ids.map((id) => {
        const row = document.querySelector(
            `.file-checkbox[value="${id}"], .file-select-checkbox[value="${id}"]`
        );
        const card = row?.closest('[data-file-name], .file-card, .file-row-item, tr');
        const original = card?.getAttribute('data-file-name')
            || card?.querySelector('[data-file-name]')?.getAttribute('data-file-name')
            || `file_${id}`;
        return { id, original };
    });

    const resolved = await showExportManifest(entries);
    if (!resolved) return;

    const kind = mode === 'originals' ? 'originals' : 'text';
    const suggested = kind === 'originals' ? 'original_files' : 'extracted_content';
    const name = await askFilename(
        t('selectedExportPrompt', 'Name this selected-file export (leave blank for an automatic name):'),
        `${suggested}_${new Date().toISOString().slice(0, 10)}`
    );
    if (name === null) return;

    // naming_template uses {name} from server; pass explicit map via filename only for archive.
    // Per-file renames are applied client-side for folder exports; for ZIP we send naming_template.
    try {
        const saved = await postDownload('/files/export', {
            file_ids: resolved.map((r) => r.id),
            mode: kind,
            filename: name,
            naming_template: '{name}',
            rename_map: Object.fromEntries(resolved.map((r) => [String(r.id), r.exportAs])),
        }, ensureExportExtension(name || suggested, 'zip', suggested));
        if (saved === false) return;  // the reader dismissed the save dialog
        notify('success', t('selectedExportReady', 'Selected-file export ready.'));
    } catch (error) {
        notify('error', (t('selectedExportFailed', 'Selected-file export failed.') + ' ' + error.message).trim());
    }
}
