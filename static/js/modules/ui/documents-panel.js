/**
 * Documents panel — the one side panel of one owner's documents.
 *
 * The File Types dashboard, the Keywords page and the Categories page all
 * open the same panel: a slice of the File Library pinned to one owner (a
 * detected format, a keyword, a category), rendered by the shared file
 * library table and fetched as a fragment. This module owns the panel's
 * behaviour — open, close, search, sort, Load More, adoption — so no page
 * reimplements it.
 *
 * A page declares the openers: any element with `data-panel-open` carries
 * `data-panel-url` (the fragment endpoint) and, optionally,
 * `data-panel-label` (the heading). The module wires the rest:
 *
 *     DocumentsPanel.wire({ panelId: 'documentsPanel', tableId: 'panelFilesTable' });
 *
 * Sorting inside the panel re-asks the fragment (the panel's sort is its
 * own request; the page URL is not rewritten); Load More is handed the
 * adoption through UnifiedTable, so selections and scroll survive.
 */
(function (global) {
    'use strict';

    const state = {
        panelId: null,
        tableId: null,
        limit: 25,
        base: '',
        label: '',
        search: '',
        sort: '',
        order: '',
        page: 1,
        total: 0,
        loadingMore: false,
        searchTimer: null,
    };

    function el(selector) { return document.querySelector(selector); }

    function messages() {
        const node = document.getElementById('documents-panel-data');
        try {
            return (JSON.parse(node?.textContent || '{}').translations) || {};
        } catch (_error) {
            return {};
        }
    }

    function notify(kind, message, detail) {
        const toast = global.Toast;
        const options = detail ? { detail } : undefined;
        if (toast && typeof toast[kind] === 'function') {
            toast[kind](message, options);
            return;
        }
        const fallback = kind === 'error' ? global.showError : global.showSuccess;
        if (typeof fallback === 'function') fallback(message);
    }

    function buildUrl(pageOverride) {
        const params = new URLSearchParams();
        if (state.search) params.set('search', state.search);
        if (state.sort) params.set('sort', state.sort);
        if (state.order) params.set('order', state.order);
        params.set('limit', String(state.limit));
        params.set('page', String(pageOverride !== undefined ? pageOverride : state.page));
        const joiner = state.base.indexOf('?') >= 0 ? '&' : '?';
        return state.base + joiner + params.toString();
    }

    async function fetchFragment(url) {
        const response = await fetch(url, {
            credentials: 'same-origin',
            headers: { 'X-Requested-With': 'fetch' },
        });
        if (!response.ok) throw new Error('HTTP ' + response.status);
        return response.text();
    }

    function parseTotal(text) {
        return parseInt(String(text || '').replace(/[^0-9]/g, ''), 10) || 0;
    }

    function adoptFragment(html) {
        const body = el('[data-panel-body]');
        if (!body) return;
        body.innerHTML = html;
        // The fragment is a full unified table; the module enhances whatever
        // it finds inside the panel.
        if (global.UnifiedTable && typeof global.UnifiedTable.init === 'function') {
            global.UnifiedTable.init(body);
        }
        global.DocumentsPanelExports?.sync(body.closest('.documents-panel'));
        const total = body.querySelector('[data-ut-total]');
        if (total) state.total = parseTotal(total.textContent);
    }

    async function reload() {
        if (!state.base) return;
        state.page = 1;
        try {
            const html = await fetchFragment(buildUrl(1));
            adoptFragment(html);
            const sub = el('[data-panel-sub]');
            if (sub) {
                sub.textContent = state.search
                    ? state.total + ' ' + (messages().panelMatches || 'document(s) matching "' + state.search + '"')
                    : state.total + ' ' + (messages().panelDocuments || 'document(s)');
            }
        } catch (error) {
            console.error('Documents panel reload failed:', error);
            notify('error', messages().panelFailed || 'Could not load the documents.', error.message);
        }
    }

    async function loadMore(table) {
        if (state.loadingMore) return;   // a double click must not ask twice
        state.loadingMore = true;
        try {
            const html = await fetchFragment(buildUrl(state.page + 1));
            const doc = new DOMParser().parseFromString(html, 'text/html');
            const incoming = doc.querySelector('table#' + state.tableId + ' tbody');
            if (!incoming || !incoming.rows.length) return;
            state.page += 1;
            const total = doc.querySelector('[data-ut-total]');
            if (total) state.total = parseTotal(total.textContent);
            global.UnifiedTable.appendRows(state.tableId, {
                html: incoming.innerHTML,
                total: state.total,
            });
            global.DocumentsPanelExports?.sync(el('#' + state.panelId));
            const sub = el('[data-panel-sub]');
            if (sub) {
                sub.textContent = state.search
                    ? state.total + ' ' + (messages().panelMatches || 'document(s) matching "' + state.search + '"')
                    : state.total + ' ' + (messages().panelDocuments || 'document(s)');
            }
        } catch (error) {
            console.error('Documents panel load-more failed:', error);
            const loadError = table?.querySelector('[data-ut-load-more] [data-ut-load-error]');
            if (loadError) loadError.hidden = false;
            notify('error', messages().panelLoadFailed || 'Could not load more documents.', error.message);
        } finally {
            state.loadingMore = false;
        }
    }

    function open(url, label) {
        state.base = url;
        state.label = label || '';
        state.search = '';
        state.sort = '';
        state.order = '';
        state.page = 1;
        state.total = 0;
        const aside = document.getElementById(state.panelId);
        const search = el('[data-panel-search]');
        if (search) search.value = '';
        const title = el('[data-panel-title]');
        if (title) title.textContent = state.label || (messages().panelTitle || 'Documents');
        const sub = el('[data-panel-sub]');
        if (sub) sub.textContent = '';
        const body = el('[data-panel-body]');
        if (body) {
            body.innerHTML = '<div class="file-type-panel-placeholder">'
                + '<div class="spinner-border" role="status"></div></div>';
        }
        global.DocumentsPanelExports?.sync(aside);
        if (aside) aside.hidden = false;
        document.body.classList.add('documents-panel-open');
        reload();
    }

    function close() {
        const aside = document.getElementById(state.panelId);
        if (aside) aside.hidden = true;
        document.body.classList.remove('documents-panel-open');
        state.base = '';
    }

    function wire(options) {
        state.panelId = (options && options.panelId) || 'documentsPanel';
        state.tableId = (options && options.tableId) || 'panelFilesTable';
        if (options && options.limit) state.limit = options.limit;

        document.addEventListener('click', (event) => {
            const opener = event.target.closest('[data-panel-open]');
            if (opener) {
                if (event.target.closest('[data-export-names], [data-on-click]')) return;
                event.preventDefault();
                open(opener.getAttribute('data-panel-url'),
                     opener.getAttribute('data-panel-label'));
                return;
            }
            if (event.target.closest('[data-panel-close]')) close();
        });

        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' && state.base) close();
            const row = event.target.closest?.('[data-panel-open][role="button"]');
            if (row && (event.key === 'Enter' || event.key === ' ')) {
                event.preventDefault();
                open(row.getAttribute('data-panel-url'),
                     row.getAttribute('data-panel-label'));
            }
        });

        const search = el('[data-panel-search]');
        if (search) {
            search.addEventListener('input', () => {
                clearTimeout(state.searchTimer);
                state.searchTimer = setTimeout(() => {
                    state.search = search.value.trim();
                    reload();
                }, 300);
            });
        }

        if (global.UnifiedTable) {
            if (typeof global.UnifiedTable.onSort === 'function') {
                global.UnifiedTable.onSort(state.tableId, (key, direction) => {
                    state.sort = direction ? key : '';
                    state.order = direction || '';
                    reload();
                });
            }
            if (typeof global.UnifiedTable.onLoadMore === 'function') {
                global.UnifiedTable.onLoadMore(state.tableId, loadMore);
            }
        }
    }

    global.DocumentsPanel = { wire, open, close };
}(window));
