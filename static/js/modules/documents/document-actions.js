/**
 * DocumentActionsController — one facade for selection + export + analysis.
 *
 * Pages bind toolbar buttons with ``data-doc-action`` instead of scattering
 * page-specific ``window.*`` handlers. Existing selectable-files-ops /
 * file-export implementations remain the workers.
 */

import { collectSelectedIds } from '../file-operations/bulk-analyst-actions.js';
import { navigationState } from '../core/state.js';
import {
    fetchSelectionAnalysis,
    mountAnalysisSummary,
    renderAnalysisSummary,
} from './document-analysis.js';

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
    else if (kind === 'error' || kind === 'warning') window.alert(message);
}

async function ops() {
    return import('../file-operations/selectable-files-ops.js');
}

async function fileExport() {
    return import('../file-operations/file-export.js');
}

function selectionOrWarn() {
    const ids = collectSelectedIds();
    if (!ids.length) {
        notify('warning', t('pleaseSelectFilesToExport', 'Please select files to export'));
        return null;
    }
    return ids;
}

function loadedFileIds() {
    const fromState = (navigationState.loadedFiles || [])
        .map((f) => Number(f.id))
        .filter((n) => Number.isFinite(n) && n > 0);
    if (fromState.length) return fromState;
    return Array.from(document.querySelectorAll('.file-checkbox, .file-select-checkbox'))
        .map((el) => Number(el.value))
        .filter((n) => Number.isFinite(n) && n > 0);
}

function ensureAnalysisDialog() {
    let dialog = document.getElementById('docAnalysisDialog');
    if (dialog) return dialog;
    dialog = document.createElement('div');
    dialog.id = 'docAnalysisDialog';
    dialog.className = 'doc-analysis-dialog';
    dialog.hidden = true;
    dialog.setAttribute('role', 'dialog');
    dialog.setAttribute('aria-modal', 'true');
    dialog.innerHTML = `
        <div class="doc-analysis-dialog-backdrop" data-doc-dismiss></div>
        <div class="doc-analysis-dialog-panel">
            <header class="doc-analysis-dialog-header">
                <h2>${t('documentAnalysis', 'Document analysis')}</h2>
                <button type="button" class="doc-analysis-dialog-close" data-doc-dismiss aria-label="${t('close', 'Close')}">&times;</button>
            </header>
            <div id="docAnalysisDialogBody"></div>
        </div>
    `;
    dialog.addEventListener('click', (event) => {
        if (event.target.closest('[data-doc-dismiss]')) dialog.hidden = true;
    });
    document.body.appendChild(dialog);
    return dialog;
}

export const DocumentActions = {
    getSelection() {
        return collectSelectedIds();
    },

    getLoadedIds() {
        return loadedFileIds();
    },

    async viewAnalysis(fileIds = null) {
        const ids = fileIds || selectionOrWarn();
        if (!ids) return;
        const dialog = ensureAnalysisDialog();
        const body = dialog.querySelector('#docAnalysisDialogBody');
        body.innerHTML = `<div class="doc-analysis-summary loading">${t('loadingAnalysis', 'Loading analysis...')}</div>`;
        dialog.hidden = false;
        try {
            if (ids.length === 1) {
                const { fetchDocumentAnalysis } = await import('./document-analysis.js');
                const analysis = await fetchDocumentAnalysis(ids[0]);
                mountAnalysisSummary(body, analysis);
            } else {
                const data = await fetchSelectionAnalysis(ids);
                body.innerHTML = renderAnalysisSummary(data.aggregate || {}, {
                    title: t('selectionAnalysis', 'Selection analysis'),
                    fileCount: data.aggregate?.file_count || ids.length,
                });
            }
        } catch (error) {
            body.innerHTML = `<div class="doc-analysis-summary error">${error.message || t('errorLoadingAnalysis', 'Could not load analysis')}</div>`;
        }
    },

    async export({ kind = 'text', destination = 'save-as' } = {}) {
        const mod = await ops();
        if (destination === 'folder' || destination === 'existing-folder' || destination === 'new-folder') {
            return mod.exportSelectionToFolder(kind === 'originals' ? 'originals' : 'text');
        }
        if (kind === 'originals') {
            if (typeof window.exportSelectedOriginals === 'function') {
                return window.exportSelectedOriginals();
            }
            const exp = await fileExport();
            const ids = selectionOrWarn();
            if (!ids) return;
            return exp.exportSelectedFiles(ids, 'originals');
        }
        if (typeof window.exportSelectedFilesShared === 'function') {
            return window.exportSelectedFilesShared();
        }
        const exp = await fileExport();
        const ids = selectionOrWarn();
        if (!ids) return;
        return exp.exportSelectedFiles(ids, 'text');
    },

    async openOriginal(fileId = null) {
        const ids = fileId != null ? [Number(fileId)] : selectionOrWarn();
        if (!ids) return;
        if (ids.length !== 1) {
            notify('warning', t('openOriginalSingleOnly', 'Open original works with one file at a time'));
            return;
        }
        const mod = await ops();
        return mod.openInNativeApp(ids[0]);
    },

    async locate(fileId = null) {
        const ids = fileId != null ? [Number(fileId)] : selectionOrWarn();
        if (!ids) return;
        const mod = await ops();
        // Locate the first selected; multi-locate can expand later.
        return mod.locateFile(ids[0]);
    },

    async exportNames({ scope = 'selected', format = 'csv', fileIds = null } = {}) {
        const mod = await ops();
        if (scope === 'loaded') {
            const ids = fileIds || loadedFileIds();
            if (!ids.length) {
                notify('warning', t('noLoadedFiles', 'No loaded files to export'));
                return;
            }
            return mod.exportFileNames({
                scope: 'selected',
                format,
                fileIds: ids,
                suggestedName: 'my_document_list',
            });
        }
        return mod.exportFileNames({ scope, format, fileIds });
    },

    async exportDetails(options = {}) {
        const mod = await ops();
        return mod.openDetailsExportDialog(options);
    },

    async exportStructuredFolder(context = 'files') {
        const { openStructuredFolderExport } = await import('../file-operations/structured-folder-export.js');
        return openStructuredFolderExport({ context });
    },

    async exportWithRename(fileId = null) {
        const ids = fileId != null ? [Number(fileId)] : selectionOrWarn();
        if (!ids) return;
        const mod = await ops();
        if (ids.length === 1) {
            return mod.exportFileWithRename(ids[0]);
        }
        // Batch: folder export with rename prompt per flow via manifest
        return mod.exportSelectionWithManifest('originals');
    },
};

const ACTION_MAP = {
    'view-analysis': () => DocumentActions.viewAnalysis(),
    'export-text': () => DocumentActions.export({ kind: 'text', destination: 'save-as' }),
    'export-originals': () => DocumentActions.export({ kind: 'originals', destination: 'save-as' }),
    'export-text-folder': () => DocumentActions.export({ kind: 'text', destination: 'folder' }),
    'export-originals-folder': () => DocumentActions.export({ kind: 'originals', destination: 'folder' }),
    'export-rename': () => DocumentActions.exportWithRename(),
    'open-original': () => DocumentActions.openOriginal(),
    'locate': () => DocumentActions.locate(),
    'export-names-csv': () => DocumentActions.exportNames({ scope: 'selected', format: 'csv' }),
    'export-names-excel': () => DocumentActions.exportNames({ scope: 'selected', format: 'excel' }),
    'export-loaded-names': () => DocumentActions.exportNames({ scope: 'loaded', format: 'csv' }),
    'export-details': () => DocumentActions.exportDetails(),
    'export-structured-folder': () => DocumentActions.exportStructuredFolder('files'),
};

let delegated = false;

/** Bind once: clicks on [data-doc-action] run DocumentActions. */
export function initDocumentActions() {
    if (delegated) return;
    delegated = true;
    document.addEventListener('click', (event) => {
        const btn = event.target.closest('[data-doc-action]');
        if (!btn) return;
        const action = btn.getAttribute('data-doc-action');
        const handler = ACTION_MAP[action];
        if (!handler) return;
        event.preventDefault();
        Promise.resolve(handler()).catch((error) => {
            console.error('Document action failed:', action, error);
            notify('error', error.message || t('actionFailed', 'Action failed'));
        });
    });
}

// Auto-init for soft navigation and full loads.
if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initDocumentActions, { once: true });
    } else {
        initDocumentActions();
    }
}

export default DocumentActions;
