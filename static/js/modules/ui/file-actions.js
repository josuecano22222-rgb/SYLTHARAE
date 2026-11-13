/**
 * File actions: the one set of operations any file name or file row offers,
 * everywhere in the application.
 *
 * A row (or a file link) marks itself with `data-file-menu-button` and
 * carries `data-file-id` + `data-file-name`. One control opens one menu:
 *
 *   Quick Preview      — a modal panel over the page (inline render)
 *   Open Side-by-Side  — a split view beside the table, the page stays put
 *   Open in New Page   — the application's own document view, its own tab
 *   Open Original      — the source file, downloaded; it opens in the
 *                        application the reader's desktop uses for it
 *   Locate Folder      — where the file lives; revealed in the file manager
 *                        when the reader is on the storage machine
 *   Copy               — a duplicate record over the same bytes
 *   Rename             — the record, and the file on disk when it is there
 *   Save As            — the original under a name the reader types
 *
 * CSP-safe: no inline script, no eval, no browser dialogs.
 */
(function () {
    'use strict';

    const MENU_ID = 'fileActionsMenu';
    const PREVIEW_ID = 'fileActionsPreview';
    const SPLIT_ID = 'fileActionsSplit';
    const DIALOG_ID = 'fileActionsDialog';

    let csrfToken = null;
    let activeFile = null;
    let splitFrame = null;

    function translations() {
        return window.translations || {};
    }

    function t(key, fallback) {
        const dict = translations();
        return dict[key] || fallback;
    }

    function csrf() {
        if (csrfToken) return csrfToken;
        const meta = document.querySelector('meta[name="csrf-token"]');
        csrfToken = meta ? meta.getAttribute('content') : '';
        return csrfToken;
    }

    function toast(message, tone) {
        if (typeof window.showToast === 'function') {
            window.showToast(message, tone || 'info');
            return;
        }
        // The page has no toast helper: say it in the menu's own status line.
        const status = document.getElementById(MENU_ID);
        if (status) {
            status.setAttribute('data-last-status', message);
        }
        console.log(message);
    }

    function escapeHtml(value) {
        return String(value == null ? '' : value).replace(/[&<>"']/g, (ch) => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        }[ch]));
    }

    // ------------------------------------------------------------------
    // The menu
    // ------------------------------------------------------------------

    function ensureMenu() {
        let menu = document.getElementById(MENU_ID);
        if (menu) return menu;
        menu = document.createElement('div');
        menu.id = MENU_ID;
        menu.className = 'dropdown-menu file-actions-menu';
        menu.setAttribute('role', 'menu');
        menu.hidden = true;
        menu.innerHTML = [
            '<button type="button" class="dropdown-item" role="menuitem" data-file-action="preview">',
            '<i class="bi bi-eye me-2" aria-hidden="true"></i>' + escapeHtml(t('quickPreview', 'Quick Preview')) + '</button>',
            '<button type="button" class="dropdown-item" role="menuitem" data-file-action="split">',
            '<i class="bi bi-layout-sidebar-inset me-2" aria-hidden="true"></i>' + escapeHtml(t('openSideBySide', 'Open Side-by-Side')) + '</button>',
            '<button type="button" class="dropdown-item" role="menuitem" data-file-action="newpage">',
            '<i class="bi bi-box-arrow-up-right me-2" aria-hidden="true"></i>' + escapeHtml(t('openInNewPage', 'Open in New Page')) + '</button>',
            '<div class="dropdown-divider"></div>',
            '<button type="button" class="dropdown-item" role="menuitem" data-file-action="original">',
            '<i class="bi bi-download me-2" aria-hidden="true"></i>' + escapeHtml(t('openOriginal', 'Open Original')) + '</button>',
            '<button type="button" class="dropdown-item" role="menuitem" data-file-action="native">',
            '<i class="bi bi-app-indicator me-2" aria-hidden="true"></i>' + escapeHtml(t('openInNativeApp', 'Open in native app')) + '</button>',
            '<button type="button" class="dropdown-item" role="menuitem" data-file-action="saveas">',
            '<i class="bi bi-save me-2" aria-hidden="true"></i>' + escapeHtml(t('saveAs', 'Save As')) + '</button>',
            '<button type="button" class="dropdown-item" role="menuitem" data-file-action="locate">',
            '<i class="bi bi-folder2-open me-2" aria-hidden="true"></i>' + escapeHtml(t('locateFolder', 'Locate Folder')) + '</button>',
            '<div class="dropdown-divider"></div>',
            '<button type="button" class="dropdown-item" role="menuitem" data-file-action="copy">',
            '<i class="bi bi-files me-2" aria-hidden="true"></i>' + escapeHtml(t('copyFile', 'Copy')) + '</button>',
            '<button type="button" class="dropdown-item" role="menuitem" data-file-action="rename">',
            '<i class="bi bi-pencil me-2" aria-hidden="true"></i>' + escapeHtml(t('renameFile', 'Rename')) + '</button>'
        ].join('');
        document.body.appendChild(menu);
        menu.addEventListener('click', onMenuItem);
        document.addEventListener('click', (event) => {
            if (!menu.hidden && !menu.contains(event.target)
                && !event.target.closest('[data-file-menu-button]')) {
                closeMenu();
            }
        });
        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') closeMenu();
        });
        return menu;
    }

    function openMenu(button) {
        const file = {
            id: button.getAttribute('data-file-id'),
            name: button.getAttribute('data-file-name') || ''
        };
        if (!file.id) return;
        activeFile = file;
        const menu = ensureMenu();
        menu.hidden = false;
        const rect = button.getBoundingClientRect();
        const width = menu.offsetWidth || 240;
        const left = Math.max(8, Math.min(rect.left, window.innerWidth - width - 8));
        menu.style.left = left + 'px';
        menu.style.top = Math.min(rect.bottom + 4, window.innerHeight - menu.offsetHeight - 8) + 'px';
    }

    function closeMenu() {
        const menu = document.getElementById(MENU_ID);
        if (menu) menu.hidden = true;
    }

    function onMenuItem(event) {
        const item = event.target.closest('[data-file-action]');
        if (!item || !activeFile) return;
        event.preventDefault();
        const action = item.getAttribute('data-file-action');
        closeMenu();
        runAction(action, activeFile);
    }

    // ------------------------------------------------------------------
    // Actions
    // ------------------------------------------------------------------

    function inlineUrl(file) {
        return '/api/file/' + encodeURIComponent(file.id) + '/original/content';
    }

    function downloadUrl(file, name) {
        let url = '/api/file/' + encodeURIComponent(file.id) + '/original/content?download=1';
        if (name) url += '&filename=' + encodeURIComponent(name);
        return url;
    }

    function runAction(action, file) {
        switch (action) {
            case 'preview':
                openPreview(file);
                break;
            case 'split':
                openSplit(file);
                break;
            case 'newpage':
                window.open('/file/' + encodeURIComponent(file.id) + '/full-content', '_blank', 'noopener');
                break;
            case 'original':
                saveFile({url: downloadUrl(file), suggestedName: file.name});
                break;
            case 'native':
                openNative(file);
                break;
            case 'saveas':
                saveFile({url: downloadUrl(file), suggestedName: file.name});
                break;
            case 'locate':
                locate(file);
                break;
            case 'copy':
                copy(file);
                break;
            case 'rename':
                askName(t('renameFile', 'Rename'), file.name, (name) => {
                    if (name) rename(file, name);
                });
                break;
        }
    }

    function describe(file) {
        return fetch('/api/file/' + encodeURIComponent(file.id) + '/original', {
            credentials: 'same-origin',
        }).then((response) => response.json())
            .then((json) => (json && json.original) || {});
    }

    function viewerDocument(file, info) {
        // The frame holds what the browser renders without script; anything
        // else is offered as a download instead of an empty pane.
        const url = inlineUrl(file);
        if (!info || info.available === false) {
            return '<div class="empty-state" role="status">'
                + '<i class="bi bi-file-earmark-x" aria-hidden="true"></i>'
                + '<p>' + escapeHtml((info && info.message && info.message.text)
                    || t('originalUnavailable', 'The original file is not available. The extracted content still is.'))
                + '</p></div>';
        }
        const kind = info.viewer;
        if (kind === 'image') {
            return '<img src="' + escapeHtml(url) + '" class="file-actions-image" alt="' + escapeHtml(file.name) + '">';
        }
        if (kind === 'pdf' || kind === 'text' || kind === 'audio' || kind === 'video') {
            return '<iframe src="' + escapeHtml(url) + '" class="file-actions-frame" title="'
                + escapeHtml(file.name) + '"></iframe>';
        }
        return '<div class="empty-state" role="status">'
            + '<i class="bi bi-file-earmark-arrow-down" aria-hidden="true"></i>'
            + '<p>' + escapeHtml(t('noInlinePreview', 'This format has no preview here.')) + '</p>'
            + '<a class="btn btn-primary" href="' + escapeHtml(downloadUrl(file))
            + '" data-file-save-url="' + escapeHtml(downloadUrl(file))
            + '" data-file-save-name="' + escapeHtml(file.name) + '">'
            + escapeHtml(t('openOriginal', 'Open Original')) + '</a></div>';
    }

    function headerFor(file) {
        return '<div class="file-actions-head">'
            + '<span class="file-actions-title"><i class="bi bi-file-earmark me-2" aria-hidden="true"></i>'
            + escapeHtml(file.name) + '</span>'
            + '<button type="button" class="btn-close" data-file-close aria-label="'
            + escapeHtml(t('close', 'Close')) + '"></button></div>';
    }

    function bindClose(container, onClose) {
        container.querySelectorAll('[data-file-close]').forEach((button) => {
            button.addEventListener('click', onClose);
        });
    }

    function openPreview(file) {
        closeSplit();
        let shell = document.getElementById(PREVIEW_ID);
        if (!shell) {
            shell = document.createElement('div');
            shell.id = PREVIEW_ID;
            shell.className = 'file-actions-backdrop';
            document.body.appendChild(shell);
        }
        shell.innerHTML = '<div class="file-actions-panel" role="dialog" aria-modal="true" aria-label="'
            + escapeHtml(file.name) + '">' + headerFor(file)
            + '<div class="file-actions-body"><div class="empty-state" role="status">'
            + '<span class="spinner-border text-primary" aria-hidden="true"></span></div></div></div>';
        shell.hidden = false;
        bindClose(shell, () => { shell.hidden = true; shell.innerHTML = ''; });
        shell.addEventListener('click', (event) => {
            if (event.target === shell) { shell.hidden = true; shell.innerHTML = ''; }
        });
        describe(file).then((info) => {
            const body = shell.querySelector('.file-actions-body');
            if (body && !shell.hidden) body.innerHTML = viewerDocument(file, info);
        }).catch(() => {
            const body = shell.querySelector('.file-actions-body');
            if (body) body.innerHTML = viewerDocument(file, { available: false });
        });
    }

    function openSplit(file) {
        const existing = document.getElementById(SPLIT_ID);
        if (existing) existing.remove();
        const pane = document.createElement('div');
        pane.id = SPLIT_ID;
        pane.className = 'file-actions-split';
        pane.innerHTML = headerFor(file)
            + '<div class="file-actions-body"><div class="empty-state" role="status">'
            + '<span class="spinner-border text-primary" aria-hidden="true"></span></div></div>';
        document.body.appendChild(pane);
        splitFrame = pane;
        document.body.classList.add('file-actions-splitting');
        bindClose(pane, closeSplit);
        describe(file).then((info) => {
            const body = pane.querySelector('.file-actions-body');
            if (body && document.getElementById(SPLIT_ID) === pane) {
                body.innerHTML = viewerDocument(file, info);
            }
        }).catch(() => {
            const body = pane.querySelector('.file-actions-body');
            if (body) body.innerHTML = viewerDocument(file, { available: false });
        });
    }

    function closeSplit() {
        const pane = document.getElementById(SPLIT_ID);
        if (pane) pane.remove();
        splitFrame = null;
        document.body.classList.remove('file-actions-splitting');
    }

    function locate(file) {
        const bridge = window.inforaxisDesktop || window.INFORAXIS_DESKTOP || null;
        if (bridge && typeof bridge.openContainingFolder === 'function') {
            bridge.openContainingFolder({ fileId: file.id, fileName: file.name })
                .then(() => toast(t('folderRevealed', 'The folder is open in your file manager.'), 'success'))
                .catch(() => locateViaApi(file));
            return;
        }
        locateViaApi(file);
    }

    function locateViaApi(file) {
        fetch('/api/file/' + encodeURIComponent(file.id) + '/locate', {
            credentials: 'same-origin',
        }).then((response) => response.json()).then((json) => {
            if (!json.success) {
                toast(json.error || t('locateFailed', 'The file location is not known.'), 'error');
                return;
            }
            if (json.revealed) {
                toast(t('folderRevealed', 'The folder is open in your file manager.'), 'success');
                return;
            }
            if (navigator.clipboard && json.path) {
                navigator.clipboard.writeText(json.path).then(() => {
                    toast((json.reason || json.path) + ' ' + t('pathCopied', 'Path copied.'), 'info');
                }, () => toast(json.path, 'info'));
            } else {
                toast(json.path, 'info');
            }
        }).catch(() => toast(t('locateFailed', 'The file location is not known.'), 'error'));
    }

    function openNative(file) {
        const bridge = window.inforaxisDesktop || window.INFORAXIS_DESKTOP || null;
        const openDocument = bridge && (bridge.openDocumentForEdit || bridge.openDocument);
        if (typeof openDocument !== 'function') {
            toast(t('desktopBridgeMissing',
                'Opening or editing a native file requires the trusted local SYLTHARAE companion. Downloading instead.'), 'info');
            window.location.href = downloadUrl(file);
            return;
        }
        describe(file).then((info) => {
            return openDocument.call(bridge, {
                fileId: file.id,
                fileName: info.name || file.name,
                extension: info.extension,
                mimeType: info.mime_type,
                downloadUrl: downloadUrl(file),
                edit: typeof bridge.openDocumentForEdit === 'function',
            });
        }).catch((error) => {
            toast(t('desktopBridgeFailed', 'The local SYLTHARAE companion could not complete this action.') + ' ' + (error.message || ''), 'error');
        });
    }

    function post(file, path, body) {
        return fetch(path, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrf(),
            },
            body: JSON.stringify(body || {}),
        }).then((response) => response.json());
    }

    function copy(file) {
        post(file, '/api/file/' + encodeURIComponent(file.id) + '/copy', {})
            .then((json) => {
                if (json.success) {
                    toast(t('fileCopied', 'A copy was created.') + ' ' + (json.name || ''), 'success');
                    if (typeof window.refreshMainContent === 'function') {
                        window.refreshMainContent();
                    } else {
                        window.location.reload();
                    }
                } else {
                    toast(json.error || t('copyFailed', 'The copy could not be created.'), 'error');
                }
            })
            .catch(() => toast(t('copyFailed', 'The copy could not be created.'), 'error'));
    }

    function rename(file, newName) {
        post(file, '/api/file/' + encodeURIComponent(file.id) + '/rename', { new_name: newName })
            .then((json) => {
                if (json.success) {
                    toast(t('fileRenamed', 'Renamed.') + ' ' + (json.note || ''), 'success');
                    if (typeof window.refreshMainContent === 'function') {
                        window.refreshMainContent();
                    } else {
                        window.location.reload();
                    }
                } else {
                    toast(json.error || t('renameFailed', 'The file could not be renamed.'), 'error');
                }
            })
            .catch(() => toast(t('renameFailed', 'The file could not be renamed.'), 'error'));
    }

    // ------------------------------------------------------------------
    // The one name dialog (no browser dialogs)
    // ------------------------------------------------------------------

    function askName(title, current, onConfirm, onCancel) {
        let settled = false;
        let shell = document.getElementById(DIALOG_ID);
        if (!shell) {
            shell = document.createElement('div');
            shell.id = DIALOG_ID;
            shell.className = 'file-actions-backdrop';
            document.body.appendChild(shell);
        }
        shell.innerHTML = '<div class="file-actions-dialog" role="dialog" aria-modal="true">'
            + '<div class="file-actions-head"><span class="file-actions-title">' + escapeHtml(title) + '</span>'
            + '<button type="button" class="btn-close" data-file-close aria-label="'
            + escapeHtml(t('close', 'Close')) + '"></button></div>'
            + '<form class="file-actions-dialog-body">'
            + '<label class="form-label" for="fileActionsName">' + escapeHtml(t('fileName', 'File name')) + '</label>'
            + '<input type="text" class="form-control" id="fileActionsName" value="'
            + escapeHtml(current) + '" maxlength="255">'
            + '<div class="file-actions-dialog-actions">'
            + '<button type="button" class="btn btn-secondary" data-file-close>'
            + escapeHtml(t('cancel', 'Cancel')) + '</button>'
            + '<button type="submit" class="btn btn-primary">' + escapeHtml(t('save', 'Save')) + '</button>'
            + '</div></form></div>';
        shell.hidden = false;
        const input = shell.querySelector('#fileActionsName');
        input.focus();
        input.select();
        const close = () => {
            if (!settled) {
                // Dismissing the dialog is a cancel, never a confirmation.
                settled = true;
                if (onCancel) onCancel();
            }
            shell.hidden = true;
            shell.innerHTML = '';
        };
        bindClose(shell, close);
        shell.addEventListener('click', (event) => {
            if (event.target === shell) close();
        });
        shell.querySelector('form').addEventListener('submit', (event) => {
            event.preventDefault();
            const value = (input.value || '').trim();
            if (!value) return;
            settled = true;
            shell.hidden = true;
            shell.innerHTML = '';
            onConfirm(value);
        });
    }

    // ------------------------------------------------------------------
    // Save As: one flow for every download in the application
    // ------------------------------------------------------------------

    /** Ask for a file name; resolves to the name, or null when cancelled. */
    function promptFileName(title, current) {
        return new Promise((resolve) => {
            askName(title, current || 'file', resolve, () => resolve(null));
        });
    }

    /** Keep the extension the file really has, whatever the reader typed. */
    function withExtension(name, extension) {
        if (!extension) return name;
        return name.toLowerCase().endsWith(extension.toLowerCase()) ? name : name + extension;
    }

    function extensionOf(name) {
        const match = /\.[A-Za-z0-9]{1,10}$/.exec(name || '');
        return match ? match[0] : '';
    }

    async function fetchDownload(url) {
        const response = await fetch(url, {credentials: 'same-origin'});
        if (!response.ok) throw new Error('HTTP ' + response.status);
        return response.blob();
    }

    /**
     * Save a file the reader chose a name for.
     *
     * The reader is asked for the name first (prefilled, editable). Where the
     * browser offers a real Save As dialog (Chromium, Edge) it opens, so the
     * reader also picks the folder; elsewhere the browser downloads the file
     * under the chosen name. `source` is either `{url}` (fetched through the
     * application) or `{blob}` (text the page already holds).
     *
     * Resolves to `{ok, handled, cancelled?}` so the record-action surface can
     * report the outcome and never follow the link a second time.
     */
    async function saveFile(options) {
        const opts = options || {};
        const extension = opts.extension || extensionOf(opts.suggestedName) || '';
        const chosen = await promptFileName(t('saveAs', 'Save As'), opts.suggestedName || 'file');
        if (!chosen) return {ok: true, handled: true, cancelled: true};
        const name = withExtension(chosen.split(/[\\/]/).pop().trim() || 'file', extension);

        try {
            if (typeof window.showSaveFilePicker === 'function') {
                const blob = opts.blob || await fetchDownload(opts.url);
                const mime = blob.type && blob.type !== 'application/octet-stream'
                    ? blob.type : 'application/octet-stream';
                const accept = {};
                accept[mime] = extension ? [extension] : [];
                const handle = await window.showSaveFilePicker({
                    suggestedName: name,
                    types: extension ? [{description: name, accept: accept}] : undefined,
                });
                const writable = await handle.createWritable();
                await writable.write(blob);
                await writable.close();
                toast(t('fileSaved', 'The file was saved.'), 'success');
                return {ok: true, handled: true};
            }
            // No folder picker here: the browser saves under the chosen name.
            const link = document.createElement('a');
            if (opts.blob) {
                link.href = URL.createObjectURL(opts.blob);
                setTimeout(() => URL.revokeObjectURL(link.href), 4000);
            } else {
                const separator = opts.url.indexOf('?') >= 0 ? '&' : '?';
                link.href = opts.url + separator + 'filename=' + encodeURIComponent(name);
            }
            link.download = name;
            document.body.appendChild(link);
            link.click();
            link.remove();
            return {ok: true, handled: true};
        } catch (error) {
            if (error && error.name === 'AbortError') {
                // The reader closed the folder picker: nothing was saved.
                return {ok: true, handled: true, cancelled: true};
            }
            toast(t('downloadFailed', 'The file could not be downloaded.'), 'error');
            return {ok: false, handled: true};
        }
    }

    // The preview's "Open Original" link saves through the same dialog.
    document.addEventListener('click', (event) => {
        const original = event.target.closest && event.target.closest('a[data-original-action="download"]');
        if (original) {
            // "Download original" on the record page: name it, then save.
            event.preventDefault();
            const nameNode = document.querySelector('.original-file-name');
            saveFile({url: original.getAttribute('href'),
                      suggestedName: (nameNode && nameNode.textContent.trim()) || 'file'});
            return;
        }
        const link = event.target.closest && event.target.closest('[data-file-save-url]');
        if (!link) return;
        event.preventDefault();
        saveFile({url: link.getAttribute('data-file-save-url'),
                  suggestedName: link.getAttribute('data-file-save-name') || 'file'});
    });

    // ------------------------------------------------------------------
    // Wiring: one delegate serves every row on every page
    // ------------------------------------------------------------------

    document.addEventListener('click', (event) => {
        const button = event.target.closest('[data-file-menu-button]');
        if (!button) return;
        event.preventDefault();
        const menu = document.getElementById(MENU_ID);
        if (menu && !menu.hidden && activeFile
            && activeFile.id === button.getAttribute('data-file-id')) {
            closeMenu();
            return;
        }
        openMenu(button);
    });

    // Re-export for a page that wants to offer a single action directly.
    window.FileActions = {
        saveFile,
        openPreview,
        openSplit,
        closeSplit,
        closeMenu,
    };
})();
