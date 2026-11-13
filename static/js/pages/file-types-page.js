import {
    chooseExportDestination,
    ensureExportExtension,
    saveExportBlob,
} from '../modules/core/export-download.js';

function translations() {
    const dataElement = document.getElementById('file-types-page-data');
    try {
        return JSON.parse(dataElement?.textContent || '{}').translations || {};
    } catch (_error) {
        return {};
    }
}

function notify(kind, message, detail = '') {
    const options = detail ? { detail } : undefined;
    const toast = window.Toast;
    if (toast && typeof toast[kind] === 'function') {
        toast[kind](message, options);
        return;
    }
    const fallback = kind === 'error' ? window.showError : window.showSuccess;
    if (typeof fallback === 'function') fallback(detail ? `${message} ${detail}` : message);
    else window.alert(detail ? `${message} ${detail}` : message);
}

function responseFilename(response, fallback) {
    const disposition = response.headers.get('Content-Disposition') || '';
    const match = disposition.match(/filename\*?=(?:UTF-8''|")?([^";]+)/i);
    if (!match) return fallback;
    try { return decodeURIComponent(match[1].replace(/"/g, '')); }
    catch (_error) { return match[1].replace(/"/g, ''); }
}

async function exportNames(button, messages) {
    const scope = button.dataset.exportNames;
    const format = button.dataset.format === 'excel' ? 'excel' : 'csv';
    const fileType = button.dataset.fileType || '';
    const typeSlug = fileType.replace(/^\.+/, '').replace(/[^A-Za-z0-9_-]+/g, '_').toLowerCase();
    const suggested = scope === 'all'
        ? 'all_indexed_filenames'
        : `${typeSlug || 'selected'}_filenames`;
    const filename = await window.prompt(
        messages.filenamePrompt || 'Name this filename export (leave blank for an automatic name):',
        suggested);
    if (filename === null) return;

    let destination;
    const extension = format === 'excel' ? 'xlsx' : 'csv';
    const suggestedFilename = ensureExportExtension(
        filename.trim() || suggested, extension, suggested);
    try {
        // Ask for the save location before network awaits so browsers that
        // enforce transient user activation still permit their native picker.
        destination = await chooseExportDestination(suggestedFilename);
        if (destination === false) return;
    } catch (error) {
        console.warn('Save location picker unavailable:', error);
        notify('error', messages.exportFailed || 'Could not export the filename list.', error.message);
        return;
    }

    button.disabled = true;
    try {
        const response = await fetch('/api/files/names/export', {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.content || '',
            },
            body: JSON.stringify({
                scope,
                ...(scope === 'type' ? { file_type: fileType } : {}),
                format,
                filename: filename.trim(),
            }),
        });
        if (!response.ok) {
            const problem = await response.json().catch(() => ({}));
            const error = response.status === 413
                ? (messages.exportLimit || 'This filename export exceeds the synchronous limit. Narrow the list and try again.')
                : (problem.error || `HTTP ${response.status}`);
            throw new Error(error);
        }

        const blob = await response.blob();
        const fallback = `${suggested}.${format === 'excel' ? 'xlsx' : 'csv'}`;
        const filename = responseFilename(response, fallback);
        const saved = await saveExportBlob(blob, filename, destination);
        if (!saved) return;
        notify('success', messages.exportReady || 'Filename export ready.',
            response.headers.get('X-Export-Rows') || '');
    } catch (error) {
        console.error('Filename export failed:', error);
        notify('error', messages.exportFailed || 'Could not export the filename list.', error.message);
    } finally {
        button.disabled = false;
    }
}

export default function init() {
    const messages = translations();
    document.querySelectorAll('[data-export-names]').forEach(button => {
        button.addEventListener('click', () => exportNames(button, messages));
    });
    // The panel of one format's documents is the shared component
    // (modules/ui/documents-panel.js); the page only provides the messages.
    if (window.DocumentsPanel) {
        window.DocumentsPanel.wire({ panelId: 'documentsPanel', tableId: 'panelFilesTable' });
    }
}
