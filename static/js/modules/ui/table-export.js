/**
 * The table toolbar's export menu: this view, every row of it, the reader's
 * chosen columns, as CSV or Excel - plus print.
 *
 * Export downloads are fetched without navigating away from the current
 * page. Browsers with the File System Access API get a native Save As picker;
 * other browsers get a filename prompt and their normal download handling.
 */
import {
    chooseExportDestination,
    ensureExportExtension,
    saveExportBlob,
} from '../core/export-download.js';

(function () {
    'use strict';

    // Paging coordinates are the one thing an export must not inherit: the
    // export is the whole view, not one page of it.
    const PARAMS_TO_DROP = ['page', 'per_page', 'limit', 'cursor', 'start_position'];

    function chosenColumns(menu) {
        return Array.from(menu.querySelectorAll('.ut-export-column-check:checked'))
            .map((box) => box.getAttribute('data-export-column'))
            .filter(Boolean);
    }

    function chosenScope(menu) {
        const checked = menu.querySelector('.ut-export-scope-check:checked');
        return checked ? checked.value : 'view';
    }

    function exportUrl(menu, format, filename, columns) {
        // A table inside a panel carries its own view string; the page URL is
        // not that table's view. "Entire dataset" keeps only the identity
        // parameters that pin that panel to its owner.
        const scope = chosenScope(menu);
        const source = scope === 'all'
            ? (menu.getAttribute('data-export-scope-base') || '')
            : (menu.getAttribute('data-export-params') || window.location.search);
        const params = new URLSearchParams(source);
        PARAMS_TO_DROP.forEach((name) => params.delete(name));
        // Route-path identities (keyword/category IDs) do not appear in the
        // browser query string. The base scope is authoritative for those
        // parameters in both "view" and "all" exports.
        const baseParams = new URLSearchParams(menu.getAttribute('data-export-scope-base') || '');
        baseParams.forEach((value, key) => params.set(key, value));
        params.set('format', format);
        params.set('columns', columns.join(','));
        params.set('filename', filename);
        const customPath = menu.getAttribute('data-export-path');
        if (customPath) {
            return customPath + '?' + params.toString();
        }
        return '/api/export/' + encodeURIComponent(menu.getAttribute('data-export-interface'))
            + '?' + params.toString();
    }

    function translated(key, fallback) {
        try {
            const value = typeof window.t === 'function' ? window.t(key) : '';
            return value && value !== key ? value : fallback;
        } catch (_error) {
            return fallback;
        }
    }

    function filenameForFormat(value, format, fallback) {
        const extension = format === 'xlsx' ? 'xlsx' : 'csv';
        let candidate = String(value || '').trim();
        const existingExtension = candidate.match(/\.([^.\/\\]+)$/);
        if (existingExtension && existingExtension[1].toLowerCase() !== extension) {
            candidate = candidate.slice(0, -existingExtension[0].length);
        }
        return ensureExportExtension(candidate, extension, fallback);
    }

    function suggestedFilename(menu, format) {
        const interfaceId = menu.getAttribute('data-export-interface') || 'table';
        const date = new Date().toISOString().slice(0, 10);
        return filenameForFormat(`${interfaceId}_export_${date}`, format, interfaceId);
    }

    function responseFilename(response, fallback) {
        const disposition = response.headers.get('Content-Disposition') || '';
        const match = disposition.match(/filename\*?=(?:UTF-8''|")?([^";]+)/i);
        if (!match) return fallback;
        try { return decodeURIComponent(match[1].replace(/"/g, '')); }
        catch (_error) { return match[1].replace(/"/g, ''); }
    }

    function notify(kind, message) {
        const toast = window.Toast;
        if (toast && typeof toast[kind] === 'function') {
            toast[kind](message);
            return;
        }
        const fallback = kind === 'error' ? window.showError
            : kind === 'warning' ? window.showWarning : window.showSuccess;
        if (typeof fallback === 'function') fallback(message);
        else if (kind === 'error') window.alert(message);
    }

    async function downloadExport(menu, button, format) {
        const columns = chosenColumns(menu);
        if (!columns.length) {
            notify('warning', translated(
                'Please select at least one column to export.',
                'Please select at least one column to export.'));
            return;
        }

        const suggested = suggestedFilename(menu, format);
        let destination = null;
        let filename = suggested;
        try {
            // Invoke the native picker before any network await while the
            // click still carries transient user activation.
            destination = await chooseExportDestination(suggested);
            if (destination === false) return;
            if (destination && destination.name) {
                filename = filenameForFormat(destination.name, format, suggested);
            }
        } catch (error) {
            console.warn('Save location picker unavailable:', error);
            destination = null;
        }

        if (!destination) {
            const requested = await window.prompt(
                translated('Name your export (leave blank for an automatic name):',
                    'Name your export (leave blank for an automatic name):'),
                suggested.replace(/\.[^.]+$/, ''));
            if (requested === null) return;
            filename = filenameForFormat(String(requested).trim() || suggested, format, suggested);
        }

        button.disabled = true;
        try {
            const response = await fetch(exportUrl(menu, format, filename, columns), {
                method: 'GET',
                credentials: 'same-origin',
                headers: { 'Accept': 'text/csv, application/vnd.openxmlformats-officedocument.spreadsheetml.sheet, application/json' },
            });
            if (!response.ok) {
                const problem = await response.json().catch(() => ({}));
                throw new Error(problem.error || `Export failed (HTTP ${response.status}).`);
            }
            const blob = await response.blob();
            const saved = await saveExportBlob(
                blob,
                responseFilename(response, filename),
                destination);
            if (saved) notify('success', translated('Filename export ready.', 'Export ready.'));
        } catch (error) {
            console.error('Table export failed:', error);
            notify('error', error.message || translated('Export failed', 'Could not export this table.'));
        } finally {
            button.disabled = false;
        }
    }

    function setAllColumns(menu, checked) {
        menu.querySelectorAll('.ut-export-column-check').forEach((box) => {
            box.checked = !!checked;
        });
    }

    function onTableClick(event) {
        const columnsAll = event.target.closest('[data-export-columns-all]');
        if (columnsAll) {
            const menu = columnsAll.closest('[data-export-interface]');
            if (!menu) return;
            event.preventDefault();
            setAllColumns(menu, true);
            return;
        }
        const columnsNone = event.target.closest('[data-export-columns-none]');
        if (columnsNone) {
            const menu = columnsNone.closest('[data-export-interface]');
            if (!menu) return;
            event.preventDefault();
            setAllColumns(menu, false);
            return;
        }

        const target = event.target.closest('[data-export-download], [data-export-print]');
        if (!target) return;
        const menu = target.closest('[data-export-interface]');
        if (!menu) return;
        event.preventDefault();
        if (target.hasAttribute('data-export-print')) {
            window.print();
            return;
        }
        void downloadExport(menu, target, target.getAttribute('data-export-download'));
    }

    // Delegation survives main-content swaps: the shell and its document are
    // stable while the table markup is replaced.
    document.addEventListener('click', onTableClick);
})();
