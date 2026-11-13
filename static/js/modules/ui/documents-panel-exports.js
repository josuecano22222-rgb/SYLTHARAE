/**
 * Bulk file-content and original-file exports for the shared document panel.
 *
 * File Types, Keywords and Categories all adopt the same paged File Library
 * fragment into a side panel. This module wires that shared fragment without
 * adding a competing selection or export path.
 */
import { exportSelectedFiles } from '../file-operations/file-export.js';

function sync(panel) {
    if (!panel) return;
    const count = panel.querySelectorAll('[data-panel-body] .file-checkbox:checked').length;
    const toolbar = panel.querySelector('[data-panel-export-toolbar]');
    const countNode = panel.querySelector('[data-panel-selected-count]');
    if (countNode) countNode.textContent = String(count);
    if (toolbar) toolbar.hidden = count === 0;
    panel.querySelectorAll('[data-bulk-file-export]').forEach((button) => {
        button.disabled = count === 0;
    });
}

function onChange(event) {
    const checkbox = event.target;
    if (!checkbox.matches?.('.file-checkbox, .ut-check-all')) return;
    const panel = checkbox.closest('.documents-panel');
    if (panel) sync(panel);
}

function onClick(event) {
    const button = event.target.closest('[data-bulk-file-export]');
    if (!button) return;
    const panel = button.closest('.documents-panel');
    if (!panel) return;
    event.preventDefault();
    const ids = Array.from(panel.querySelectorAll('[data-panel-body] .file-checkbox:checked'))
        .map((checkbox) => checkbox.value);
    if (!ids.length) {
        sync(panel);
        return;
    }
    button.disabled = true;
    exportSelectedFiles(ids, button.getAttribute('data-bulk-file-export'))
        .catch((error) => {
            console.error('Document-panel export failed:', error);
            if (typeof window.showError === 'function') {
                window.showError(error.message || 'Could not export the selected documents.');
            }
        })
        .finally(() => sync(panel));
}

document.addEventListener('change', onChange);
document.addEventListener('click', onClick);

// The panel adopts fragments after fetch and appends later pages. Give its
// owner one explicit hook so the visible count is always synchronized.
window.DocumentsPanelExports = { sync };
document.querySelectorAll('.documents-panel').forEach(sync);
