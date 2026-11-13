/**
 * LEXIS Cosmos - the galaxy browser (/lexis).
 *
 * WHAT THIS FILE IS
 * -----------------
 * The whole interactive surface of the interface: the canvas (root -> lens ->
 * category -> subcategory -> term -> file), the search sidebar with its facets
 * and two pagination modes, the document viewer, the exports and the Manage
 * sheet. It is a plain script (no framework, classic script tag) because the
 * application ships one runtime and no bundler.
 *
 * WHAT IT DELIBERATELY DOES NOT DO
 * --------------------------------
 * * No inline script and no inline event handler; every listener is attached
 *   here, so the Content-Security-Policy keeps `script-src 'self'`.
 * * No SQL and no knowledge of table names: every number comes from a JSON
 *   endpoint registered in Api/routes/lexis_cosmos.py.
 * * No hard-coded English in the UI: strings pass through `window.t()` (the
 *   application's I18N runtime, which merges the server catalogs) so a new
 *   string is translatable by adding it to the catalogs, not by editing this
 *   file again.
 *
 * DATA SHAPE
 * ----------
 * Nodes are addressed exactly as the API addresses them:
 *
 *     root | lens:<lens> | cat:<lens>:<id> | sub:<lens>:<id>
 *     term:(w|k)_<id> | file:<path_id>
 *
 * Every request carries the same filter envelope
 * (`file_types`, `sources`, `sides`, `date_from`, `date_to`, `q`), which is why
 * the tree, the list, the facets and the exports can never disagree about what
 * is in scope.
 */
(function () {
    'use strict';

    var RTL = ['ar', 'fa', 'he', 'ur'].indexOf(
        (document.documentElement.getAttribute('lang') || 'en').split('-')[0]) !== -1;
    var t = function (text) {
        if (typeof window.t === 'function') {
            try { return window.t(text); } catch (e) { /* fall through */ }
        }
        return text;
    };

    var TAG = '[LEXIS]';
    var CARD_W = 190;
    var CARD_H = 66;
    var FILE_H = 50;
    var GAP = 18;
    var ROW = 124;
    var PAD = 40;
    var FGAP = 8;
    var PAGE_SIZES = [25, 50, 100, 200];
    var PALETTE = ['#ffc857', '#7d9bff', '#ff7eb9', '#ff6b6b', '#3ddc97',
        '#4fd1ff', '#b084ff', '#ff9f43', '#9be564', '#f0f08a'];
    var ACCENT = { words: '#2dd4bf', keywords: '#a78bfa' };
    var TYPE_COL = {
        PDF: '#ff6b6b', DOCX: '#5b9dff', XLSX: '#3ddc97', PPTX: '#ff9f43',
        TXT: '#c9cfe9', EML: '#ffc857', CSV: '#9be564', MP4: '#4fd1ff'
    };
    var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
        'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

    /* ==================================================================
       Boot: page data
       ================================================================== */

    var PAGE = {};
    var URLS = {};
    try {
        PAGE = JSON.parse(document.getElementById('lexis-page-data').textContent);
        URLS = PAGE.urls || {};
    } catch (e) {
        console.error(TAG, 'page data is missing', e);
    }

    var $ = function (selector, root) { return (root || document).querySelector(selector); };
    var $$ = function (selector, root) {
        return Array.prototype.slice.call((root || document).querySelectorAll(selector));
    };

    function esc(value) {
        return String(value === null || value === undefined ? '' : value)
            .replace(/[&<>"']/g, function (ch) {
                return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
            });
    }

    function fmtDate(value) {
        if (!value) { return '—'; }
        var parts = String(value).slice(0, 10).split('-');
        if (parts.length !== 3) { return String(value); }
        return parts[2] + ' ' + (MONTHS[Number(parts[1]) - 1] || parts[1]) + ' ' + parts[0];
    }

    function fmtSize(bytes) {
        var value = Number(bytes || 0);
        if (!value) { return '0 B'; }
        var units = ['B', 'KB', 'MB', 'GB', 'TB'];
        var index = Math.floor(Math.log(value) / Math.log(1024));
        index = Math.max(0, Math.min(index, units.length - 1));
        return (value / Math.pow(1024, index)).toFixed(index ? 1 : 0) + ' ' + units[index];
    }

    var toastTimer = null;
    function toast(message) {
        var el = $('#lexisToast');
        if (!el) { return; }
        el.textContent = message;
        el.classList.add('show');
        clearTimeout(toastTimer);
        toastTimer = setTimeout(function () { el.classList.remove('show'); }, 2600);
    }

    /* ==================================================================
       API client
       ================================================================== */

    function withId(template, id) {
        // Every id-taking endpoint is rendered by the template with a 0
        // placeholder; the id is substituted at the end of the path.
        return String(template).replace(/0(\/|$)/, String(id) + '$1');
    }

    function csrfToken() {
        if (window.CSRF && typeof window.CSRF.getToken === 'function') {
            return window.CSRF.getToken();
        }
        var meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute('content') : '';
    }

    function api(url, options) {
        options = options || {};
        var init = {
            method: options.method || 'GET',
            credentials: 'same-origin',
            cache: 'no-store',
            headers: { 'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest' }
        };
        if (init.method !== 'GET') {
            init.headers['Content-Type'] = 'application/json';
            init.headers['X-CSRFToken'] = csrfToken();
            init.body = JSON.stringify(options.body || {});
        } else if (options.body) {
            init.method = 'POST';
            init.headers['Content-Type'] = 'application/json';
            init.headers['X-CSRFToken'] = csrfToken();
            init.body = JSON.stringify(options.body);
        }
        return fetch(url, init).then(function (response) {
            return response.json().catch(function () { return {}; }).then(function (data) {
                if (!response.ok || data.success === false) {
                    var message = data.error || (t('Request failed') + ' (' + response.status + ')');
                    throw new Error(typeof message === 'string' ? message : t('Request failed'));
                }
                return data;
            });
        });
    }

    /* ==================================================================
       State
       ================================================================== */

    var S = {
        focus: 'root',
        expanded: new Set(['root', 'lens:words']),
        hideEmpty: true,
        basket: new Set(),
        sel: new Set(),
        anchor: null,
        filters: { file_types: [], sources: [], sides: [], date_from: '', date_to: '', q: '' },
        sort: { key: 'hits', dir: 'desc' },
        page: 1,
        size: 50,
        total: 0,
        lenses: [],
        files: [],
        cursor: null,
        scope: null,
        facets: null,
        options: { file_types: [], sources: [], sides: [] },
        views: [],
        health: {},
        depth: 2,
        zoom: 1,
        panelWidth: 440,
        panelVisible: true,
        filtersVisible: true,
        manageTab: 'overview',
        undo: [],
        redo: [],
        lastRow: null
    };

    var TREE = new Map();      // node -> {children, error}
    var LOADING = new Set();

    function filterPayload() {
        return {
            file_types: S.filters.file_types.length ? S.filters.file_types : null,
            sources: S.filters.sources.length ? S.filters.sources : null,
            sides: S.filters.sides.length ? S.filters.sides : null,
            date_from: S.filters.date_from || null,
            date_to: S.filters.date_to || null,
            q: S.filters.q || null
        };
    }

    function searchPayload() {
        var payload = filterPayload();
        payload.basket = Array.from(S.basket);
        payload.focus = S.focus;
        payload.hide_empty = S.hideEmpty;
        payload.sort_key = S.sort.key;
        payload.sort_dir = S.sort.dir;
        payload.size = S.size;
        payload.page = S.page;
        return payload;
    }

    function invalidateTree() { TREE.clear(); }

    /* ==================================================================
       Node helpers
       ================================================================== */

    function nodeKind(id) {
        if (id === 'root') { return 'root'; }
        return String(id).split(':')[0];
    }

    function nodeLens(id) {
        var parts = String(id).split(':');
        if (parts[0] === 'lens') { return parts[1]; }
        if (parts[0] === 'cat' || parts[0] === 'sub') { return parts[1]; }
        if (parts[0] === 'term') { return parts[1][0] === 'w' ? 'words' : 'keywords'; }
        return null;
    }

    function nodeTermId(id) { return Number(String(id).split(':')[1].slice(2)); }
    function nodeCategoryId(id) { return Number(String(id).split(':')[2]); }
    function nodePathId(id) { return Number(String(id).split(':')[1]); }

    function pathIds(id) {
        if (!id || id === 'root') { return ['root']; }
        var kind = nodeKind(id);
        var lens = nodeLens(id);
        if (kind === 'lens') { return ['root', id]; }
        if (kind === 'cat') { return ['root', 'lens:' + lens, id]; }
        if (kind === 'sub') { return ['root', 'lens:' + lens, 'cat:' + lens + ':?', id]; }
        if (kind === 'term') {
            return ['root', 'lens:' + lens, 'cat:' + lens + ':?', 'sub:' + lens + ':?', id];
        }
        if (kind === 'file') { return ['root', id]; }
        return ['root'];
    }

    // The two lenses carry the design's own names: the page is a galaxy, and
    // each lens is one galaxy inside it. ``PAGE.lensLabels`` stays as the
    // server-side override so a deploy can rename them without a code change.
    function lensLabel(lens) {
        if (PAGE.lensLabels && PAGE.lensLabels[lens]) { return PAGE.lensLabels[lens]; }
        if (lens === 'words') { return t('Word galaxy'); }
        if (lens === 'keywords') { return t('Keyword galaxy'); }
        return lens;
    }

    var KIND_NAMES = { lens: 'Lens', cat: 'Category', sub: 'Subcategory',
        term: 'Term', file: 'File' };

    function kindLabel(kind) {
        if (PAGE.kinds && PAGE.kinds[kind]) { return PAGE.kinds[kind]; }
        if (KIND_NAMES[kind]) { return t(KIND_NAMES[kind]); }
        return kind;
    }

    function nodeLabel(id, info) {
        var kind = nodeKind(id);
        // A lens is named by the page, not by the payload: the tree sends the
        // machine name ("words") because that is what the node id carries, and
        // trusting it showed "words" in the breadcrumb, on the card and in the
        // sidebar title instead of the galaxy's own name.
        if (kind === 'lens') { return lensLabel(nodeLens(id)); }
        if (info && info.label) {
            return info.label;
        }
        if (id === 'root') { return t('Lexicon'); }
        if (kind === 'file') { return t('File') + ' ' + nodePathId(id); }
        return id;
    }

    function metaOf(id, info) {
        var kind = nodeKind(id);
        if (kind === 'root') {
            var h = S.health || {};
            return (h.total_files || 0) + ' ' + t('files') + ' · ' +
                (h.total_keywords || 0) + ' ' + t('keywords');
        }
        if (kind === 'lens') {
            // The tree names these counts term_count/file_count (Q2.1). Reading
            // ``info.counts`` - which no node carries - printed "0 terms ·
            // 0 files" for a galaxy that had thousands of both.
            var counts = (info && info.counts) || {};
            var terms = info && info.term_count !== undefined ? info.term_count : counts.terms;
            var files = info && info.file_count !== undefined ? info.file_count : counts.files;
            return (terms || 0) + ' ' + t('terms') + ' · ' +
                (files || 0) + ' ' + t('files');
        }
        if (kind === 'cat' || kind === 'sub') {
            var terms = info && info.term_count ? info.term_count : 0;
            var files = info && info.file_count ? info.file_count : 0;
            var subs = info && info.subcategory_count ? info.subcategory_count : 0;
            var line = terms + ' ' + t('terms') + ' · ' + files + ' ' + t('files');
            if (subs) { line = subs + ' ' + t('subcategories') + ' · ' + line; }
            return line;
        }
        if (kind === 'term') {
            return (info && info.file_count ? info.file_count : 0) + ' ' + t('files') +
                ' · ' + (info && info.total_hits ? info.total_hits : 0) + ' ' + t('hits');
        }
        return [(info && info.type) || '', fmtDate(info && info.date),
            (info && info.source) || ''].filter(Boolean).join(' · ');
    }

    function colorOf(id, info) {
        var kind = nodeKind(id);
        if (kind === 'root') { return '#8b7bff'; }
        if (kind === 'lens') { return ACCENT[nodeLens(id)] || '#8a92c2'; }
        if (kind === 'cat' || kind === 'sub') {
            if (info && info.color) { return info.color; }
            return PALETTE[Math.abs(hashCode(nodeLabel(id, info))) % PALETTE.length];
        }
        if (kind === 'term') { return ACCENT[nodeLens(id)] || '#8a92c2'; }
        if (kind === 'file') { return TYPE_COL[info && info.type] || '#7d9bff'; }
        return '#8a92c2';
    }

    function hashCode(text) {
        var hash = 0;
        String(text || '').split('').forEach(function (ch) {
            hash = ((hash << 5) - hash + ch.charCodeAt(0)) | 0;
        });
        return hash;
    }

    /* ==================================================================
       Tree loading and layout
       ================================================================== */

    function treeUrl(node) {
        return URLS.tree + '?node=' + encodeURIComponent(node) +
            '&hide_empty=' + (S.hideEmpty ? '1' : '0') +
            '&filters=' + encodeURIComponent(JSON.stringify(filterPayload()));
    }

    function loadChildren(node) {
        if (TREE.has(node)) {
            return Promise.resolve(TREE.get(node));
        }
        if (LOADING.has(node)) { return Promise.resolve(null); }
        LOADING.add(node);
        return api(treeUrl(node)).then(function (data) {
            TREE.set(node, data);
            LOADING.delete(node);
            return data;
        }).catch(function (error) {
            LOADING.delete(node);
            TREE.set(node, { node: node, children: [], error: error.message });
            toast(error.message);
            return null;
        });
    }

    function childrenOf(node) {
        var entry = TREE.get(node);
        if (!entry || !entry.children) { return []; }
        // Only nodes whose own children have been asked for may appear; the
        // layout is a projection of the cached, expanded structure.
        return entry.children;
    }

    function expandable(node) {
        return nodeKind(node) !== 'file' && !!(TREE.get(node) && TREE.get(node).children.length);
    }

    function infoOf(id) {
        if (id === 'root') {
            return { label: t('LEXIS'), kind: 'root', counts: {} };
        }
        // Search the cached, expanded frame for the node's own record.
        var found = null;
        if (S.lastRow && S.lastRow.node === id) { found = S.lastRow; }
        TREE.forEach(function (entry) {
            (entry.children || []).forEach(function (child) {
                if (child.node === id) { found = child; }
            });
        });
        return found || { node: id, label: nodeLabel(id), kind: nodeKind(id) };
    }

    function kidsShown(id) {
        if (!S.expanded.has(id)) { return []; }
        var kids = childrenOf(id);
        if (!S.hideEmpty) { return kids; }
        // The taxonomy children arrive already filtered: Q2.2-Q2.7 answer with
        // ``hide_empty``. This catches the two lens nodes, whose counts come
        // from the unfiltered Q2.1 summary, so an empty galaxy cannot sit in
        // the structure beside the categories the toggle just removed.
        return kids.filter(function (kid) {
            return kid.file_count === undefined || kid.file_count > 0;
        });
    }

    function isStack(kids) {
        return kids.length > 0 && kids[0].node.indexOf('file:') === 0;
    }

    function widthOf(id, guard) {
        var kids = kidsShown(id);
        if (!kids.length || isStack(kids) || (guard || 0) > 8) { return CARD_W; }
        return Math.max(CARD_W, kids.reduce(function (total, kid) {
            return total + widthOf(kid.node, (guard || 0) + 1);
        }, 0) + GAP * (kids.length - 1));
    }

    function layout() {
        var positions = {};
        var edges = [];
        // A card is addressed by its *path*, not by its node id. One document can
        // hang under two terms at once (it matches both words), and keying the
        // geometry by id meant the second match overwrote the first: one file
        // card was drawn and the other branch pointed at nothing.
        (function place(id, x0, depth, guard, parentKey) {
            var key = parentKey ? parentKey + '>' + id : id;
            var width = widthOf(id, guard);
            var centre = x0 + width / 2;
            positions[key] = { node: id, x: centre, y: depth * ROW, depth: depth, stack: false };
            var kids = kidsShown(id);
            if (!kids.length || guard > 8) { return; }
            if (isStack(kids)) {
                kids.forEach(function (kid, index) {
                    var fileKey = key + '>' + kid.node;
                    positions[fileKey] = {
                        node: kid.node,
                        x: centre,
                        y: (depth + 1) * ROW - 14 + index * (FILE_H + FGAP),
                        depth: depth + 1,
                        stack: true
                    };
                    edges.push({ from: key, to: fileKey, stack: true });
                });
                return;
            }
            var cursor = x0;
            kids.forEach(function (kid) {
                place(kid.node, cursor, depth + 1, guard + 1, key);
                edges.push({ from: key, to: key + '>' + kid.node, stack: false });
                cursor += widthOf(kid.node, guard + 1) + GAP;
            });
        })('root', 0, 0, 0, '');
        return { positions: positions, edges: edges };
    }

    function renderChart() {
        var wrap = $('#lexisChartWrap');
        if (!wrap || !$('#lexisChart')) return;
        var chart = $('#lexisChart');
        var result = layout();
        var positions = result.positions;
        var edges = result.edges;
        var maxX = 0;
        var maxY = 0;
        var deepest = 0;
        Object.keys(positions).forEach(function (key) {
            maxX = Math.max(maxX, positions[key].x + CARD_W / 2);
            maxY = Math.max(maxY, positions[key].y + (positions[key].stack ? FILE_H : CARD_H));
            deepest = Math.max(deepest, positions[key].depth);
        });
        // The Depth selector is a statement about the chart, not a stored
        // preference: expanding a card by hand used to leave it reading "Show 1
        // level" over a five-level tree.
        S.depth = Math.min(5, Math.max(1, deepest));
        var depthSelect = $('#lexisDepth');
        if (depthSelect && depthSelect.value !== String(S.depth)) {
            depthSelect.value = String(S.depth);
        }
        var width = Math.max(maxX + PAD * 2, wrap.clientWidth);
        var height = maxY + PAD * 2;
        chart.style.width = width + 'px';
        chart.style.height = height + 'px';
        chart.innerHTML = '';

        var path = '';
        edges.forEach(function (edge) {
            var parent = positions[edge.from];
            var child = positions[edge.to];
            if (!parent || !child) { return; }
            var px = parent.x + PAD;
            var py = parent.y + PAD + CARD_H;
            var cx = child.x + PAD;
            var cy = child.y + PAD;
            if (edge.stack) {
                var sx = RTL ? px + CARD_W / 2 - 14 : px - CARD_W / 2 + 14;
                path += 'M' + sx + ' ' + py + ' V' + (cy + FILE_H / 2) +
                    ' M' + sx + ' ' + (cy + FILE_H / 2) + ' H' + (cx - CARD_W / 2) + ' ';
            } else {
                path += 'M' + px + ' ' + py + ' V' + ((py + cy) / 2) + ' H' + cx +
                    ' V' + cy + ' ';
            }
        });
        var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('width', String(width));
        svg.setAttribute('height', String(height));
        svg.setAttribute('aria-hidden', 'true');
        var svgPath = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        svgPath.setAttribute('d', path);
        svg.appendChild(svgPath);
        chart.appendChild(svg);

        Object.keys(positions).forEach(function (key) {
            var position = positions[key];
            var id = position.node;
            var info = infoOf(id);
            var kind = nodeKind(id);
            var expanded = S.expanded.has(id);
            var canExpand = expandable(id);
            var card = document.createElement('div');
            card.className = 'lexis-card' + (kind === 'file' ? ' file' : '') +
                (kind === 'root' ? ' root' : '') + (S.focus === id ? ' focus' : '');
            card.dataset.id = id;
            card.dataset.kind = kind;
            card.tabIndex = 0;
            card.setAttribute('role', 'treeitem');
            card.setAttribute('aria-expanded', canExpand ? String(expanded) : 'false');
            card.style.setProperty('--c', colorOf(id, info));
            card.style.left = (position.x + PAD - CARD_W / 2) + 'px';
            card.style.top = (position.y + PAD) + 'px';

            var ops = '';
            if (kind === 'file') {
                var pathId = nodePathId(id);
                ops = '<input type="checkbox" class="cb" data-file-select="' + pathId + '"' +
                    (S.sel.has(String(pathId)) ? ' checked' : '') +
                    ' aria-label="' + esc(t('Select file')) + '">' +
                    '<button type="button" class="lexis-op" data-file-edit="' + pathId +
                    '" title="' + esc(t('Edit file')) + '">&#9998;</button>';
            } else {
                if (canExpand) {
                    ops += '<button type="button" class="lexis-op" data-toggle="' + esc(id) +
                        '" title="' + esc(expanded ? t('Collapse') : t('Expand')) + '">' +
                        (expanded ? '&#9662;' : '&#9656;') + '</button>';
                }
                if (['cat', 'sub', 'term'].indexOf(kind) !== -1) {
                    var inBasket = S.basket.has(id);
                    ops += '<button type="button" class="lexis-op' + (inBasket ? ' on' : '') +
                        '" data-basket="' + esc(id) + '" title="' +
                        esc(inBasket ? t('Remove from basket') : t('Add to basket')) + '">' +
                        (inBasket ? '&#10003;' : '&#8853;') + '</button>';
                    ops += '<button type="button" class="lexis-op" data-edit="' + esc(id) +
                        '" title="' + esc(t('Edit')) + '">&#9998;</button>';
                }
            }

            var kicker = kind === 'file' ? '' :
                '<div class="k">' + esc(kind === 'lens' ? t('Galaxy') : kindLabel(kind)) + '</div>';
            var title = nodeLabel(id, info);
            var meta = metaOf(id, info);
            card.innerHTML = kicker +
                '<div class="t" title="' + esc(title) + '">' + esc(title) + '</div>' +
                '<div class="m">' + esc(meta) + '</div>' +
                '<div class="ops">' + ops + '</div>';
            chart.appendChild(card);

            card.addEventListener('click', function (event) {
                if (event.target.closest('button, input')) { return; }
                focusNode(id, true);
            });
            card.addEventListener('dblclick', function (event) {
                if (event.target.closest('button, input')) { return; }
                if (kind === 'file') { openViewer(nodePathId(id)); }
                else { toggleExpand(id); }
            });
            card.addEventListener('keydown', function (event) {
                if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    focusNode(id, true);
                }
                if (event.key === 'ArrowRight' && canExpand && !expanded) { toggleExpand(id); }
                if (event.key === 'ArrowLeft' && expanded) { toggleExpand(id); }
            });
        });

        applyZoom();
        updateStatus(Object.keys(positions).length);
    }

    function applyZoom() {
        var chart = $('#lexisChart');
        chart.style.transform = 'scale(' + S.zoom + ')';
        $('#lexisZoomReset').textContent = Math.round(S.zoom * 100) + '%';
    }

    function updateStatus(nodeCount) {
        var bar = $('#lexisStatus');
        if (!bar) { return; }
        var h = S.health || {};
        var size = h.database_size_bytes ? fmtSize(h.database_size_bytes) : '—';
        bar.innerHTML =
            '<span>' + esc(t('Structure')) + ': <b>' + (nodeCount || 0) + '</b> ' +
            esc(t('nodes')) + '</span>' +
            '<span>' + esc(t('Files')) + ': <b>' + (h.total_files || 0) + '</b></span>' +
            '<span>' + esc(t('Contents')) + ': <b>' + (h.total_unique_contents || 0) + '</b></span>' +
            '<span>' + esc(t('Categories')) + ': <b>' + (h.total_main_categories || 0) +
            '</b> · ' + (h.total_subcategories || 0) + ' ' + esc(t('subcategories')) + '</span>' +
            '<span>' + esc(t('Keywords')) + ': <b>' + (h.total_keywords || 0) + '</b></span>' +
            '<span>' + esc(t('Database')) + ': <b>' + esc(size) + '</b></span>' +
            '<span class="grow"></span>' +
            '<span>' + esc(t('Zoom')) + ' ' + Math.round(S.zoom * 100) + '%</span>';
    }

    /* ==================================================================
       Focus, expansion, crumbs
       ================================================================== */

    function focusNode(id, expand) {
        if (nodeKind(id) === 'file') {
            // A file is a leaf, not a scope: opening it is the useful action.
            openViewer(nodePathId(id));
            return;
        }
        S.focus = id;
        S.page = 1;
        if (expand && expandable(id)) { S.expanded.add(id); }
        renderCrumbs();
        renderChart();
        refreshPanel();
    }

    function toggleExpand(id) {
        if (S.expanded.has(id)) {
            S.expanded.delete(id);
            renderChart();
            return;
        }
        loadChildren(id).then(function () {
            S.expanded.add(id);
            renderChart();
        });
    }

    function expandToDepth(depth) {
        S.depth = depth;
        var queue = ['root'];
        var level = 1;
        function step() {
            if (level > depth || !queue.length) {
                renderChart();
                return;
            }
            var next = [];
            Promise.all(queue.map(function (node) {
                return loadChildren(node).then(function () {
                    S.expanded.add(node);
                    (childrenOf(node) || []).forEach(function (child) {
                        if (nodeKind(child.node) !== 'file') { next.push(child.node); }
                    });
                });
            })).then(function () {
                queue = next;
                level += 1;
                step();
            });
        }
        step();
    }

    function renderCrumbs() {
        var crumbs = $('#lexisCrumbs');
        crumbs.innerHTML = '';
        var chain = [];
        var id = S.focus;
        var guard = 0;
        while (id && id !== 'root' && guard < 8) {
            chain.unshift(id);
            var kind = nodeKind(id);
            var lens = nodeLens(id);
            if (kind === 'term') { id = 'lens:' + lens; }
            else if (kind === 'sub') { id = 'lens:' + lens; }
            else if (kind === 'cat') { id = 'lens:' + lens; }
            else if (kind === 'lens') { id = 'root'; }
            else { id = 'root'; }
            guard += 1;
        }
        // The intermediate cat/sub nodes are resolved from the tree cache so the
        // trail reads "root > Word galaxy > Finance > Budget > budget".
        var trail = ['root'];
        chain.forEach(function (node) {
            var kind = nodeKind(node);
            var lens = nodeLens(node);
            if (kind === 'sub') {
                var parent = findParentOf(node);
                if (parent && trail.indexOf(parent) === -1) { trail.push(parent); }
            }
            trail.push(node);
        });
        trail.forEach(function (node, index) {
            if (index) {
                var sep = document.createElement('i');
                sep.textContent = '\u203a';
                crumbs.appendChild(sep);
            }
            var button = document.createElement('button');
            button.type = 'button';
            button.textContent = node === 'root' ? t('LEXIS') : nodeLabel(node, infoOf(node));
            if (node === S.focus) { button.classList.add('cur'); button.setAttribute('aria-current', 'page'); }
            button.addEventListener('click', function () { focusNode(node, false); });
            crumbs.appendChild(button);
        });
    }

    function findParentOf(node) {
        var parent = null;
        TREE.forEach(function (entry, key) {
            (entry.children || []).forEach(function (child) {
                if (child.node === node) { parent = key; }
            });
        });
        return parent;
    }

    function updateBasketBadge() {
        $('#lexisBasketN').textContent = String(S.basket.size);
        var counter = $('#lexisCntBasket');
        if (counter) {
            counter.textContent = S.basket.size ? String(S.basket.size) : '';
            counter.classList.toggle('on', S.basket.size > 0);
        }
    }

    function basketChange() {
        updateBasketBadge();
        renderBasketChips();
        S.page = 1;
        refreshPanel();
    }

    function renderBasketChips() {
        var host = $('#lexisBasketChips');
        host.innerHTML = '';
        if (!S.basket.size) {
            host.innerHTML = '<span class="muted">' + esc(t('No terms in the basket.')) + '</span>';
            return;
        }
        S.basket.forEach(function (node) {
            var chip = document.createElement('button');
            chip.type = 'button';
            chip.className = 'lexis-chip on';
            chip.textContent = nodeLabel(node, infoOf(node));
            chip.title = t('Remove from basket');
            chip.addEventListener('click', function () {
                S.basket.delete(node);
                basketChange();
                renderChart();
            });
            host.appendChild(chip);
        });
    }

    function toggleBasket(node) {
        if (S.basket.has(node)) { S.basket.delete(node); }
        else { S.basket.add(node); }
        basketChange();
        renderChart();
    }

    /* ==================================================================
       Toolbar and keyboard
       ================================================================== */

    function setZoom(value) {
        S.zoom = Math.min(2.5, Math.max(0.3, Math.round(value * 100) / 100));
        applyZoom();
        updateStatus($$('#lexisChart .lexis-card').length);
    }

    function togglePanel() {
        S.panelVisible = !S.panelVisible;
        $('#lexisPanel').classList.toggle('hidden', !S.panelVisible);
        renderChart();
    }

    /* ---- the show/hide toggle ------------------------------------------
       "Empty items" is one decision, so it is one piece of state with two
       controls: the canvas button, for whoever is working in the chart, and
       the switch in the sidebar's filter cards, for whoever is working in the
       list. `syncEmptyControls()` is the only code that writes either label,
       so the two can never disagree.
       ------------------------------------------------------------------ */

    function syncEmptyControls() {
        var button = $('#lexisEmptyBtn');
        if (button) {
            button.textContent = t('Empty items') + ': ' + (S.hideEmpty ? t('hidden') : t('shown'));
            button.classList.toggle('on', !S.hideEmpty);
            button.setAttribute('aria-pressed', S.hideEmpty ? 'false' : 'true');
        }
        var toggle = $('#lexisEmptyToggle');
        if (toggle) {
            toggle.checked = S.hideEmpty;
            toggle.setAttribute('aria-checked', S.hideEmpty ? 'true' : 'false');
        }
        var label = $('#lexisEmptyLabel');
        if (label) {
            label.textContent = S.hideEmpty ? t('Hidden') : t('Shown');
        }
    }

    function toggleEmpty() {
        setHideEmpty(!S.hideEmpty);
    }

    function setHideEmpty(value, options) {
        options = options || {};
        var next = value !== false;
        if (next === S.hideEmpty && !options.force) {
            syncEmptyControls();
            return Promise.resolve();
        }
        S.hideEmpty = next;
        syncEmptyControls();
        invalidateTree();
        return reloadStructure().then(function (rescued) {
            if (options.announce === false) { return; }
            if (rescued) {
                // Say where the view went rather than what the switch did: the
                // switch is visible, the jump is not.
                toast(t('The focused item had no files, so the view moved up one level.'));
                return;
            }
            toast(S.hideEmpty ? t('Items with no files are hidden')
                              : t('Items with no files are shown'));
        });
    }

    /** True when *node* is still drawn somewhere in the loaded structure. */
    function nodeIsVisible(node) {
        if (node === 'root') { return true; }
        var found = false;
        TREE.forEach(function (entry) {
            (entry.children || []).forEach(function (child) {
                if (child.node === node) { found = true; }
            });
        });
        return found;
    }

    /** The nearest ancestor that survived the filter (``root`` at the worst). */
    function nearestVisibleAncestor(node) {
        var candidates = [];
        var parent = findParentOf(node);
        if (parent && parent.indexOf('?') === -1) { candidates.push(parent); }
        var lens = nodeLens(node);
        if (lens) { candidates.push('lens:' + lens); }
        candidates.push('root');
        for (var index = 0; index < candidates.length; index += 1) {
            if (nodeIsVisible(candidates[index])) { return candidates[index]; }
        }
        return 'root';
    }

    function reloadStructure() {
        var chain = ['root'];
        S.expanded.forEach(function (node) { chain.push(node); });
        invalidateTree();
        return Promise.all(chain.map(function (node) { return loadChildren(node); }))
            .then(function () {
                // Hiding empty items can hide the node that was in focus. Keeping
                // it would leave the chart pointing at a card that is no longer
                // part of the structure, and the sidebar listing a scope whose
                // category cannot be seen; the view steps up to the nearest
                // ancestor that is still there instead.
                var rescued = null;
                if (S.focus !== 'root' && !nodeIsVisible(S.focus)) {
                    rescued = nearestVisibleAncestor(S.focus);
                    S.focus = rescued;
                    S.page = 1;
                }
                // A node the new envelope emptied is still marked expanded; it
                // would be asked for on every reload from here on, so forget it.
                S.expanded.forEach(function (node) {
                    if (node !== 'root' && !nodeIsVisible(node)) { S.expanded.delete(node); }
                });
                renderChart();
                renderCrumbs();
                refreshPanel();
                return rescued;
            });
    }

    function bindToolbar() {
        $('#lexisZoomIn').addEventListener('click', function () { setZoom(S.zoom + 0.1); });
        $('#lexisZoomOut').addEventListener('click', function () { setZoom(S.zoom - 0.1); });
        $('#lexisZoomReset').addEventListener('click', function () { setZoom(1); });
        $('#lexisZoomFit').addEventListener('click', fitZoom);
        $('#lexisLocate').addEventListener('click', locateFocus);
        $('#lexisDepth').addEventListener('change', function (event) {
            expandToDepth(Number(event.target.value));
        });
        $('#lexisEmptyBtn').addEventListener('click', toggleEmpty);
        $('#lexisExpandOne').addEventListener('click', function () {
            expandToDepth(Math.min(5, S.depth + 1));
            $('#lexisDepth').value = String(Math.min(5, S.depth));
        });
        $('#lexisCollapseOne').addEventListener('click', function () {
            expandToDepth(Math.max(1, S.depth - 1));
            $('#lexisDepth').value = String(Math.max(1, S.depth));
        });
        $('#lexisPanelBtn').addEventListener('click', togglePanel);
        $('#lexisFullscreen').addEventListener('click', function () {
            var app = $('#lexisApp');
            if (!document.fullscreenElement && app.requestFullscreen) {
                app.requestFullscreen().catch(function () { /* refused */ });
            } else if (document.exitFullscreen) {
                document.exitFullscreen().catch(function () { /* refused */ });
            }
        });
        $('#lexisOutlineBtn').addEventListener('click', exportOutline);
        $('#lexisPrintBtn').addEventListener('click', openPrint);

        $('#lexisUndo').addEventListener('click', undo);
        $('#lexisRedo').addEventListener('click', redo);
        $('#lexisDashboardBtn').addEventListener('click', function () { openManage('overview'); });
        $('#lexisManageBtn').addEventListener('click', function () { openManage(null); });
        $('#lexisBasketBtn').addEventListener('click', function () {
            S.basket.clear();
            basketChange();
            renderChart();
        });
        $('#lexisHelpBtn').addEventListener('click', openHelp);

        document.addEventListener('keydown', function (event) {
            var tag = (event.target.tagName || '').toLowerCase();
            var typing = tag === 'input' || tag === 'textarea' || tag === 'select' ||
                event.target.isContentEditable;
            if (typing) {
                if (event.key === 'Escape') { event.target.blur(); }
                return;
            }
            var key = event.key;
            if (key === '/') {
                event.preventDefault();
                $('#lexisFind').focus();
            } else if (key === '+') {
                setZoom(S.zoom + 0.1);
            } else if (key === '-') {
                setZoom(S.zoom - 0.1);
            } else if (key === '0') {
                setZoom(1);
            } else if (key === 'f' || key === 'F') {
                if (event.shiftKey) { $('#lexisFullscreen').click(); }
                else { fitZoom(); }
            } else if (key === 'l' || key === 'L') {
                locateFocus();
            } else if (key === 'b' || key === 'B') {
                togglePanel();
            } else if (key === 'm' || key === 'M') {
                openManage(null);
            } else if (key === 'd' || key === 'D') {
                var next = S.depth >= 5 ? 1 : S.depth + 1;
                $('#lexisDepth').value = String(next);
                expandToDepth(next);
            } else if (key === '?') {
                openHelp();
            } else if (key === 'Escape') {
                closeModals();
            } else if (key.toLowerCase() === 'z' && (event.metaKey || event.ctrlKey)) {
                event.preventDefault();
                if (event.shiftKey) { redo(); } else { undo(); }
            }
        });
    }

    function fitZoom() {
        var wrap = $('#lexisChartWrap');
        var chart = $('#lexisChart');
        var width = parseFloat(chart.style.width) || wrap.clientWidth;
        if (!width) { return; }
        setZoom(Math.min(1, (wrap.clientWidth - 24) / width));
    }

    function locateFocus() {
        var card = $('#lexisChart .lexis-card.focus');
        if (card) { card.scrollIntoView({ block: 'center', inline: 'center', behavior: 'smooth' }); }
    }

    // Exposed for the inline-free delegated renderer: the chart cards attach
    // their own listeners, so nothing needs to be reachable from window except
    // these test hooks.
    window.LEXIS = {
        state: S,
        focus: focusNode,
        reload: reloadStructure
    };

    /* ==================================================================
       Panel: header, filters, facets, list, pager, selection
       ================================================================== */

    var panelRequest = 0;

    function refreshPanel() {
        if (nodeKind(S.focus) === 'file') { return; }
        renderScopeHeader();
        updateBasketBadge();
        renderActiveBar();
        var ticket = ++panelRequest;
        var payload = searchPayload();
        api(URLS.scope + '?focus=' + encodeURIComponent(S.focus), {
            method: 'POST',
            body: { focus: S.focus, basket: Array.from(S.basket) }
        }).then(function (data) {
            if (ticket !== panelRequest) { return null; }
            S.scope = data.scope;
            return Promise.all([
                api(URLS.search, { method: 'POST', body: payload }),
                api(URLS.facets, { method: 'POST', body: payload })
            ]);
        }).then(function (results) {
            if (!results || ticket !== panelRequest) { return; }
            applySearch(results[0]);
            S.facets = results[1].facets;
            renderFacets();
            renderList();
            renderPager();
            renderSelectionBar();
            renderStatusCounts();
        }).catch(function (error) {
            toast(error.message);
            $('#lexisList').innerHTML = '<div class="lexis-empty">' + esc(error.message) + '</div>';
        });
    }

    // Q2.1 - the lens summary is what the galaxy holds right now: two nodes,
    // each with its own term and file count. It is only meaningful at the root,
    // so the strip disappears as soon as a node is focused.
    function loadLenses() {
        api(URLS.lenses).then(function (data) {
            S.lenses = data.lenses || [];
        }).catch(function () {
            S.lenses = [];
        }).then(renderLensStrip);
    }

    function renderLensStrip() {
        var host = $('#lexisLensStrip');
        if (!host) { return; }
        var lenses = (S.lenses || []).filter(function (lens) {
            // Same rule as the structure: an empty galaxy is an empty item.
            return !S.hideEmpty || (lens.file_count || 0) > 0;
        });
        if (!lenses.length || S.basket.size || S.focus !== 'root') {
            host.hidden = true;
            host.innerHTML = '';
            return;
        }
        host.hidden = false;
        host.innerHTML = lenses.map(function (lens) {
            return '<button type="button" class="lexis-lens-chip" data-lens-goto="' +
                esc(lens.node || ('lens:' + lens.lens)) + '">' +
                '<b>' + esc(lensLabel(lens.lens)) + '</b>' +
                '<span>' + (lens.term_count || 0) + ' ' + esc(t('terms')) + ' \u00b7 ' +
                (lens.file_count || 0) + ' ' + esc(t('files')) + '</span></button>';
        }).join('');
        $$('[data-lens-goto]', host).forEach(function (button) {
            button.addEventListener('click', function () {
                focusNode(button.dataset.lensGoto, true);
            });
        });
    }

    function renderScopeHeader() {
        var kicker = $('#lexisKicker');
        var title = $('#lexisScopeTitle');
        var sub = $('#lexisScopeSub');
        if (S.basket.size) {
            kicker.textContent = t('Basket');
            title.textContent = S.basket.size + ' ' + t('terms in the basket');
            sub.textContent = t('The basket overrides the focused node.');
        } else {
            var kind = nodeKind(S.focus);
            var info = infoOf(S.focus);
            // The card for a lens says Galaxy; the sidebar describing the same
            // node should not call it something else.
            kicker.textContent = S.focus === 'root' ? t('Library') :
                (kind === 'lens' ? t('Galaxy') : kindLabel(kind));
            title.textContent = nodeLabel(S.focus, info);
            sub.textContent = S.focus === 'root'
                ? t('Every file that carries a taxonomy term.')
                : metaOf(S.focus, info);
        }
        renderLensStrip();
    }

    function renderStatusCounts() {
        var stats = $('#lexisStats');
        var termCount = S.scope && S.scope.term_count ? S.scope.term_count : 0;
        stats.innerHTML =
            '<span class="lexis-stat"><b>' + (S.total || 0) + '</b>' + esc(t('files')) + '</span>' +
            '<span class="lexis-stat"><b>' + termCount + '</b>' + esc(t('terms')) + '</span>' +
            '<span class="lexis-stat"><b>' + (S.files.length) + '</b>' + esc(t('on this page')) + '</span>';
    }

    function applySearch(data) {
        S.files = data.files || [];
        S.total = data.total === null || data.total === undefined ? S.files.length : data.total;
        S.cursor = data.cursor || null;
        S.scope = data.scope ? Object.assign(S.scope || {}, data.scope) : S.scope;
    }

    function activeFilterEntries() {
        var entries = [];
        S.filters.file_types.forEach(function (value) {
            entries.push({ key: 'file_types', value: value, label: value });
        });
        S.filters.sources.forEach(function (value) {
            entries.push({ key: 'sources', value: value, label: value });
        });
        S.filters.sides.forEach(function (value) {
            entries.push({ key: 'sides', value: value, label: value });
        });
        if (S.filters.date_from) {
            entries.push({ key: 'date_from', value: S.filters.date_from,
                label: t('From') + ' ' + fmtDate(S.filters.date_from) });
        }
        if (S.filters.date_to) {
            entries.push({ key: 'date_to', value: S.filters.date_to,
                label: t('To') + ' ' + fmtDate(S.filters.date_to) });
        }
        if (S.filters.q) {
            entries.push({ key: 'q', value: S.filters.q, label: '"' + S.filters.q + '"' });
        }
        return entries;
    }

    function renderActiveBar() {
        var host = $('#lexisActiveBar');
        var entries = activeFilterEntries();
        host.innerHTML = '';
        if (!entries.length) { return; }
        var label = document.createElement('span');
        label.className = 'lexis-ab-lbl';
        label.textContent = t('Active filters');
        host.appendChild(label);
        entries.forEach(function (entry) {
            var chip = document.createElement('button');
            chip.type = 'button';
            chip.className = 'lexis-achip';
            chip.innerHTML = '<b>' + esc(entry.label) + '</b> &times;';
            chip.title = t('Remove this filter');
            chip.addEventListener('click', function () {
                if (Array.isArray(S.filters[entry.key])) {
                    S.filters[entry.key] = S.filters[entry.key].filter(function (value) {
                        return value !== entry.value;
                    });
                } else {
                    S.filters[entry.key] = '';
                    if (entry.key === 'date_from') { $('#lexisFrom').value = ''; }
                    if (entry.key === 'date_to') { $('#lexisTo').value = ''; }
                    if (entry.key === 'q') { $('#lexisQ').value = ''; }
                }
                renderFilterCounts();
                renderFacets();
                filtersChanged();
            });
            host.appendChild(chip);
        });
        var clear = document.createElement('button');
        clear.type = 'button';
        clear.className = 'lexis-ab-all';
        clear.textContent = t('Clear all');
        clear.addEventListener('click', clearAllFilters);
        host.appendChild(clear);
    }

    function clearAllFilters() {
        S.filters = { file_types: [], sources: [], sides: [], date_from: '', date_to: '', q: '' };
        $('#lexisQ').value = '';
        $('#lexisFrom').value = '';
        $('#lexisTo').value = '';
        renderFilterCounts();
        renderFacets();
        filtersChanged();
    }

    function renderFilterCounts() {
        var counts = {
            types: S.filters.file_types.length,
            sources: S.filters.sources.length,
            sides: S.filters.sides.length,
            dates: (S.filters.date_from ? 1 : 0) + (S.filters.date_to ? 1 : 0)
        };
        Object.keys(counts).forEach(function (key) {
            var el = $('#lexisCnt' + key.charAt(0).toUpperCase() + key.slice(1));
            if (el) {
                el.textContent = counts[key] ? String(counts[key]) : '';
                el.classList.toggle('on', counts[key] > 0);
            }
        });
        var dateCount = $('#lexisCntDates');
        if (dateCount) { dateCount.textContent = counts.dates ? String(counts.dates) : ''; }
        var viewCount = $('#lexisCntViews');
        if (viewCount) { viewCount.textContent = S.views.length ? String(S.views.length) : ''; }
    }

    /* ---- multi-select dropdowns ---- */

    function facetCountFor(key, value) {
        var facets = S.facets || {};
        var source = key === 'types' ? facets.type : key === 'sources' ? facets.source : facets.dim;
        var found = (source || []).filter(function (item) { return item.value === value; })[0];
        return found ? found.count : 0;
    }

    function multiSelect(host, key, values, allLabel) {
        host.innerHTML = '';
        var selected = S.filters[key] || [];
        var trigger = document.createElement('button');
        trigger.type = 'button';
        trigger.className = 'lexis-ms-trigger';
        trigger.setAttribute('aria-expanded', 'false');
        var summary = selected.length ? selected.join(', ') : allLabel;
        trigger.innerHTML = '<span class="lexis-ms-sum' + (selected.length ? '' : ' muted') + '">' +
            esc(summary) + '</span>' +
            (selected.length ? '<span class="lexis-ms-badge">' + selected.length + '</span>' : '') +
            '<span class="lexis-ms-caret">&#9662;</span>';
        host.appendChild(trigger);

        var menu = document.createElement('div');
        menu.className = 'lexis-ms-menu';
        menu.hidden = true;
        host.appendChild(menu);

        var search = document.createElement('input');
        search.type = 'search';
        search.className = 'lexis-ms-search';
        search.placeholder = t('Filter values…');
        search.setAttribute('aria-label', t('Filter values'));
        menu.appendChild(search);

        var actions = document.createElement('div');
        actions.className = 'lexis-ms-actions';
        var all = document.createElement('button');
        all.type = 'button';
        all.textContent = t('Select all');
        var none = document.createElement('button');
        none.type = 'button';
        none.textContent = t('Clear');
        actions.appendChild(all);
        actions.appendChild(none);
        var grow = document.createElement('span');
        grow.className = 'grow';
        actions.appendChild(grow);
        menu.appendChild(actions);

        var options = document.createElement('div');
        options.className = 'lexis-ms-opts';
        menu.appendChild(options);

        values.forEach(function (value) {
            var label = document.createElement('label');
            label.className = 'lexis-ms-opt';
            var count = facetCountFor(key, value);
            if (!count) { label.classList.add('zero'); }
            if (selected.indexOf(value) !== -1) { label.classList.add('on'); }
            var box = document.createElement('input');
            box.type = 'checkbox';
            box.checked = selected.indexOf(value) !== -1;
            box.value = value;
            var text = document.createElement('span');
            text.textContent = value;
            var badge = document.createElement('small');
            badge.textContent = String(count);
            label.appendChild(box);
            label.appendChild(text);
            label.appendChild(badge);
            box.addEventListener('change', function () {
                var list = S.filters[key].slice();
                var index = list.indexOf(value);
                if (box.checked && index === -1) { list.push(value); }
                if (!box.checked && index !== -1) { list.splice(index, 1); }
                S.filters[key] = list;
                renderFilterCounts();
                multiSelect(host, key, values, allLabel);
                filtersChanged();
            });
            options.appendChild(label);
        });

        search.addEventListener('input', function () {
            var needle = search.value.toLowerCase();
            $$('.lexis-ms-opt', options).forEach(function (label) {
                label.hidden = needle && label.textContent.toLowerCase().indexOf(needle) === -1;
            });
        });
        all.addEventListener('click', function () {
            S.filters[key] = values.slice();
            renderFilterCounts();
            multiSelect(host, key, values, allLabel);
            filtersChanged();
        });
        none.addEventListener('click', function () {
            S.filters[key] = [];
            renderFilterCounts();
            multiSelect(host, key, values, allLabel);
            filtersChanged();
        });
        trigger.addEventListener('click', function () {
            menu.hidden = !menu.hidden;
            trigger.classList.toggle('open', !menu.hidden);
            trigger.setAttribute('aria-expanded', menu.hidden ? 'false' : 'true');
            if (!menu.hidden) { search.focus(); }
        });
    }

    function renderFacets() {
        var typeValues = (S.options.file_types || []).slice();
        (S.facets && S.facets.type ? S.facets.type : []).forEach(function (item) {
            if (typeValues.indexOf(item.value) === -1 && item.value) { typeValues.push(item.value); }
        });
        var sourceValues = (S.options.sources || []).map(function (item) { return item.name; });
        (S.facets && S.facets.source ? S.facets.source : []).forEach(function (item) {
            if (sourceValues.indexOf(item.value) === -1 && item.value) { sourceValues.push(item.value); }
        });
        var sideValues = (S.options.sides || []).map(function (item) { return item.name; });
        (S.facets && S.facets.dim ? S.facets.dim : []).forEach(function (item) {
            if (sideValues.indexOf(item.value) === -1 && item.value) { sideValues.push(item.value); }
        });
        multiSelect($('#lexisMsTypes'), 'file_types', typeValues, t('All file types'));
        multiSelect($('#lexisMsSources'), 'sources', sourceValues, t('All sources'));
        multiSelect($('#lexisMsSides'), 'sides', sideValues, t('All sides'));
        renderFilterCounts();
    }

    /* ---- list ---- */

    function typeBadge(type) {
        var color = TYPE_COL[type] || '#c9cfe9';
        return '<span class="lexis-tbadge" style="background:' + color + '">' +
            esc(type || '—') + '</span>';
    }

    function renderList() {
        var host = $('#lexisList');
        host.innerHTML = '';
        if (!S.files.length) {
            host.innerHTML = '<div class="lexis-empty">' +
                esc(t('No files in this scope. Expand the structure or clear the filters.')) + '</div>';
            return;
        }
        S.files.forEach(function (row) {
            var id = String(row.id);
            var selected = S.sel.has(id);
            var wrapper = document.createElement('div');
            wrapper.className = 'lexis-row' + (selected ? ' sel' : '');
            wrapper.dataset.fileId = id;
            wrapper.setAttribute('role', 'option');
            wrapper.setAttribute('aria-selected', selected ? 'true' : 'false');
            var box = '<input type="checkbox" data-select="' + id + '"' +
                (selected ? ' checked' : '') + ' aria-label="' + esc(t('Select file')) + '">';
            var chips = (row.matched || []).slice(0, 3).map(function (label) {
                return '<span class="lexis-mchip" title="' + esc(label) + '">' + esc(label) + '</span>';
            }).join('') + ((row.matched || []).length > 3 ?
                '<span class="lexis-mchip">+' + ((row.matched || []).length - 3) + '</span>' : '');
            wrapper.innerHTML =
                box +
                '<div><div class="lexis-rname" title="' + esc(row.name) + '">' + esc(row.name) + '</div>' +
                '<div class="lexis-rmeta">' + typeBadge(row.type) +
                '<span>' + esc(fmtDate(row.date)) + '</span>' +
                (row.source ? '<span>· ' + esc(row.source) + '</span>' : '') +
                (row.side ? '<span>· ' + esc(row.side) + '</span>' : '') + chips + '</div></div>' +
                '<div class="lexis-rhits">' + (row.hits || 0) + '</div>' +
                '<button type="button" class="lexis-rbtn" data-row-edit="' + id +
                '" title="' + esc(t('Edit file')) + '">&#9998;</button>';
            wrapper.addEventListener('click', function (event) {
                if (event.target.closest('button, input')) { return; }
                if (event.shiftKey) {
                    selectRange(id);
                    return;
                }
                if (event.ctrlKey || event.metaKey) {
                    toggleSelect(id);
                    return;
                }
                openViewer(Number(id));
            });
            host.appendChild(wrapper);
        });
    }

    function renderSelectionBar() {
        $('#lexisSelN').innerHTML = S.sel.size
            ? '<b>' + S.sel.size + '</b> ' + esc(t('selected'))
            : esc(t('No selection'));
        var all = S.files.length > 0 && S.files.every(function (row) {
            return S.sel.has(String(row.id));
        });
        $('#lexisSelToggle').textContent = all ? t('Select none') : t('Select all');
    }

    function toggleSelect(id) {
        if (S.sel.has(id)) { S.sel.delete(id); } else { S.sel.add(id); S.anchor = id; }
        renderList();
        renderSelectionBar();
    }

    function selectRange(id) {
        var ids = S.files.map(function (row) { return String(row.id); });
        var anchor = S.anchor && ids.indexOf(S.anchor) !== -1 ? ids.indexOf(S.anchor) : 0;
        var target = ids.indexOf(id);
        if (target === -1) { return; }
        var start = Math.min(anchor, target);
        var end = Math.max(anchor, target);
        for (var index = start; index <= end; index += 1) { S.sel.add(ids[index]); }
        renderList();
        renderSelectionBar();
    }

    function toggleSelectAll() {
        var ids = S.files.map(function (row) { return String(row.id); });
        var all = ids.length && ids.every(function (id) { return S.sel.has(id); });
        if (all) {
            ids.forEach(function (id) { S.sel.delete(id); });
        } else {
            ids.forEach(function (id) { S.sel.add(id); });
        }
        renderList();
        renderSelectionBar();
    }

    function selectedIds() {
        return Array.from(S.sel).map(Number);
    }

    /* ---- pager ---- */

    function pageCount() {
        return Math.max(1, Math.ceil((S.total || 0) / S.size));
    }

    function goPage(page) {
        S.page = Math.min(Math.max(1, page), pageCount());
        refreshPanel();
    }

    function renderPager() {
        var host = $('#lexisPager');
        var pages = pageCount();
        var from = S.total ? (S.page - 1) * S.size + 1 : 0;
        var to = Math.min(S.page * S.size, S.total || 0);
        var tokens = [];
        for (var page = 1; page <= pages; page += 1) {
            if (page === 1 || page === pages || Math.abs(page - S.page) <= 1) { tokens.push(page); }
            else if (tokens[tokens.length - 1] !== '…') { tokens.push('…'); }
        }
        host.innerHTML =
            '<div class="pg-row">' +
            '<button type="button" data-page="1" title="' + esc(t('First page')) + '">&#171;</button>' +
            '<button type="button" data-page="' + (S.page - 1) + '" title="' + esc(t('Previous page')) + '">&#8249;</button>' +
            '<span class="pg-info">' + esc(t('Showing')) + ' <b>' + from + '–' + to + '</b> ' +
            esc(t('of')) + ' <b>' + (S.total || 0) + '</b></span>' +
            '<span class="grow"></span>' +
            tokens.map(function (token) {
                if (token === '…') { return '<span class="gap">…</span>'; }
                return '<button type="button" class="pnum' + (token === S.page ? ' on' : '') +
                    '" data-page="' + token + '">' + token + '</button>';
            }).join('') +
            '<button type="button" data-page="' + (S.page + 1) + '" title="' + esc(t('Next page')) + '">&#8250;</button>' +
            '<button type="button" data-page="' + pages + '" title="' + esc(t('Last page')) + '">&#187;</button>' +
            '</div>' +
            '<div class="pg-row">' +
            '<label>' + esc(t('Per page')) +
            '<select id="lexisPageSize">' + PAGE_SIZES.map(function (size) {
                return '<option value="' + size + '"' + (size === S.size ? ' selected' : '') + '>' + size + '</option>';
            }).join('') + '</select></label>' +
            '<span class="grow"></span>' +
            (S.total > S.files.length || S.page < pages
                ? '<button type="button" id="lexisShowMore">' + esc(t('Show more')) + ' (+' +
                  Math.min(S.size, (S.total || 0) - S.files.length) + ')</button>'
                : '') +
            '</div>';
        $$('#lexisPager [data-page]').forEach(function (button) {
            button.addEventListener('click', function () {
                goPage(Number(button.dataset.page));
            });
        });
        var sizeSelect = $('#lexisPageSize');
        if (sizeSelect) {
            sizeSelect.addEventListener('change', function () {
                S.size = Number(sizeSelect.value);
                S.page = 1;
                refreshPanel();
            });
        }
        var more = $('#lexisShowMore');
        if (more) {
            more.addEventListener('click', function () {
                if (!S.cursor) { goPage(S.page + 1); return; }
                var payload = searchPayload();
                payload.cursor = S.cursor;
                api(URLS.search, { method: 'POST', body: payload }).then(function (data) {
                    var ids = new Set(S.files.map(function (row) { return row.id; }));
                    (data.files || []).forEach(function (row) {
                        if (!ids.has(row.id)) { S.files.push(row); }
                    });
                    S.cursor = data.cursor;
                    renderList();
                    renderPager();
                    renderStatusCounts();
                }).catch(function (error) { toast(error.message); });
            });
        }
    }

    /* ---- panel bindings ---- */

    function bindPanel() {
        var timer = null;
        var emptyToggle = $('#lexisEmptyToggle');
        if (emptyToggle) {
            emptyToggle.addEventListener('change', function (event) {
                setHideEmpty(!!event.target.checked);
            });
        }
        // The name box filters documents, not terms, so the structure follows
        // it too: a category whose files do not match the name is not part of
        // the answer either.
        $('#lexisQ').addEventListener('input', function (event) {
            S.filters.q = event.target.value;
            clearTimeout(timer);
            timer = setTimeout(filtersChanged, 250);
        });
        $('#lexisFrom').addEventListener('change', function (event) {
            S.filters.date_from = event.target.value;
            filtersChanged();
        });
        $('#lexisTo').addEventListener('change', function (event) {
            S.filters.date_to = event.target.value;
            filtersChanged();
        });
        $('#lexisFiltersBtn').addEventListener('click', function () {
            S.filtersVisible = !S.filtersVisible;
            $('#lexisFilters').hidden = !S.filtersVisible;
            this.textContent = S.filtersVisible ? t('Hide filters') : t('Show filters');
            this.setAttribute('aria-expanded', S.filtersVisible ? 'true' : 'false');
            renderChart();
        });
        $$('[data-card-toggle]').forEach(function (button) {
            button.addEventListener('click', function () {
                var card = button.closest('.lexis-fcard');
                card.classList.toggle('closed');
                button.setAttribute('aria-expanded', card.classList.contains('closed') ? 'false' : 'true');
            });
        });
        $$('[data-clear-card]').forEach(function (button) {
            button.addEventListener('click', function () {
                var key = button.dataset.clearCard;
                if (key === 'types') { S.filters.file_types = []; }
                if (key === 'sources') { S.filters.sources = []; }
                if (key === 'side') { S.filters.sides = []; }
                if (key === 'dates') {
                    S.filters.date_from = '';
                    S.filters.date_to = '';
                    $('#lexisFrom').value = '';
                    $('#lexisTo').value = '';
                }
                if (key === 'basket') {
                    S.basket.clear();
                    basketChange();
                    renderChart();
                    return;
                }
                renderFacets();
                filtersChanged();
            });
        });
        $('#lexisSelToggle').addEventListener('click', toggleSelectAll);
        $('#lexisOpenSelected').addEventListener('click', function () {
            var ids = selectedIds();
            if (!ids.length) {
                if (S.files.length) { openViewer(S.files[0].id); }
                else { toast(t('Nothing to open')); }
                return;
            }
            openViewer(ids[0]);
        });
        $('#lexisExportBtn').addEventListener('click', openExporter);
        $('#lexisActBtn').addEventListener('click', function (event) {
            event.stopPropagation();
            var menu = $('#lexisActMenu');
            menu.hidden = !menu.hidden;
            this.setAttribute('aria-expanded', menu.hidden ? 'false' : 'true');
            if (!menu.hidden) { renderActionsMenu(); }
        });
        document.addEventListener('click', function (event) {
            var menu = $('#lexisActMenu');
            if (menu && !menu.hidden && !event.target.closest('#lexisActMenu, #lexisActBtn')) {
                menu.hidden = true;
                $('#lexisActBtn').setAttribute('aria-expanded', 'false');
            }
        });
        $('#lexisSaveView').addEventListener('click', openSaveView);
        $('#lexisFind').addEventListener('input', debounce(runFind, 220));
        $('#lexisFind').addEventListener('keydown', function (event) {
            if (event.key === 'Escape') { hideFind(); }
        });
        document.addEventListener('click', function (event) {
            if (!event.target.closest('.lexis-find-wrap')) { hideFind(); }
        });
        var resize = $('#lexisResize');
        var dragging = false;
        resize.addEventListener('mousedown', function (event) {
            dragging = true;
            resize.classList.add('drag');
            event.preventDefault();
        });
        document.addEventListener('mousemove', function (event) {
            if (!dragging) { return; }
            var width = RTL ? event.clientX : window.innerWidth - event.clientX;
            S.panelWidth = Math.min(Math.max(320, width), Math.round(window.innerWidth * 0.7));
            $('#lexisPanel').style.setProperty('--pw', S.panelWidth + 'px');
            renderChart();
        });
        document.addEventListener('mouseup', function () {
            if (!dragging) { return; }
            dragging = false;
            resize.classList.remove('drag');
        });
        resize.addEventListener('dblclick', function () {
            S.panelWidth = 440;
            $('#lexisPanel').style.setProperty('--pw', '440px');
            renderChart();
        });
        window.addEventListener('resize', debounce(function () { renderChart(); }, 200));
        // list clicks
        $('#lexisList').addEventListener('click', function (event) {
            var box = event.target.closest('input[data-select]');
            if (box) {
                toggleSelect(box.dataset.select);
                return;
            }
            var edit = event.target.closest('[data-row-edit]');
            if (edit) { openFileEditor(Number(edit.dataset.rowEdit)); }
        });
        // chart card buttons
        $('#lexisChart').addEventListener('click', function (event) {
            var toggle = event.target.closest('[data-toggle]');
            if (toggle) { toggleExpand(toggle.dataset.toggle); return; }
            var basket = event.target.closest('[data-basket]');
            if (basket) { toggleBasket(basket.dataset.basket); return; }
            var edit = event.target.closest('[data-edit]');
            if (edit) { openNodeEditor(edit.dataset.edit); return; }
            var fileEdit = event.target.closest('[data-file-edit]');
            if (fileEdit) { openFileEditor(Number(fileEdit.dataset.fileEdit)); return; }
            var box = event.target.closest('input[data-file-select]');
            if (box) { toggleSelect(box.dataset.fileSelect); }
        });
        // modals: close buttons and backdrop
        $$('[data-close-modal]').forEach(function (button) {
            button.addEventListener('click', function () {
                $('#' + button.dataset.closeModal).classList.remove('open');
            });
        });
        $$('.lexis-modal').forEach(function (modal) {
            modal.addEventListener('click', function (event) {
                if (event.target === modal) { modal.classList.remove('open'); }
            });
        });
    }

    function debounce(fn, wait) {
        var handle = null;
        return function () {
            var args = arguments;
            var self = this;
            clearTimeout(handle);
            handle = setTimeout(function () { fn.apply(self, args); }, wait);
        };
    }

    function closeModals() {
        $$('.lexis-modal.open').forEach(function (modal) { modal.classList.remove('open'); });
    }

    /* ==================================================================
       Header: find, health, options, views
       ================================================================== */

    function loadHealth() {
        api(URLS.health).then(function (data) {
            S.health = data.health || {};
            updateStatus($$('#lexisChart .lexis-card').length);
            renderChart();
            renderScopeHeader();
        }).catch(function (error) { toast(error.message); });
    }

    function loadOptions() {
        api(URLS.filterOptions).then(function (data) {
            S.options = data.options || S.options;
            renderFacets();
        }).catch(function () { renderFacets(); });
    }

    function loadViews() {
        api(URLS.views).then(function (data) {
            S.views = data.views || [];
            renderViews();
        }).catch(function () { renderViews(); });
    }

    function renderViews() {
        var host = $('#lexisViewChips');
        host.innerHTML = '';
        if (!S.views.length) {
            host.innerHTML = '<span class="muted">' + esc(t('No saved views yet.')) + '</span>';
        }
        S.views.forEach(function (view) {
            var chip = document.createElement('button');
            chip.type = 'button';
            chip.className = 'lexis-chip';
            chip.textContent = view.name;
            chip.title = t('Apply this view');
            chip.addEventListener('click', function () { applyView(view); });
            host.appendChild(chip);
        });
        renderFilterCounts();
    }

    /** Every control that changes the filter envelope goes through here.
     *
     * The structure is a projection of the filters - Q2.2-Q2.7 filter their
     * category and subcategory counts through the same envelope as the list -
     * so a filter change has to re-read the tree, not just the list. Calling
     * `refreshPanel()` alone left the cached tree on screen: choosing "PDF"
     * narrowed the file stack to PDFs while every category kept the counts (and
     * the presence) it had before the filter, which is exactly the "categories
     * with no PDFs still appear" complaint.
     */
    function filtersChanged() {
        S.page = 1;
        invalidateTree();
        // Nodes that the new envelope may have emptied are still marked
        // expanded; reloadStructure() asks only for what is still reachable.
        return reloadStructure();
    }

    function applyView(view) {
        var filters = view.filters || {};
        S.focus = filters.focus || 'root';
        S.basket = new Set(filters.basket || []);
        S.hideEmpty = filters.hide_empty !== false;
        syncEmptyControls();
        S.sort.key = filters.sort_key || 'hits';
        S.sort.dir = filters.sort_dir || 'desc';
        S.size = filters.size || 50;
        S.filters = {
            file_types: filters.file_types || [],
            sources: filters.sources || [],
            sides: filters.sides || [],
            date_from: filters.date_from || '',
            date_to: filters.date_to || '',
            q: filters.q || ''
        };
        $('#lexisQ').value = S.filters.q;
        $('#lexisFrom').value = S.filters.date_from;
        $('#lexisTo').value = S.filters.date_to;
        api(withId(URLS.viewTouch, view.id), { method: 'POST', body: {} }).catch(function () {});
        invalidateTree();
        ['root', 'lens:words', 'lens:keywords'].forEach(function (node) { S.expanded.add(node); });
        reloadStructure();
        toast(t('View applied') + ': ' + view.name);
    }

    function openSaveView() {
        openEditor(t('Save view'), [
            { name: 'name', label: t('View name'), value: '' }
        ], function (values) {
            return api(URLS.viewSave, {
                method: 'POST',
                body: {
                    name: values.name,
                    query: S.filters.q,
                    focus: S.focus,
                    basket: Array.from(S.basket),
                    hide_empty: S.hideEmpty,
                    sort_key: S.sort.key,
                    sort_dir: S.sort.dir,
                    size: S.size,
                    file_types: S.filters.file_types,
                    sources: S.filters.sources,
                    sides: S.filters.sides,
                    date_from: S.filters.date_from,
                    date_to: S.filters.date_to,
                    q: S.filters.q
                }
            }).then(function () {
                toast(t('View saved'));
                loadViews();
            });
        });
    }

    function runFind() {
        var value = $('#lexisFind').value.trim();
        if (!value) { hideFind(); return; }
        api(URLS.find + '?q=' + encodeURIComponent(value)).then(function (data) {
            var result = data.result || { words: [], keywords: [] };
            renderFind(result);
        }).catch(function (error) { toast(error.message); });
    }

    function hideFind() {
        var el = $('#lexisFindRes');
        if (!el) return;
        el.classList.remove('open');
        el.innerHTML = '';
    }

    function renderFind(result) {
        var host = $('#lexisFindRes');
        if (!host) return;
        host.innerHTML = '';
        var groups = [
            { key: 'words', label: t('Word terms'), items: result.words || [] },
            { key: 'keywords', label: t('Keyword expressions'), items: result.keywords || [] }
        ];
        var any = false;
        groups.forEach(function (group) {
            if (!group.items.length) { return; }
            any = true;
            var header = document.createElement('div');
            header.className = 'lh';
            header.textContent = group.label;
            host.appendChild(header);
            group.items.forEach(function (item) {
                var button = document.createElement('button');
                button.type = 'button';
                button.setAttribute('role', 'option');
                button.innerHTML = '<span>' + esc(item.label) + '</span><small>' +
                    esc([item.cat_name, item.sub_name].filter(Boolean).join(' \u203a ')) +
                    ' · ' + item.file_count + ' ' + esc(t('files')) + '</small>';
                button.addEventListener('click', function () {
                    hideFind();
                    revealNode(item);
                });
                host.appendChild(button);
            });
        });
        if (!any) {
            host.innerHTML = '<div class="lh">' + esc(t('No matching terms.')) + '</div>';
        }
        host.classList.add('open');
    }

    function revealNode(item) {
        var lens = item.lens;
        var chain = ['root', 'lens:' + lens];
        if (item.cat_id) { chain.push('cat:' + lens + ':' + item.cat_id); }
        if (item.sub_id && item.sub_id !== item.cat_id) { chain.push('sub:' + lens + ':' + item.sub_id); }
        chain.push(item.node);
        var level = 1;
        function step() {
            if (level >= chain.length) {
                S.focus = item.node;
                S.page = 1;
                S.expanded.add(item.node);
                renderCrumbs();
                renderChart();
                refreshPanel();
                var card = $('#lexisChart .lexis-card.focus');
                if (card) { card.scrollIntoView({ block: 'center', inline: 'center', behavior: 'smooth' }); }
                return;
            }
            loadChildren(chain[level - 1]).then(function () {
                S.expanded.add(chain[level - 1]);
                level += 1;
                step();
            });
        }
        step();
    }


    /* ==================================================================
       Generic dialogs
       ================================================================== */

    function openModal(id) {
        $('#' + id).classList.add('open');
    }

    function openEditor(title, fields, onSubmit, options) {
        options = options || {};
        $('#lexisEditorTitle').textContent = title;
        var body = $('#lexisEditorBody');
        body.innerHTML = '';
        var form = document.createElement('form');
        form.noValidate = true;
        fields.forEach(function (field) {
            var wrapper = document.createElement('label');
            wrapper.className = 'lexis-field';
            wrapper.textContent = field.label;
            var input;
            if (field.type === 'textarea') {
                input = document.createElement('textarea');
                input.value = field.value || '';
            } else if (field.type === 'select') {
                input = document.createElement('select');
                (field.options || []).forEach(function (option) {
                    var element = document.createElement('option');
                    element.value = String(option.value);
                    element.textContent = option.label;
                    if (String(option.value) === String(field.value)) { element.selected = true; }
                    input.appendChild(element);
                });
            } else {
                input = document.createElement('input');
                input.type = field.type || 'text';
                input.value = field.value === undefined || field.value === null ? '' : field.value;
            }
            input.name = field.name;
            if (field.placeholder) { input.placeholder = field.placeholder; }
            wrapper.appendChild(input);
            if (field.help) {
                var help = document.createElement('span');
                help.className = 'muted';
                help.textContent = field.help;
                wrapper.appendChild(help);
            }
            form.appendChild(wrapper);
        });
        var error = document.createElement('div');
        error.className = 'err';
        error.setAttribute('role', 'alert');
        form.appendChild(error);
        var actions = document.createElement('div');
        actions.className = 'lexis-vrow';
        var cancel = document.createElement('button');
        cancel.type = 'button';
        cancel.textContent = t('Cancel');
        cancel.addEventListener('click', function () { $('#lexisEditor').classList.remove('open'); });
        var submit = document.createElement('button');
        submit.type = 'submit';
        submit.className = 'primary';
        submit.textContent = options.submitLabel || t('Save');
        actions.appendChild(cancel);
        actions.appendChild(submit);
        form.appendChild(actions);
        form.addEventListener('submit', function (event) {
            event.preventDefault();
            var values = {};
            fields.forEach(function (field) {
                var input = form.querySelector('[name="' + field.name + '"]');
                values[field.name] = input ? input.value : '';
            });
            submit.disabled = true;
            Promise.resolve(onSubmit(values)).then(function (result) {
                submit.disabled = false;
                if (result === false) { return; }
                $('#lexisEditor').classList.remove('open');
            }).catch(function (failure) {
                submit.disabled = false;
                error.textContent = failure.message;
            });
        });
        body.appendChild(form);
        openModal('lexisEditor');
        var first = form.querySelector('input, textarea, select');
        if (first) { first.focus(); }
    }

    function confirmDialog(message, onConfirm, options) {
        options = options || {};
        $('#lexisConfirmTitle').textContent = options.title || t('Please confirm');
        var body = $('#lexisConfirmBody');
        body.innerHTML = '<p>' + esc(message) + '</p>';
        var actions = document.createElement('div');
        actions.className = 'lexis-vrow';
        var cancel = document.createElement('button');
        cancel.type = 'button';
        cancel.textContent = t('Cancel');
        cancel.addEventListener('click', function () {
            $('#lexisConfirm').classList.remove('open');
            if (typeof options.onCancel === 'function') { options.onCancel(); }
        });
        var accept = document.createElement('button');
        accept.type = 'button';
        accept.className = options.danger === false ? 'primary' : 'danger';
        accept.textContent = options.confirmLabel || t('Confirm');
        accept.addEventListener('click', function () {
            accept.disabled = true;
            Promise.resolve(onConfirm()).then(function () {
                accept.disabled = false;
                $('#lexisConfirm').classList.remove('open');
            }).catch(function (error) {
                accept.disabled = false;
                toast(error.message);
            });
        });
        actions.appendChild(cancel);
        actions.appendChild(accept);
        body.appendChild(actions);
        openModal('lexisConfirm');
    }

    /* ==================================================================
       Undo / redo journal

       Only operations with an exact inverse are offered: a rename, a term
       added or removed, a keyword created, edited or deleted, a duplicated
       file. A deletion of files is deliberately NOT undoable (the content
       rows are shared and the operator is asked to confirm instead); the
       journal says so rather than pretending.
       ================================================================== */

    function pushUndo(label, inverse) {
        S.undo.push({ label: label, inverse: inverse });
        S.redo.length = 0;
        renderUndoState();
    }

    function renderUndoState() {
        $('#lexisUndo').disabled = !S.undo.length;
        $('#lexisRedo').disabled = !S.redo.length;
        $('#lexisUndo').title = S.undo.length
            ? t('Undo') + ': ' + S.undo[S.undo.length - 1].label : t('Nothing to undo');
        $('#lexisRedo').title = S.redo.length
            ? t('Redo') + ': ' + S.redo[S.redo.length - 1].label : t('Nothing to redo');
    }

    function undo() {
        if (!S.undo.length) { toast(t('Nothing to undo')); return; }
        var entry = S.undo.pop();
        Promise.resolve(entry.inverse()).then(function () {
            S.redo.push(entry);
            renderUndoState();
            invalidateTree();
            reloadStructure();
            toast(t('Undone') + ': ' + entry.label);
            recordActivity('lexis.undo', null, t('Undo') + ': ' + entry.label);
        }).catch(function (error) { toast(error.message); });
    }

    function redo() {
        if (!S.redo.length) { toast(t('Nothing to redo')); return; }
        var entry = S.redo.pop();
        Promise.resolve(entry.inverse()).then(function () {
            S.undo.push(entry);
            renderUndoState();
            invalidateTree();
            reloadStructure();
            toast(t('Redone') + ': ' + entry.label);
            recordActivity('lexis.redo', null, t('Redo') + ': ' + entry.label);
        }).catch(function (error) { toast(error.message); });
    }

    function recordActivity(action, resource, message) {
        return api(URLS.activityRecord, {
            method: 'POST',
            body: { action: action, resource: resource, message: message }
        }).catch(function () { /* the audit trail is best effort from the client */ });
    }

    /* ==================================================================
       Node and file editors
       ================================================================== */

    function openNodeEditor(node) {
        var kind = nodeKind(node);
        if (kind === 'cat' || kind === 'sub') {
            var info = infoOf(node);
            openEditor(t('Rename category'), [
                { name: 'name', label: t('Name'), value: nodeLabel(node, info) },
                { name: 'color', label: t('Colour (optional)'), value: (info && info.color) || '',
                    placeholder: '#2dd4bf' }
            ], function (values) {
                return api(withId(URLS.categoryUpdate, nodeCategoryId(node)), {
                    method: 'PATCH',
                    body: { name: values.name, color: values.color || null,
                        is_main: kind === 'cat' }
                }).then(function () {
                    var previous = nodeLabel(node, info);
                    pushUndo(t('Rename category'), function () {
                        return api(withId(URLS.categoryUpdate, nodeCategoryId(node)), {
                            method: 'PATCH', body: { name: previous }
                        }).then(afterMutation);
                    });
                    toast(t('Category renamed'));
                    afterMutation();
                });
            });
            return;
        }
        if (kind === 'term') {
            openTermEditor(node);
            return;
        }
        toast(t('This node cannot be edited here.'));
    }

    function afterMutation() {
        invalidateTree();
        loadHealth();
        loadLenses();
        reloadStructure();
    }

    function openTermEditor(node) {
        var lens = nodeLens(node);
        var info = infoOf(node);
        var label = nodeLabel(node, info);
        var categoryId = lens === 'words'
            ? (info && info.subcategory_id) || (info && info.main_category_id)
            : (info && info.subcategory_id) || (info && info.main_category_id);
        var parentCategory = null;
        if (lens === 'words' || lens === 'keywords') {
            // The term's own record carries the category it belongs to; when the
            // card was drawn from a search result that record may be absent, so
            // the parent is resolved from the tree cache.
            parentCategory = findParentOf(node);
            if (parentCategory && parentCategory.indexOf('sub:') !== 0 &&
                parentCategory.indexOf('cat:') !== 0) {
                parentCategory = null;
            }
        }
        var fields = [{ name: 'label', label: t('Term'), value: label }];
        openEditor(t('Edit term'), fields, function (values) {
            if (lens === 'words') {
                var subId = categoryId || (parentCategory ? nodeCategoryId(parentCategory) : 0);
                if (!subId) { throw new Error(t('The subcategory could not be resolved.')); }
                return api(URLS.termUpdate, {
                    method: 'PATCH',
                    body: { word_id: nodeTermId(node), label: values.label, subcategory_id: subId }
                }).then(function () {
                    pushUndo(t('Edit term'), function () {
                        return api(URLS.termUpdate, {
                            method: 'PATCH',
                            body: { word_id: nodeTermId(node), label: label, subcategory_id: subId }
                        }).then(afterMutation);
                    });
                    toast(t('Term updated'));
                    afterMutation();
                });
            }
            var id = nodeTermId(node);
            var subIdK = categoryId || (parentCategory ? nodeCategoryId(parentCategory) : 0);
            if (!subIdK) { throw new Error(t('The subcategory could not be resolved.')); }
            return api(withId(URLS.keywordUpdate, id), {
                method: 'PATCH',
                body: { keyword: values.label, subcategory_id: subIdK }
            }).then(function () {
                pushUndo(t('Edit keyword'), function () {
                    return api(withId(URLS.keywordUpdate, id), {
                        method: 'PATCH', body: { keyword: label, subcategory_id: subIdK }
                    }).then(afterMutation);
                });
                toast(t('Keyword updated'));
                afterMutation();
            });
        });
    }

    function openFileEditor(pathId) {
        var row = (S.files || []).filter(function (item) { return Number(item.id) === Number(pathId); })[0];
        openEditor(t('Edit file'), [
            { name: 'name', label: t('File name'), value: row ? row.name : '' },
            { name: 'path', label: t('Stored path (optional)'), value: '' }
        ], function (values) {
            return api(withId(URLS.rename, pathId), {
                method: 'POST',
                body: { new_file_name: values.name, new_file_path: values.path || null }
            }).then(function (data) {
                var previous = (row && row.name) || '';
                pushUndo(t('Rename file'), function () {
                    return api(withId(URLS.rename, pathId), {
                        method: 'POST', body: { new_file_name: previous }
                    }).then(afterMutation);
                });
                toast(t('File renamed'));
                afterMutation();
                return data;
            });
        });
    }

    function duplicateFile(pathId) {
        api(withId(URLS.duplicate, pathId), { method: 'POST', body: {} }).then(function (data) {
            var created = data.file ? data.file.id : null;
            pushUndo(t('Duplicate file'), function () {
                return api(URLS.bulkDelete, { method: 'POST', body: { path_ids: [created] } })
                    .then(afterMutation);
            });
            toast(t('File duplicated'));
            afterMutation();
        }).catch(function (error) { toast(error.message); });
    }

    /* ==================================================================
       Viewer
       ================================================================== */

    function highlight(text, labels) {
        var output = esc(text || '');
        (labels || []).slice().sort(function (a, b) { return b.length - a.length; })
            .forEach(function (label) {
                if (!label || label.length < 2) { return; }
                var pattern = new RegExp('(' + esc(label).replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'gi');
                output = output.replace(pattern, '<mark>$1</mark>');
            });
        return output;
    }

    function openViewer(pathId) {
        var body = $('#lexisViewerBody');
        $('#lexisViewerTitle').textContent = t('Document');
        body.innerHTML = '<p class="muted">' + esc(t('Loading…')) + '</p>';
        openModal('lexisViewer');
        Promise.all([
            api(withId(URLS.file, pathId)),
            api(withId(URLS.fileText, pathId)),
            // Q5.4 - the file payload carries a summarised match list; the
            // dedicated endpoint returns the same terms with their taxonomy
            // path, which is what "why it matched" is really about.
            api(withId(URLS.fileMatches, pathId)).catch(function () { return {}; })
        ]).then(function (results) {
            var file = results[0].file || {};
            var detailed = (results[2] || {}).matches;
            var matches = (detailed && detailed.length ? detailed : results[0].matches) || [];
            var text = results[1] || {};
            $('#lexisViewerTitle').textContent = file.name || t('Document');
            var labels = matches.map(function (match) { return match.label; });
            var meta = [
                [t('Type'), file.type], [t('Size'), fmtSize(file.size)],
                [t('Date'), fmtDate(file.date)], [t('Status'), file.status],
                [t('Source'), file.source], [t('Side'), file.side],
                [t('Processing'), file.processing_status], [t('Hash'), file.hash]
            ].map(function (pair) {
                return '<div><b>' + esc(pair[1] || '—') + '</b>' + esc(pair[0]) + '</div>';
            }).join('');
            var matchRows = matches.length ? matches.map(function (match) {
                var label = match.node
                    ? '<button type="button" class="linkish" data-goto="' + esc(match.node) + '">' +
                      esc(match.label) + '</button>'
                    : esc(match.label);
                return '<tr><td><span class="lexis-tag ' +
                    (match.lens === 'words' ? 'lexis-tag-w' : 'lexis-tag-k') + '">' +
                    esc(match.lens === 'words' ? t('Word') : t('Keyword')) + '</span></td>' +
                    '<td>' + label + '</td>' +
                    '<td>' + esc([match.cat_name, match.sub_name].filter(Boolean).join(' \u203a ')) + '</td>' +
                    '<td class="num">' + (match.hits || 0) + '</td></tr>';
            }).join('') : '<tr><td colspan="4" class="muted">' + esc(t('No term matched this file.')) + '</td></tr>';

            var index = (S.files || []).map(function (row) { return Number(row.id); }).indexOf(Number(pathId));
            body.innerHTML =
                '<div class="lexis-meta">' + meta + '</div>' +
                '<h4 style="margin:14px 0 6px;font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--mute)">' +
                esc(t('Why it matched')) + '</h4>' +
                '<table class="lexis-table"><thead><tr><th>' + esc(t('Lens')) + '</th><th>' +
                esc(t('Term')) + '</th><th>' + esc(t('Taxonomy path')) + '</th><th class="num">' +
                esc(t('Hits')) + '</th></tr></thead><tbody>' + matchRows + '</tbody></table>' +
                '<h4 style="margin:14px 0 6px;font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--mute)">' +
                esc(t('Text')) + ' <span class="muted">(' + esc(text.source || '—') + ', ' +
                (text.chunks || 0) + ' ' + esc(t('chunks')) + ')</span></h4>' +
                '<div class="lexis-doc" data-i18n-skip>' +
                (text.text ? highlight(text.text, labels) : esc(t('No text was stored for this file.'))) +
                '</div>' +
                '<div class="lexis-vrow">' +
                '<span class="muted">' + esc(file.path || '') + '</span>' +
                '<span>' +
                '<button type="button" id="lexisViewerPrev"' + (index <= 0 ? ' disabled' : '') + '>' +
                esc(t('Previous')) + '</button> ' +
                '<button type="button" id="lexisViewerNext"' +
                (index === -1 || index >= (S.files || []).length - 1 ? ' disabled' : '') + '>' +
                esc(t('Next')) + '</button> ' +
                '<button type="button" id="lexisViewerRename">' + esc(t('Rename')) + '</button> ' +
                '<button type="button" id="lexisViewerDuplicate">' + esc(t('Duplicate')) + '</button>' +
                '</span></div>';
            var prev = $('#lexisViewerPrev');
            if (prev) {
                prev.addEventListener('click', function () {
                    openViewer(Number(S.files[index - 1].id));
                });
            }
            var next = $('#lexisViewerNext');
            if (next) {
                next.addEventListener('click', function () {
                    openViewer(Number(S.files[index + 1].id));
                });
            }
            $('#lexisViewerRename').addEventListener('click', function () {
                openFileEditor(pathId);
            });
            $('#lexisViewerDuplicate').addEventListener('click', function () {
                duplicateFile(pathId);
            });
            // The term in "why it matched" is the way back into the galaxy: the
            // viewer closes and the scope narrows to that term.
            $$('[data-goto]', body).forEach(function (button) {
                button.addEventListener('click', function () {
                    closeModals();
                    focusNode(button.dataset.goto, true);
                });
            });
        }).catch(function (error) {
            body.innerHTML = '<p class="bad">' + esc(error.message) + '</p>';
        });
    }

    /* ==================================================================
       Serialisation and downloads
       ================================================================== */

    function cell(value) {
        if (value === null || value === undefined) { return ''; }
        return String(value);
    }

    function toCSV(columns, rows) {
        var escapeCell = function (value) {
            var text = cell(value);
            return /[",\n\r]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text;
        };
        var head = columns.map(function (column) { return escapeCell(column.label); }).join(',');
        var body = rows.map(function (row) {
            return columns.map(function (column) { return escapeCell(row[column.key]); }).join(',');
        });
        return [head].concat(body).join('\r\n') + '\r\n';
    }

    function toTSV(columns, rows) {
        var clean = function (value) { return cell(value).replace(/\t/g, ' ').replace(/\r?\n/g, ' '); };
        var head = columns.map(function (column) { return clean(column.label); }).join('\t');
        var body = rows.map(function (row) {
            return columns.map(function (column) { return clean(row[column.key]); }).join('\t');
        });
        return [head].concat(body).join('\n') + '\n';
    }

    function toMarkdown(columns, rows) {
        var head = '| ' + columns.map(function (column) { return column.label; }).join(' | ') + ' |';
        var rule = '|' + columns.map(function () { return ' --- '; }).join('|') + '|';
        var body = rows.map(function (row) {
            return '| ' + columns.map(function (column) {
                return cell(row[column.key]).replace(/\|/g, '\\|').replace(/\r?\n/g, ' ');
            }).join(' | ') + ' |';
        });
        return [head, rule].concat(body).join('\n') + '\n';
    }

    function download(filename, text, mime) {
        var blob = new Blob([text], { type: (mime || 'text/plain') + ';charset=utf-8' });
        var url = URL.createObjectURL(blob);
        var link = document.createElement('a');
        link.href = url;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        setTimeout(function () { URL.revokeObjectURL(url); }, 2000);
    }

    var COLUMNS = {
        columns: [
            { key: 'id', label: t('ID') }, { key: 'name', label: t('Name') },
            { key: 'type', label: t('Type') }, { key: 'size', label: t('Size') },
            { key: 'source', label: t('Source') }, { key: 'side', label: t('Side') },
            { key: 'date', label: t('Date') }, { key: 'status', label: t('Status') },
            { key: 'hits', label: t('Hits') }, { key: 'matched', label: t('Matched') },
            { key: 'path', label: t('Taxonomy path') }, { key: 'hash', label: t('Hash') }
        ],
        review: [
            { key: 'file_id', label: t('File ID') }, { key: 'file', label: t('File') },
            { key: 'lens', label: t('Lens') }, { key: 'category', label: t('Category') },
            { key: 'subcategory', label: t('Subcategory') }, { key: 'term_id', label: t('Term ID') },
            { key: 'term', label: t('Term') }, { key: 'hits', label: t('Hits') }
        ],
        structured: [
            { key: 'file_id', label: t('File ID') }, { key: 'name', label: t('Name') },
            { key: 'path', label: t('Path') }, { key: 'type', label: t('Type') },
            { key: 'date', label: t('Date') }, { key: 'year_seg', label: t('Year') },
            { key: 'year_month_seg', label: t('Month') }, { key: 'source', label: t('Source') },
            { key: 'side', label: t('Side') }, { key: 'primary_category', label: t('Category') },
            { key: 'primary_subcategory', label: t('Subcategory') }
        ],
        originals: [
            { key: 'file_id', label: t('File ID') }, { key: 'name', label: t('Name') },
            { key: 'path', label: t('Path') }, { key: 'size', label: t('Size') },
            { key: 'type', label: t('Type') }, { key: 'date', label: t('Date') },
            { key: 'source', label: t('Source') }, { key: 'side', label: t('Side') }
        ],
        text: [
            { key: 'file_id', label: t('File ID') }, { key: 'name', label: t('Name') },
            { key: 'type', label: t('Type') }, { key: 'source', label: t('Source') },
            { key: 'date', label: t('Date') }, { key: 'side', label: t('Side') },
            { key: 'extracted_text', label: t('Extracted text') }
        ]
    };

    var EXPORT_URLS = {
        columns: 'exportColumns',
        review: 'exportReview',
        structured: 'exportStructured',
        originals: 'exportOriginals',
        'originals-zip': 'exportOriginalsZip',
        text: 'exportText'
    };

    function fetchExport(kind, pathIds) {
        var body = {
            path_ids: pathIds && pathIds.length ? pathIds : null,
            focus: S.focus,
            basket: Array.from(S.basket),
            hide_empty: S.hideEmpty,
            file_types: S.filters.file_types,
            sources: S.filters.sources,
            sides: S.filters.sides,
            date_from: S.filters.date_from,
            date_to: S.filters.date_to,
            q: S.filters.q,
            selected_path_ids: pathIds && pathIds.length ? pathIds : null
        };
        return api(URLS[EXPORT_URLS[kind]], { method: 'POST', body: body })
            .then(function (data) { return data.rows || []; });
    }

    function serializeExport(kind, format, rows) {
        var columns = COLUMNS[kind] || COLUMNS.columns;
        if (format === 'json') {
            return JSON.stringify(rows, null, 2);
        }
        if (format === 'tsv') { return toTSV(columns, rows); }
        if (format === 'md') { return toMarkdown(columns, rows); }
        return toCSV(columns, rows);
    }

    /** A file name the reader can recognise: no event objects, no blanks. */
    function exportName(kind, format) {
        var safeKind = /^[a-z]+$/.test(String(kind)) ? kind : 'export';
        var stamp = new Date().toISOString().slice(0, 10);
        var extension = format === 'md' ? 'md' : format === 'json' ? 'json' : format === 'tsv' ? 'tsv' : 'csv';
        return 'lexis-' + safeKind + '-' + stamp + '.' + extension;
    }

    var EXPORT_MIME = {
        csv: 'text/csv', tsv: 'text/tab-separated-values',
        json: 'application/json', md: 'text/markdown'
    };

    /* ==================================================================
       Export modal
       ================================================================== */

    var EXPORT_STATE = { kind: 'columns', format: 'csv', scope: 'view', preview: [], rows: [],
                         loading: false, error: null };

    /** The export choices, in the order the dialog offers them. */
    var EXPORT_KINDS = [
        { id: 'columns', label: t('Files and details'),
          },
        { id: 'review', label: t('Categories, terms and keywords'),
          },
        { id: 'structured', label: t('Structured (category folders)'),
          },
        { id: 'text', label: t('Extracted text'),
          },
        { id: 'originals', label: t('Original paths'),
          },
        { id: 'originals-zip', label: t('Original files') + ' (ZIP)' }
    ];

    function openExporter(kind) {
        // Called from a plain button as well as from a menu, so the first
        // argument can be a click event. Treating it as a kind produced
        // "lexis-[object PointerEvent]-2026-10-27.csv" as the file name.
        EXPORT_STATE.kind = (typeof kind === 'string' && kind) ? kind : 'columns';
        EXPORT_STATE.format = 'csv';
        EXPORT_STATE.scope = S.sel.size ? 'selection' : 'view';
        EXPORT_STATE.rows = [];
        EXPORT_STATE.error = null;
        renderExporter();
        openModal('lexisExporter');
    }

    function exportKindMeta(kind) {
        for (var i = 0; i < EXPORT_KINDS.length; i++) {
            if (EXPORT_KINDS[i].id === kind) { return EXPORT_KINDS[i]; }
        }
        return EXPORT_KINDS[0];
    }

    function renderExporter() {
        var body = $('#lexisExportBody');
        var selectionCount = S.sel.size;
        var isZip = EXPORT_STATE.kind === 'originals-zip';
        body.innerHTML =
            '<div class="lexis-radios" role="radiogroup" aria-label="' + esc(t('What to export')) + '">' +
            EXPORT_KINDS.map(function (kind) {
                return '<label title="' + esc(kind.hint) + '"><input type="radio" name="lexisExportKind" value="' +
                    kind.id + '"' + (EXPORT_STATE.kind === kind.id ? ' checked' : '') + '> ' +
                    esc(kind.label) + '</label>';
            }).join('') + '</div>' +
            '<p class="muted" id="lexisExportHint">' + esc(exportKindMeta(EXPORT_STATE.kind).hint) + '</p>' +
            '<div class="lexis-radios" role="radiogroup" aria-label="' + esc(t('Which files')) + '">' +
            '<label><input type="radio" name="lexisExportScope" value="view"' +
            (EXPORT_STATE.scope === 'view' ? ' checked' : '') + '> ' +
            esc(t('Everything in the current view')) + '</label>' +
            '<label><input type="radio" name="lexisExportScope" value="selection"' +
            (EXPORT_STATE.scope === 'selection' ? ' checked' : '') +
            (selectionCount ? '' : ' disabled') + '> ' +
            esc(t('Only the selection')) + ' (' + selectionCount + ')</label>' +
            '</div>' +
            (isZip ? '' :
            '<div class="lexis-fmt" role="radiogroup" aria-label="' + esc(t('Format')) + '">' +
            [['csv', 'CSV'], ['tsv', 'TSV'], ['json', 'JSON'], ['md', 'Markdown']].map(function (pair) {
                return '<button type="button" class="' + (EXPORT_STATE.format === pair[0] ? 'on' : '') +
                    '" data-format="' + pair[0] + '">' + pair[1] +
                    '<small>' + esc(pair[0] === 'md' ? t('table in Markdown') : pair[0].toUpperCase()) +
                    '</small></button>';
            }).join('') + '</div>') +
            (isZip ? '' : '<pre class="lexis-preview" id="lexisExportPreview">' + esc(t('Loading…')) + '</pre>') +
            '<div class="lexis-vrow"><span class="muted" id="lexisExportCount"></span>' +
            '<span><button type="button" id="lexisExportDownload" class="primary">' +
            esc(t('Download')) + '</button></span></div>';

        $$('[name="lexisExportKind"]', body).forEach(function (input) {
            input.addEventListener('change', function () {
                EXPORT_STATE.kind = input.value;
                renderExporter();
            });
        });
        $$('[name="lexisExportScope"]', body).forEach(function (input) {
            input.addEventListener('change', function () {
                EXPORT_STATE.scope = input.value;
                renderExporter();
            });
        });
        $$('[data-format]', body).forEach(function (button) {
            button.addEventListener('click', function () {
                EXPORT_STATE.format = button.dataset.format;
                renderExporter();
            });
        });
        $('#lexisExportDownload').addEventListener('click', function () {
            if (isZip) { downloadOriginalsZip(); return; }
            if (!EXPORT_STATE.rows.length) {
                toast(t('Nothing to export in this scope.'));
                return;
            }
            var text = serializeExport(EXPORT_STATE.kind, EXPORT_STATE.format, EXPORT_STATE.rows);
            download(exportName(EXPORT_STATE.kind, EXPORT_STATE.format), text,
                EXPORT_MIME[EXPORT_STATE.format] || 'text/plain');
            recordActivity('lexis.export', EXPORT_STATE.kind,
                t('Exported') + ' ' + EXPORT_STATE.rows.length + ' ' + t('rows'));
        });
        if (!isZip) { loadExportPreview(); }
    }

    function exportScopeIds() {
        return EXPORT_STATE.scope === 'selection' ? selectedIds() : null;
    }

    function loadExportPreview() {
        var preview = $('#lexisExportPreview');
        if (!preview) { return; }
        var ids = exportScopeIds();
        preview.textContent = t('Loading…');
        EXPORT_STATE.loading = true;
        fetchExport(EXPORT_STATE.kind, ids).then(function (rows) {
            EXPORT_STATE.loading = false;
            EXPORT_STATE.rows = rows;
            var countEl = $('#lexisExportCount');
            if (countEl) { countEl.textContent = rows.length + ' ' + t('rows'); }
            var downloadBtn = $('#lexisExportDownload');
            if (downloadBtn) { downloadBtn.disabled = rows.length === 0; }
            var text = serializeExport(EXPORT_STATE.kind, EXPORT_STATE.format, rows.slice(0, 12));
            preview.textContent = rows.length
                ? text + (rows.length > 12 ? '\n…' : '')
                : t('Nothing to export in this scope.');
        }).catch(function (error) {
            EXPORT_STATE.loading = false;
            EXPORT_STATE.rows = [];
            var downloadBtn = $('#lexisExportDownload');
            if (downloadBtn) { downloadBtn.disabled = true; }
            preview.textContent = error.message;
        });
    }

    /**
     * The source files themselves, as one ZIP.
     *
     * Fetched rather than posted as a form: a form POST navigates the page when
     * the server answers with JSON (an empty scope, no original bytes on disk),
     * which loses the reader's place and looks like a broken page. Here a
     * refusal is a toast and the reader stays where they were.
     */
    function downloadOriginalsZip() {
        var ids = exportScopeIds();
        var payload = {
            path_ids: ids && ids.length ? ids : null,
            focus: S.focus,
            basket: Array.from(S.basket),
            hide_empty: S.hideEmpty,
            file_types: S.filters.file_types,
            sources: S.filters.sources,
            sides: S.filters.sides,
            date_from: S.filters.date_from,
            date_to: S.filters.date_to,
            q: S.filters.q
        };
        var button = $('#lexisExportDownload');
        var original = button ? button.textContent : '';
        if (button) { button.disabled = true; button.textContent = t('Processing'); }
        toast(t('Processing'));
        fetch(URLS[EXPORT_URLS['originals-zip']], {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
            body: JSON.stringify(payload)
        }).then(function (response) {
            var kind = response.headers.get('Content-Type') || '';
            if (!response.ok || kind.indexOf('application/zip') === -1) {
                return response.json().catch(function () { return {}; }).then(function (data) {
                    throw new Error(data.error || (t('Request failed') + ' (' + response.status + ')'));
                });
            }
            var included = response.headers.get('X-Export-Included');
            var skipped = response.headers.get('X-Export-Skipped') || '0';
            return response.blob().then(function (blob) {
                var url = URL.createObjectURL(blob);
                var link = document.createElement('a');
                link.href = url;
                link.download = 'lexis-originals-' + new Date().toISOString().slice(0, 10) + '.zip';
                document.body.appendChild(link);
                link.click();
                document.body.removeChild(link);
                setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
                toast(t('Exported') + ' ' + (included || '?') + ' ' + t('files') +
                    (skipped !== '0' ? ' · ' + skipped + ' ' + t('Missing') : ''));
                recordActivity('lexis.export', 'originals-zip',
                    t('Archive') + ': ' + (included || '?') + ' ' + t('Files'));
            });
        }).catch(function (error) {
            toast(error.message);
        }).then(function () {
            if (button) { button.disabled = false; button.textContent = original; }
        });
    }

    /* ==================================================================
       Actions menu
       ================================================================== */

    function actionItems() {
        var count = S.sel.size;
        var ids = count ? selectedIds() : [];
        return [
            { label: t('Open in the viewer'), run: function () {
                var target = ids.length ? ids[0] : (S.files[0] ? S.files[0].id : null);
                if (!target) { toast(t('Nothing to open')); return; }
                openViewer(target);
            }, enabled: !!(S.files.length || ids.length) },
            { label: t('Export…'), run: function () { openExporter(); }, enabled: true },
            { group: t('Export the scope') },
            { label: t('Categories, terms and keywords (CSV)'), run: function () {
                runExport('review', 'csv');
            } },
            { label: t('Extracted text bundle'), run: exportTextBundle },
            { label: t('Original paths (CSV)'), run: function () { runExport('originals', 'csv'); } },
            { label: t('Structured export (CSV)'), run: function () {
                runExport('structured', 'csv');
            } },
            { label: t('Files and details (CSV)'), run: function () { runExport('columns', 'csv'); } },
            { label: t('Original files') + ' (ZIP)', run: function () {
                EXPORT_STATE.kind = 'originals-zip';
                EXPORT_STATE.scope = S.sel.size ? 'selection' : 'view';
                downloadOriginalsZip();
            }, enabled: true },
            { label: t('Structure') + ' (JSON)', run: exportOutlineJson },
            { group: t('Change the files') },
            { label: t('Rename file…'), run: function () {
                if (ids.length !== 1) { toast(t('Select exactly one file to rename.')); return; }
                openFileEditor(ids[0]);
            }, enabled: ids.length === 1 },
            { label: t('Duplicate file'), run: function () {
                if (ids.length !== 1) { toast(t('Select exactly one file to duplicate.')); return; }
                duplicateFile(ids[0]);
            }, enabled: ids.length === 1 },
            { label: t('Move to side…'), run: moveToSide, enabled: ids.length > 0 },
            { label: t('Delete files…'), run: deleteSelected, enabled: ids.length > 0 }
        ];
    }

    function renderActionsMenu() {
        var menu = $('#lexisActMenu');
        menu.innerHTML = '';
        actionItems().forEach(function (item) {
            if (item.group) {
                var heading = document.createElement('h5');
                heading.textContent = item.group;
                menu.appendChild(heading);
                return;
            }
            var button = document.createElement('button');
            button.type = 'button';
            button.setAttribute('role', 'menuitem');
            button.textContent = item.label;
            if (item.enabled === false) { button.disabled = true; }
            button.addEventListener('click', function () {
                menu.hidden = true;
                $('#lexisActBtn').setAttribute('aria-expanded', 'false');
                item.run();
            });
            menu.appendChild(button);
        });
    }

    function runExport(kind, format) {
        var ids = S.sel.size ? selectedIds() : null;
        fetchExport(kind, ids).then(function (rows) {
            if (!rows.length) { toast(t('Nothing to export in this scope.')); return; }
            download(exportName(kind, format), serializeExport(kind, format, rows),
                format === 'json' ? 'application/json' : 'text/plain');
            recordActivity('lexis.export', kind, t('Exported') + ' ' + rows.length + ' ' + t('rows'));
            toast(t('Exported') + ' ' + rows.length + ' ' + t('rows'));
        }).catch(function (error) { toast(error.message); });
    }

    function exportTextBundle() {
        var ids = S.sel.size ? selectedIds() : null;
        fetchExport('text', ids).then(function (rows) {
            if (!rows.length) { toast(t('Nothing to export in this scope.')); return; }
            var text = rows.map(function (row) {
                var head = '===== ' + (row.name || '') + ' =====\n' +
                    [row.type, row.date, row.source, row.side].filter(Boolean).join(' · ') + '\n\n';
                return head + (row.extracted_text || '') + '\n';
            }).join('\n');
            download('lexis-text-' + new Date().toISOString().slice(0, 10) + '.txt', text);
            toast(t('Exported') + ' ' + rows.length + ' ' + t('rows'));
        }).catch(function (error) { toast(error.message); });
    }

    function moveToSide() {
        var ids = selectedIds();
        if (!ids.length) { toast(t('Select at least one file.')); return; }
        var sides = (S.options.sides || []).map(function (side) {
            return { value: side.id, label: side.name };
        });
        if (!sides.length) { toast(t('No sides are recorded yet.')); return; }
        openEditor(t('Move to side'), [
            { name: 'side_id', label: t('Side'), type: 'select', value: sides[0].value, options: sides }
        ], function (values) {
            return api(URLS.bulkSide, {
                method: 'POST',
                body: { path_ids: ids, side_id: Number(values.side_id) }
            }).then(function () {
                toast(t('Files moved'));
                S.sel.clear();
                afterMutation();
            });
        }, { submitLabel: t('Apply') });
    }

    function deleteSelected() {
        var ids = selectedIds();
        if (!ids.length) { toast(t('Select at least one file.')); return; }
        confirmDialog(t('Delete the selected files? This cannot be undone.'), function () {
            return api(URLS.bulkDelete, { method: 'POST', body: { path_ids: ids } })
                .then(function () {
                    toast(t('Files deleted'));
                    S.sel.clear();
                    afterMutation();
                });
        }, { confirmLabel: t('Delete'), danger: true });
    }

    /* ==================================================================
       Outline (Markdown), print, and the structure document
       ================================================================== */

    /**
     * One model, three outputs.
     *
     * The outline (Markdown), the print sheet and the JSON outline all read the
     * same structure: the taxonomy rows the server returns, grouped by lens,
     * category and subcategory, with the totals the reader needs to check the
     * document against the screen. Building it once is what keeps them from
     * disagreeing - and a document that disagrees with the app is worse than no
     * document.
     *
     * A taxonomy with no terms yet is a legitimate state: the sheet then says
     * so (with the file count from the health endpoint) instead of producing a
     * blank page. That is what the old print sheet did.
     */
    function structureModel(callback) {
        api(URLS.outline).then(function (data) {
            var rows = data.outline || [];
            var lenses = {};
            var totals = { terms: 0, files: 0, categories: {}, subcategories: {} };
            rows.forEach(function (row) {
                var lens = row.lens || 'words';
                if (!lenses[lens]) { lenses[lens] = { lens: lens, label: lensLabel(lens), rows: [], files: 0 }; }
                lenses[lens].rows.push(row);
                lenses[lens].files += Number(row.file_count || 0) || 0;
                totals.terms += 1;
                if (row.main_category_name && !totals.categories[row.main_category_name]) {
                    totals.categories[row.main_category_name] = true;
                }
                if (row.subcategory_name) {
                    var key = (row.main_category_name || '') + ' / ' + row.subcategory_name;
                    totals.subcategories[key] = true;
                }
            });
            var health = S.health || {};
            totals.files = Number(health.total_files || 0);
            var model = {
                generated: new Date(),
                scope: scopeLabel(),
                focus: S.focus,
                filters: activeFilterSummary(),
                lenses: Object.keys(lenses).sort().map(function (key) { return lenses[key]; }),
                totals: {
                    terms: totals.terms,
                    files: totals.files,
                    categories: Object.keys(totals.categories).length,
                    subcategories: Object.keys(totals.subcategories).length,
                    keywords: Number(health.total_keywords || 0),
                    words: Number(health.total_categorized_words || 0),
                    database_bytes: Number(health.database_size_bytes || 0)
                },
                files: (S.files || []).slice()
            };
            callback(model);
        }).catch(function (error) { toast(error.message); });
    }

    function scopeLabel() {
        if (S.basket.size) { return t('Basket') + ' (' + S.basket.size + ')'; }
        if (!S.focus || S.focus === 'root') { return t('Library'); }
        var info = infoOf(S.focus);
        return (info && (info.label || info.name)) || S.focus;
    }

    function activeFilterSummary() {
        var parts = [];
        if (S.filters.q) { parts.push(t('Search') + ': ' + S.filters.q); }
        if (S.filters.file_types && S.filters.file_types.length) {
            parts.push(t('File type') + ': ' + S.filters.file_types.join(', '));
        }
        if (S.filters.sources && S.filters.sources.length) {
            parts.push(t('Source') + ': ' + S.filters.sources.join(', '));
        }
        if (S.filters.sides && S.filters.sides.length) {
            parts.push(t('Side') + ': ' + S.filters.sides.join(', '));
        }
        if (S.filters.date_from || S.filters.date_to) {
            parts.push(t('Date') + ': ' + (S.filters.date_from || '…') + ' – ' + (S.filters.date_to || '…'));
        }
        if (!S.hideEmpty) { parts.push(t('Empty items') + ': ' + t('Shown')); }
        return parts;
    }

    function formatBytes(bytes) {
        var value = Number(bytes || 0);
        if (!value) { return '0 B'; }
        var units = ['B', 'KB', 'MB', 'GB', 'TB'];
        var index = Math.min(units.length - 1, Math.floor(Math.log(value) / Math.log(1024)));
        return (value / Math.pow(1024, index)).toFixed(index ? 1 : 0) + ' ' + units[index];
    }

    function structureMarkdown(model) {
        var lines = ['# ' + t('LEXIS taxonomy'), ''];
        lines.push('- ' + t('Scope') + ': ' + model.scope);
        if (model.filters.length) { lines.push('- ' + t('Filters') + ': ' + model.filters.join(' · ')); }
        lines.push('- ' + t('Generated') + ': ' +
            model.generated.toISOString().slice(0, 16).replace('T', ' '));
        lines.push('- ' + t('Total') + ': ' + model.totals.files + ' ' + t('files') + ' · ' +
            model.totals.terms + ' ' + t('terms') + ' · ' +
            model.totals.categories + ' ' + t('categories') + ' · ' +
            model.totals.subcategories + ' ' + t('subcategories') + ' · ' +
            formatBytes(model.totals.database_bytes));
        lines.push('');

        if (!model.lenses.length) {
            lines.push('> ' + t('Empty items') + ': ' + t('None'),
                '');
        }
        model.lenses.forEach(function (lens) {
            lines.push('', '## ' + lens.label + ' — ' + lens.files + ' ' + t('files'), '');
            var currentCat = null;
            var currentSub = null;
            lens.rows.forEach(function (row) {
                if (row.main_category_name !== currentCat) {
                    currentCat = row.main_category_name;
                    currentSub = null;
                    lines.push('', '### ' + (currentCat || t('Uncategorised')), '');
                }
                if ((row.subcategory_name || '') !== (currentSub || '')) {
                    currentSub = row.subcategory_name || '';
                    lines.push('', '#### ' + (currentSub || t('Direct terms')), '');
                }
                lines.push('- ' + (row.term_label || '') + ' — ' + row.file_count + ' ' + t('files'));
            });
        });

        if (model.files.length) {
            lines.push('', '## ' + t('Files') + ' (' + model.files.length + ')', '');
            lines.push('| ' + t('File') + ' | ' + t('Type') + ' | ' + t('Date') + ' | ' +
                t('Source') + ' | ' + t('Side') + ' | ' + t('Hits') + ' |');
            lines.push('| --- | --- | --- | --- | --- | --- |');
            model.files.forEach(function (file) {
                lines.push('| ' + [file.name, file.type || file.file_type, fmtDate(file.date),
                    file.source, file.side, file.hits].map(function (value) {
                    return String(value === null || value === undefined ? '' : value)
                        .replace(/\|/g, '\\|');
                }).join(' | ') + ' |');
            });
        }
        return lines.join('\n') + '\n';
    }

    function exportOutline() {
        structureModel(function (model) {
            download('lexis-outline-' + model.generated.toISOString().slice(0, 10) + '.md',
                structureMarkdown(model), 'text/markdown');
            toast(t('Outline downloaded'));
            recordActivity('lexis.export', 'outline',
                t('Outline') + ': ' + model.totals.terms + ' ' + t('Terms'));
        });
    }

    /** The JSON twin of the outline, for a script rather than a reader. */
    function exportOutlineJson() {
        structureModel(function (model) {
            var payload = {
                generated_at: model.generated.toISOString(),
                scope: model.scope,
                focus: model.focus,
                filters: model.filters,
                totals: model.totals,
                lenses: model.lenses,
                files: model.files
            };
            download('lexis-structure-' + model.generated.toISOString().slice(0, 10) + '.json',
                JSON.stringify(payload, null, 2), 'application/json');
            toast(t('Outline downloaded'));
        });
    }

    var PRINT_OPTIONS = {
        includeChart: true,
        includeFiles: true,
        includeSummary: true,
        landscape: false
    };

    function openPrint() {
        var body = $('#lexisPrintBody');
        body.innerHTML =
            '<fieldset class="lexis-print-opts"><legend>' + esc(t('Scope')) + '</legend>' +
            '<label><input type="checkbox" id="lexisPrintChart"' + (PRINT_OPTIONS.includeChart ? ' checked' : '') + '> ' +
            esc(t('Preview')) + '</label>' +
            '<label><input type="checkbox" id="lexisPrintSummary"' + (PRINT_OPTIONS.includeSummary ? ' checked' : '') + '> ' +
            esc(t('Total')) + '</label>' +
            '<label><input type="checkbox" id="lexisPrintFiles"' + (PRINT_OPTIONS.includeFiles ? ' checked' : '') + '> ' +
            esc(t('Files')) + '</label>' +
            '<label>' + esc(t('Orientation')) + '</label>' +
            '</fieldset>' +
            '<div class="lexis-vrow"><button type="button" id="lexisPrintCancel">' +
            esc(t('Cancel')) + '</button>' +
            '<button type="button" id="lexisPrintOutline">' + esc(t('Download') + ' ' + t('Outline')) + '</button>' +
            '<button type="button" class="primary" id="lexisPrintGo">' + esc(t('Print')) + '</button></div>';
        $('#lexisPrintCancel').addEventListener('click', function () {
            $('#lexisPrint').classList.remove('open');
        });
        $('#lexisPrintOutline').addEventListener('click', function () { exportOutline(); });
        $('#lexisPrintGo').addEventListener('click', function () {
            PRINT_OPTIONS.includeChart = $('#lexisPrintChart').checked;
            PRINT_OPTIONS.includeSummary = $('#lexisPrintSummary').checked;
            PRINT_OPTIONS.includeFiles = $('#lexisPrintFiles').checked;
            $('#lexisPrint').classList.remove('open');
            printOutline();
        });
        openModal('lexisPrint');
    }

    /** The on-screen chart, as a self-contained SVG for the printed page. */
    function chartSnapshot() {
        var chart = $('#lexisChart');
        if (!chart) { return ''; }
        var cards = $$('.lexis-card', chart);
        if (!cards.length) { return ''; }
        var width = chart.scrollWidth || chart.clientWidth || 900;
        var height = chart.scrollHeight || chart.clientHeight || 400;
        var COLORS = {}; // card id -> colour, so edges and boxes agree
        var boxes = cards.map(function (card) {
            var id = card.dataset.id;
            var style = window.getComputedStyle(card);
            // The card markup is `.k` (kicker), `.t` (title) and `.m` (meta).
            // Falling back to the whole card merged all three into one string
            // ("GalaxyWord galaxy1 terms · 3 files") in the first snapshot.
            var kickerEl = card.querySelector('.k');
            var titleEl = card.querySelector('.t') || card.querySelector('.lexis-card-title, .name, h4, strong');
            var metaEl = card.querySelector('.m') || card.querySelector('.lexis-card-meta, .sub, small');
            var title = (titleEl || card).textContent || '';
            var kicker = kickerEl ? kickerEl.textContent : '';
            var label = kicker ? kicker.trim() + ' · ' + title.trim() : title;
            var meta = metaEl ? metaEl.textContent : '';
            var rect = card.getBoundingClientRect();
            var host = chart.getBoundingClientRect();
            COLORS[id] = card.style.getPropertyValue('--c') || style.borderColor || '#7c8cf8';
            var clean = function (value, limit) {
                return String(value || '').replace(/\s+/g, ' ').trim().slice(0, limit);
            };
            return {
                x: rect.left - host.left, y: rect.top - host.top,
                w: rect.width, h: rect.height,
                label: clean(label, 40),
                meta: clean(meta, 30),
                stroke: COLORS[id]
            };
        });
        var parts = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ' + width + ' ' + height +
            '" width="100%" preserveAspectRatio="xMidYMin meet" role="img" aria-label="' +
            esc(t('The taxonomy galaxy')) + '">',
            '<rect width="100%" height="100%" fill="#ffffff"/>'];
        boxes.forEach(function (box) {
            parts.push('<rect x="' + box.x + '" y="' + box.y + '" width="' + box.w + '" height="' + box.h +
                '" rx="8" fill="#ffffff" stroke="' + esc(box.stroke) + '" stroke-width="2"/>');
            parts.push('<text x="' + (box.x + 10) + '" y="' + (box.y + 22) +
                '" font-family="sans-serif" font-size="12" font-weight="600" fill="#15193a">' +
                esc(box.label) + '</text>');
            if (box.meta) {
                parts.push('<text x="' + (box.x + 10) + '" y="' + (box.y + 38) +
                    '" font-family="sans-serif" font-size="10" fill="#5b6178">' + esc(box.meta) + '</text>');
            }
        });
        parts.push('</svg>');
        return parts.join('');
    }

    function renderPrintSheet(model) {
        var host = $('#lexisPrintRoot');
        var html = '<div class="p-page">' +
            '<header class="p-head"><h1>' + esc(t('LEXIS taxonomy')) + '</h1>' +
            '<div class="p-scope">' + esc(model.scope) + '</div>' +
            '<div class="p-meta">' + esc(t('Generated')) + ' ' +
            esc(model.generated.toISOString().slice(0, 16).replace('T', ' ')) +
            (model.filters.length ? ' · ' + esc(model.filters.join(' · ')) : '') + '</div></header>';

        if (PRINT_OPTIONS.includeSummary) {
            html += '<table class="p-summary"><tbody>' +
                '<tr><th>' + esc(t('Files')) + '</th><td>' + model.totals.files + '</td>' +
                '<th>' + esc(t('Terms')) + '</th><td>' + model.totals.terms + '</td></tr>' +
                '<tr><th>' + esc(t('Categories')) + '</th><td>' + model.totals.categories + '</td>' +
                '<th>' + esc(t('Subcategories')) + '</th><td>' + model.totals.subcategories + '</td></tr>' +
                '<tr><th>' + esc(t('Keywords')) + '</th><td>' + model.totals.keywords + '</td>' +
                '<th>' + esc(t('Database')) + '</th><td>' + formatBytes(model.totals.database_bytes) + '</td></tr>' +
                '</tbody></table>';
        }

        if (PRINT_OPTIONS.includeChart) {
            var snapshot = chartSnapshot();
            if (snapshot) {
                html += '<figure class="p-figure">' + snapshot +
                    '<figcaption>' + esc(t('Preview')) + '</figcaption></figure>';
            }
        }

        if (!model.lenses.length) {
            html += '<p class="p-empty">' +
                esc(t('Empty items') + ': ' + t('None')) + '</p>';
        }
        model.lenses.forEach(function (lens) {
            html += '<section class="p-lens-block"><h2>' + esc(lens.label) + ' <span class="p-count">' +
                lens.files + ' ' + esc(t('files')) + '</span></h2>';
            var currentCat = null;
            var currentSub = null;
            lens.rows.forEach(function (row) {
                if (row.main_category_name !== currentCat) {
                    if (currentSub !== null) { html += '</tbody></table>'; currentSub = null; }
                    currentCat = row.main_category_name;
                    html += '<h3 class="p-cat">' + esc(currentCat || t('Uncategorised')) + '</h3>';
                }
                if ((row.subcategory_name || '') !== (currentSub || '')) {
                    if (currentSub !== null) { html += '</tbody></table>'; }
                    currentSub = row.subcategory_name || '';
                    html += '<h4 class="p-sub">' + esc(currentSub || t('Direct terms')) + '</h4>' +
                        '<table><thead><tr><th>' + esc(t('Term')) + '</th><th class="num">' +
                        esc(t('Files')) + '</th></tr></thead><tbody>';
                }
                html += '<tr><td>' + esc(row.term_label || '') + '</td><td class="num">' +
                    row.file_count + '</td></tr>';
            });
            if (currentSub !== null) { html += '</tbody></table>'; }
            html += '</section>';
        });

        if (PRINT_OPTIONS.includeFiles && model.files.length) {
            html += '<section class="p-files"><h2>' + esc(t('Files')) + ' <span class="p-count">' +
                model.files.length + '</span></h2>' +
                '<table><thead><tr><th>' + esc(t('File')) + '</th><th>' + esc(t('Type')) + '</th><th>' +
                esc(t('Date')) + '</th><th>' + esc(t('Source')) + '</th><th>' + esc(t('Side')) +
                '</th></tr></thead><tbody>';
            model.files.forEach(function (file) {
                html += '<tr><td>' + esc(file.name || '') + '</td><td>' + esc(file.type || file.file_type || '') +
                    '</td><td>' + esc(fmtDate(file.date)) + '</td><td>' + esc(file.source || '') +
                    '</td><td>' + esc(file.side || '') + '</td></tr>';
            });
            html += '</tbody></table></section>';
        }

        // No page numbers: the CSS counter prints "1" on every page in this
        // engine, and a wrong page number is worse than none.
        html += '<footer class="p-foot"><span>LEXIS Cosmos</span><span>' + esc(model.scope) +
            ' · ' + esc(model.generated.toISOString().slice(0, 16).replace('T', ' ')) +
            '</span></footer></div>';
        host.innerHTML = html;
        return host.innerHTML.length;
    }

    function printOutline() {
        structureModel(function (model) {
            var length = renderPrintSheet(model);
            if (!length) {
                toast(t('None'));
                return;
            }
            var host = $('#lexisPrintRoot');
            // The stylesheet hides everything that is not the print sheet, and
            // it can only do that for a *direct child of <body>*. The sheet is
            // declared inside the page, so before printing it is moved to the
            // body and afterwards put back - without this the printed page was
            // one blank rectangle (measured: a 1 KB, text-free PDF).
            var originalParent = host.parentNode;
            var originalNext = host.nextSibling;
            document.body.appendChild(host);
            document.body.classList.add('lexis-printing');
            document.body.classList.toggle('lexis-printing-landscape', PRINT_OPTIONS.landscape);
            var restore = function () {
                document.body.classList.remove('lexis-printing');
                document.body.classList.remove('lexis-printing-landscape');
                if (originalParent) {
                    originalParent.insertBefore(host, originalNext && originalNext.parentNode === originalParent
                        ? originalNext : null);
                }
            };
            // The sheet stays in the DOM while the dialog is open: emptying it
            // in a timer is what made an earlier run measure an empty print
            // root and a blank page.
            window.print();
            setTimeout(restore, 400);
        });
    }

    function openHelp() {
        var rows = [
            ['/', t('Focus the term finder')],
            ['+ / -', t('Zoom in and out')],
            ['0', t('Reset the zoom')],
            ['F', t('Fit the structure to the screen')],
            ['L', t('Centre on the focused node')],
            ['B', t('Show or hide the sidebar')],
            ['D', t('Cycle the depth')],
            ['M', t('Open Manage')],
            ['?', t('This help')],
            ['Ctrl/⌘ + Z', t('Undo')],
            ['Ctrl/⌘ + Shift + Z', t('Redo')],
            ['Shift + click', t('Select a range of files')],
            ['Ctrl/⌘ + click', t('Toggle one file')]
        ];
        $('#lexisHelpBody').innerHTML = '<div class="lexis-shortcut">' +
            rows.map(function (pair) {
                return '<kbd>' + esc(pair[0]) + '</kbd><span>' + esc(pair[1]) + '</span>';
            }).join('') + '</div>';
        openModal('lexisHelp');
    }

    /* ==================================================================
       Manage sheet
       ================================================================== */

    var MANAGE_TABS = [
        ['overview', 'Dashboard'], ['taxonomy', 'Taxonomy'], ['files', 'Files'],
        ['views', 'Views'], ['activity', 'Activity']
    ];

    function openManage(tab) {
        if (tab) { S.manageTab = tab; }
        renderManage();
        openModal('lexisManage');
    }

    function renderManage() {
        var host = $('#lexisManageBody');
        host.innerHTML =
            '<div class="lexis-tabs" role="tablist">' +
            MANAGE_TABS.map(function (pair) {
                return '<button type="button" role="tab" data-manage-tab="' + pair[0] + '"' +
                    (S.manageTab === pair[0] ? ' class="on" aria-selected="true"' : ' aria-selected="false"') +
                    '>' + esc(t(pair[1])) + '</button>';
            }).join('') + '</div>' +
            '<div id="lexisManagePane"></div>';
        $$('[data-manage-tab]', host).forEach(function (button) {
            button.addEventListener('click', function () {
                S.manageTab = button.dataset.manageTab;
                renderManage();
            });
        });
        var pane = $('#lexisManagePane');
        if (S.manageTab === 'overview') { manageOverview(pane); }
        else if (S.manageTab === 'taxonomy') { manageTaxonomy(pane); }
        else if (S.manageTab === 'files') { manageFiles(pane); }
        else if (S.manageTab === 'views') { manageViews(pane); }
        else { manageActivity(pane); }
    }

    function bars(items) {
        var max = items.reduce(function (top, item) { return Math.max(top, item.count); }, 0) || 1;
        return '<div class="lexis-bars">' + items.map(function (item) {
            return '<div class="lexis-bar"><span title="' + esc(item.label) + '">' +
                esc(item.label) + '</span><div class="track"><div class="fill" style="width:' +
                Math.round((item.count / max) * 100) + '%"></div></div><span>' +
                item.count + '</span></div>';
        }).join('') + '</div>';
    }

    function manageOverview(pane) {
        pane.innerHTML = '<p class="muted">' + esc(t('Loading…')) + '</p>';
        api(URLS.dashboard).then(function (data) {
            var dashboard = data.dashboard || {};
            var kpis = dashboard.kpis || {};
            var kpi = function (label, value) {
                return '<div class="lexis-kpi"><b>' + esc(value === undefined || value === null ? '—' : value) +
                    '</b><span>' + esc(label) + '</span></div>';
            };
            pane.innerHTML =
                '<div class="lexis-grid4">' +
                kpi(t('Categories'), kpis.categories_count) +
                kpi(t('Subcategories'), kpis.subcategories_count) +
                kpi(t('Word terms'), kpis.words_count) +
                kpi(t('Keywords'), kpis.keywords_count) +
                kpi(t('Files'), kpis.files_count) +
                kpi(t('Term coverage'), (kpis.coverage_pct === undefined ? '—' : kpis.coverage_pct) + '%') +
                kpi(t('Saved views'), kpis.saved_views_count) +
                '</div>' +
                '<h4 class="lexis-ftitle">' + esc(t('Files by type')) + '</h4>' +
                (dashboard.by_type && dashboard.by_type.length
                    ? bars(dashboard.by_type.map(function (item) {
                        return { label: item.label || '—', count: item.count }; }))
                    : '<p class="muted">' + esc(t('No files yet.')) + '</p>') +
                '<h4 class="lexis-ftitle">' + esc(t('Files by side')) + '</h4>' +
                (dashboard.by_side && dashboard.by_side.length
                    ? bars(dashboard.by_side.map(function (item) {
                        return { label: item.label || '—', count: item.count }; }))
                    : '<p class="muted">' + esc(t('No files yet.')) + '</p>') +
                '<h4 class="lexis-ftitle">' + esc(t('Terms with no files')) + '</h4>' +
                (dashboard.orphan_terms && dashboard.orphan_terms.length
                    ? '<table class="lexis-table"><thead><tr><th>' + esc(t('Lens')) + '</th><th>' +
                      esc(t('Term')) + '</th><th class="num">' + esc(t('Category')) + '</th></tr></thead><tbody>' +
                      dashboard.orphan_terms.map(function (term) {
                          return '<tr><td>' + esc(term.lens === 'words' ? t('Word') : t('Keyword')) +
                              '</td><td>' + esc(term.label || '—') + '</td><td class="num">' +
                              esc(term.category_id || '—') + '</td></tr>';
                      }).join('') + '</tbody></table>'
                    : '<p class="muted">' + esc(t('Every term matches at least one file.')) + '</p>') +
                '<h4 class="lexis-ftitle">' + esc(t('Files no term matches')) + '</h4>' +
                (dashboard.uncategorised && dashboard.uncategorised.length
                    ? '<table class="lexis-table"><thead><tr><th>' + esc(t('Name')) + '</th><th>' +
                      esc(t('Type')) + '</th><th>' + esc(t('Date')) + '</th></tr></thead><tbody>' +
                      dashboard.uncategorised.map(function (row) {
                          return '<tr><td>' + esc(row.file_name) + '</td><td>' + esc(row.file_type) +
                              '</td><td>' + esc(fmtDate(row.file_date)) + '</td></tr>';
                      }).join('') + '</tbody></table>'
                    : '<p class="muted">' + esc(t('Every file is matched by at least one term.')) + '</p>');
        }).catch(function (error) {
            pane.innerHTML = '<p class="bad">' + esc(error.message) + '</p>';
        });
    }

    var TAXONOMY_CACHE = null;

    function manageTaxonomy(pane) {
        pane.innerHTML =
            '<div class="lexis-toolbar-row">' +
            '<button type="button" id="lexisAddCategory">+ ' + esc(t('Main category')) + '</button>' +
            '<button type="button" id="lexisAddFile">+ ' + esc(t('File record')) + '</button>' +
            '<span class="grow"></span>' +
            '<button type="button" id="lexisReloadTaxonomy">' + esc(t('Reload')) + '</button>' +
            '</div><div id="lexisTaxonomyTree"><p class="muted">' + esc(t('Loading…')) + '</p></div>';
        $('#lexisAddCategory').addEventListener('click', function () { createCategoryDialog(null); });
        $('#lexisAddFile').addEventListener('click', addFileDialog);
        $('#lexisReloadTaxonomy').addEventListener('click', function () { TAXONOMY_CACHE = null; manageTaxonomy(pane); });
        api(URLS.outline).then(function (data) {
            TAXONOMY_CACHE = data.outline || [];
            renderTaxonomyTree();
        }).catch(function (error) {
            $('#lexisTaxonomyTree').innerHTML = '<p class="bad">' + esc(error.message) + '</p>';
        });
    }

    function renderTaxonomyTree() {
        var tree = $('#lexisTaxonomyTree');
        if (!tree) { return; }
        var rows = TAXONOMY_CACHE || [];
        if (!rows.length) {
            tree.innerHTML = '<p class="muted">' + esc(t('The taxonomy is empty. Add a main category to begin.')) + '</p>';
            return;
        }
        // Group: lens -> main category -> subcategory -> terms
        var groups = {};
        rows.forEach(function (row) {
            var lens = row.lens;
            groups[lens] = groups[lens] || {};
            var catKey = (row.main_category_name || '—') + '\u0000' + (row.main_category_id || 0);
            groups[lens][catKey] = groups[lens][catKey] || { name: row.main_category_name, id: row.main_category_id, subs: {} };
            var cat = groups[lens][catKey];
            var subKey = (row.subcategory_name || '') + '\u0000' + (row.subcategory_id || 0);
            cat.subs[subKey] = cat.subs[subKey] || { name: row.subcategory_name, id: row.subcategory_id, terms: [] };
            if (row.term_id) {
                cat.subs[subKey].terms.push({ id: row.term_id, label: row.term_label,
                    file_count: row.file_count, lens: lens });
            }
        });

        var html = '';
        Object.keys(groups).forEach(function (lens) {
            html += '<div class="lexis-tree-node"><div class="lexis-tree-row"><b>' +
                esc(lensLabel(lens)) + '</b></div>';
            Object.keys(groups[lens]).forEach(function (catKey) {
                var cat = groups[lens][catKey];
                html += '<div class="lexis-tree-node"><div class="lexis-tree-row">' +
                    '<b>' + esc(cat.name || t('Uncategorised')) + '</b>' +
                    '<span class="lexis-pill">' + esc(t('Category')) + ' #' + esc(cat.id || '—') + '</span>' +
                    '<button type="button" class="small" data-cat-add-sub="' + esc(cat.id || '') + '" data-lens="' + esc(lens) + '">+ ' +
                    esc(t('Subcategory')) + '</button>' +
                    '<button type="button" class="small" data-cat-rename="' + esc(cat.id || '') + '" data-name="' + esc(cat.name || '') + '">' +
                    esc(t('Rename')) + '</button>' +
                    '<button type="button" class="small" data-cat-color="' + esc(cat.id || '') + '" data-name="' + esc(cat.name || '') + '">' +
                    esc(t('Colour')) + '</button>' +
                    '<button type="button" class="small danger" data-cat-delete="' + esc(cat.id || '') + '">' +
                    esc(t('Delete')) + '</button>' +
                    '</div>';
                Object.keys(cat.subs).forEach(function (subKey) {
                    var sub = cat.subs[subKey];
                    var subId = sub.id || cat.id;
                    html += '<div class="lexis-tree-node"><div class="lexis-tree-row">' +
                        '<b>' + esc(sub.name || t('Direct terms')) + '</b>' +
                        '<span class="lexis-pill">' + esc(t('Subcategory')) + ' #' + esc(subId || '—') + '</span>' +
                        '<button type="button" class="small" data-sub-add-term="' + esc(subId || '') + '" data-lens="' + esc(lens) + '">+ ' +
                        esc(lens === 'words' ? t('Term') : t('Keyword')) + '</button>' +
                        (sub.id ? '<button type="button" class="small" data-sub-rename="' + esc(sub.id) +
                            '" data-name="' + esc(sub.name || '') + '">' + esc(t('Rename')) + '</button>' +
                            '<button type="button" class="small" data-cat-color="' + esc(sub.id) +
                            '" data-name="' + esc(sub.name || '') + '">' + esc(t('Colour')) + '</button>' +
                            '<button type="button" class="small danger" data-sub-delete="' + esc(sub.id) + '">' +
                            esc(t('Delete')) + '</button>' : '') +
                        '</div>';
                    if (!sub.terms.length) {
                        html += '<div class="lexis-tree-row"><span class="muted">' +
                            esc(t('No terms in this subcategory.')) + '</span></div>';
                    }
                    html += '<div class="lexis-tree-node">' + sub.terms.map(function (term) {
                        return '<div class="lexis-tree-row">' +
                            '<span class="lexis-tag ' + (lens === 'words' ? 'lexis-tag-w' : 'lexis-tag-k') + '">' +
                            esc(lens === 'words' ? t('Word') : t('Keyword')) + '</span>' +
                            '<span>' + esc(term.label || '') + '</span>' +
                            '<span class="lexis-pill">' + term.file_count + ' ' + esc(t('files')) + '</span>' +
                            '<button type="button" class="small" data-term-edit="' + esc(term.id) +
                            '" data-lens="' + esc(lens) + '" data-label="' + esc(term.label || '') +
                            '" data-sub="' + esc(subId || '') + '">' + esc(t('Edit')) + '</button>' +
                            '<button type="button" class="small danger" data-term-delete="' + esc(term.id) +
                            '" data-lens="' + esc(lens) + '" data-sub="' + esc(subId || '') + '">' +
                            esc(t('Remove')) + '</button>' +
                            '</div>';
                    }).join('') + '</div>';
                    html += '</div>';
                });
                html += '</div>';
            });
            html += '</div>';
        });
        tree.innerHTML = html;
        bindTaxonomyTree();
    }

    function bindTaxonomyTree() {
        var tree = $('#lexisTaxonomyTree');
        $$('[data-cat-add-sub]', tree).forEach(function (button) {
            button.addEventListener('click', function () {
                createCategoryDialog(Number(button.dataset.catAddSub), button.dataset.lens);
            });
        });
        $$('[data-cat-rename]', tree).forEach(function (button) {
            button.addEventListener('click', function () {
                renameCategoryDialog(Number(button.dataset.catRename), true, button.dataset.name);
            });
        });
        $$('[data-cat-color]', tree).forEach(function (button) {
            button.addEventListener('click', function () {
                colourCategoryDialog(Number(button.dataset.catColor), button.dataset.name || '');
            });
        });
        $$('[data-cat-delete]', tree).forEach(function (button) {
            button.addEventListener('click', function () {
                deleteCategory(Number(button.dataset.catDelete), true);
            });
        });
        $$('[data-sub-rename]', tree).forEach(function (button) {
            button.addEventListener('click', function () {
                renameCategoryDialog(Number(button.dataset.subRename), false, button.dataset.name);
            });
        });
        $$('[data-sub-delete]', tree).forEach(function (button) {
            button.addEventListener('click', function () {
                deleteCategory(Number(button.dataset.subDelete), false);
            });
        });
        $$('[data-sub-add-term]', tree).forEach(function (button) {
            button.addEventListener('click', function () {
                addTermDialog(Number(button.dataset.subAddTerm), button.dataset.lens);
            });
        });
        $$('[data-term-edit]', tree).forEach(function (button) {
            button.addEventListener('click', function () {
                editTermDialog(button.dataset.termEdit, button.dataset.lens,
                    button.dataset.label, Number(button.dataset.sub));
            });
        });
        $$('[data-term-delete]', tree).forEach(function (button) {
            button.addEventListener('click', function () {
                removeTerm(button.dataset.termDelete, button.dataset.lens,
                    Number(button.dataset.sub));
            });
        });
    }

    function refreshTaxonomy() {
        TAXONOMY_CACHE = null;
        afterMutation();
        if (S.manageTab === 'taxonomy' && $('#lexisManage').classList.contains('open')) {
            renderManage();
        }
    }

    function createCategoryDialog(parentId, lens) {
        var fields = [{ name: 'name', label: t('Name'), value: '' }];
        if (!parentId) {
            fields.push({ name: 'color', label: t('Colour (optional)'), value: '', placeholder: '#2dd4bf' });
        }
        openEditor(parentId ? t('New subcategory') : t('New main category'), fields, function (values) {
            return api(URLS.categoryCreate, {
                method: 'POST',
                body: { name: values.name, parent_id: parentId || null, color: values.color || null }
            }).then(function (data) {
                var created = data.category || {};
                pushUndo(parentId ? t('Add subcategory') : t('Add category'), function () {
                    return api(withId(URLS.categoryDelete, created.id), {
                        method: 'DELETE',
                        body: { main: parentId ? false : true }
                    }).then(afterMutation);
                });
                toast(t('Category created'));
                refreshTaxonomy();
            });
        });
    }

    function renameCategoryDialog(categoryId, isMain, currentName) {
        openEditor(t('Rename category'), [
            { name: 'name', label: t('Name'), value: currentName },
            { name: 'color', label: t('Colour (optional)'), value: '', placeholder: '#2dd4bf' }
        ], function (values) {
            return api(withId(URLS.categoryUpdate, categoryId), {
                method: 'PATCH',
                body: { name: values.name, color: values.color || null, is_main: isMain }
            }).then(function () {
                pushUndo(t('Rename category'), function () {
                    return api(withId(URLS.categoryUpdate, categoryId), {
                        method: 'PATCH', body: { name: currentName }
                    }).then(afterMutation);
                });
                toast(t('Category renamed'));
                refreshTaxonomy();
            });
        });
    }

    // Q8.5 - a colour belongs to the category *name* (that is how the report
    // template keys its palette), so the endpoint takes the name rather than
    // the id. Renaming a category keeps its colour, which is why this is a
    // separate action instead of a field inside the rename dialog.
    function colourCategoryDialog(categoryId, name) {
        openEditor(t('Set the category colour'), [
            { name: 'color', label: t('Colour'), value: '',
              placeholder: '#2dd4bf', help: t('Write the colour as #rrggbb.') }
        ], function (values) {
            if (!/^#[0-9a-fA-F]{6}$/.test(values.color || '')) {
                return Promise.reject(new Error(t('A colour must be written as #rrggbb.')));
            }
            return api(withId(URLS.categoryColor, categoryId), {
                method: 'POST',
                body: { name: name, color: values.color }
            }).then(function () {
                toast(t('Colour saved'));
                refreshTaxonomy();
            });
        }, { submitLabel: t('Save') });
    }

    function deleteCategory(categoryId, isMain) {
        confirmDialog(t('Delete this category? Terms are kept, only the category is removed.'), function () {
            return api(withId(URLS.categoryDelete, categoryId) + (isMain ? '?main=1' : ''), {
                method: 'DELETE',
                body: {}
            }).then(function () {
                toast(t('Category deleted'));
                refreshTaxonomy();
            });
        }, { confirmLabel: t('Delete'), danger: true });
    }

    function addTermDialog(subcategoryId, lens) {
        var isWord = lens === 'words';
        openEditor(isWord ? t('New word term') : t('New keyword'), [
            { name: 'label', label: isWord ? t('Term') : t('Keyword expression'), value: '',
                help: isWord ? '' : t('Words separated by spaces; the expression is stored as the sequence of its words.') }
        ], function (values) {
            if (isWord) {
                // Q8.8 - the same word may legitimately sit under two
                // subcategories, but the operator should know that it already
                // exists somewhere before creating a second home for it.
                return api(URLS.termDuplicate + '?label=' + encodeURIComponent(values.label))
                    .then(function (checked) {
                        var existing = checked.duplicate;
                        if (!existing) { return true; }
                        var where = [existing.category_name, existing.subcategory_name]
                            .filter(Boolean).join(' \u203a ');
                        return new Promise(function (resolve) {
                            confirmDialog(
                                t('This term already lives in %(path)s. Add it here as well?',
                                    { path: where || t('Uncategorised') }),
                                function () { resolve(true); },
                                { confirmLabel: t('Add'), danger: false,
                                    onCancel: function () { resolve(false); } });
                        });
                    })
                    .then(function (proceed) {
                        if (proceed === false) { return null; }
                        return createWordTerm(values.label, subcategoryId);
                    });
            }
            return api(URLS.keywordCreate, {
                method: 'POST',
                body: { keyword: values.label, subcategory_id: subcategoryId }
            }).then(function (data) {
                var keywordId = data.keyword ? data.keyword.id : null;
                pushUndo(t('Add keyword'), function () {
                    return api(withId(URLS.keywordDelete, keywordId), {
                        method: 'DELETE', body: {}
                    }).then(afterMutation);
                });
                toast(t('Keyword added'));
                refreshTaxonomy();
            });
        });
    }

    function createWordTerm(label, subcategoryId) {
        return api(URLS.termCreate, {
                    method: 'POST',
                    body: { label: label, subcategory_id: subcategoryId }
                }).then(function (data) {
                    var wordId = data.term ? data.term.word_id : null;
                    pushUndo(t('Add term'), function () {
                        return api(URLS.termDelete, {
                            method: 'PATCH',
                            body: { word_id: wordId, subcategory_id: subcategoryId }
                        }).then(afterMutation);
                    });
                    toast(t('Term added'));
                    refreshTaxonomy();
                });
    }

    function editTermDialog(termId, lens, currentLabel, subcategoryId) {
        openEditor(lens === 'words' ? t('Edit term') : t('Edit keyword'), [
            { name: 'label', label: lens === 'words' ? t('Term') : t('Keyword expression'),
                value: currentLabel }
        ], function (values) {
            if (lens === 'words') {
                return api(URLS.termUpdate, {
                    method: 'PATCH',
                    body: { word_id: Number(termId), label: values.label,
                        subcategory_id: subcategoryId }
                }).then(function () {
                    pushUndo(t('Edit term'), function () {
                        return api(URLS.termUpdate, {
                            method: 'PATCH',
                            body: { word_id: Number(termId), label: currentLabel,
                                subcategory_id: subcategoryId }
                        }).then(afterMutation);
                    });
                    toast(t('Term updated'));
                    refreshTaxonomy();
                });
            }
            return api(withId(URLS.keywordUpdate, termId), {
                method: 'PATCH',
                body: { keyword: values.label, subcategory_id: subcategoryId }
            }).then(function () {
                pushUndo(t('Edit keyword'), function () {
                    return api(withId(URLS.keywordUpdate, termId), {
                        method: 'PATCH', body: { keyword: currentLabel, subcategory_id: subcategoryId }
                    }).then(afterMutation);
                });
                toast(t('Keyword updated'));
                refreshTaxonomy();
            });
        });
    }

    function removeTerm(termId, lens, subcategoryId) {
        confirmDialog(t('Remove this term from the subcategory?'), function () {
            if (lens === 'words') {
                return api(URLS.termDelete, {
                    method: 'DELETE',
                    body: { word_id: Number(termId), subcategory_id: subcategoryId }
                }).then(function () {
                    toast(t('Term removed'));
                    refreshTaxonomy();
                });
            }
            return api(withId(URLS.keywordDelete, termId), { method: 'DELETE', body: {} })
                .then(function () {
                    toast(t('Keyword removed'));
                    refreshTaxonomy();
                });
        }, { confirmLabel: t('Remove'), danger: true });
    }

    function addFileDialog() {
        var types = (S.options.file_types || ['PDF', 'DOCX', 'TXT', 'EML', 'XLSX', 'CSV']);
        openEditor(t('Add a file record'), [
            { name: 'file_name', label: t('File name'), value: '' },
            { name: 'file_path', label: t('Stored path'), value: '', placeholder: '/archive/…' },
            { name: 'file_type', label: t('Type'), type: 'select', value: types[0],
                options: types.map(function (type) { return { value: type, label: type }; }) },
            { name: 'file_date', label: t('Date'), type: 'date',
                value: new Date().toISOString().slice(0, 10) },
            { name: 'source', label: t('Source'), value: '', placeholder: 'LEXIS' },
            { name: 'side', label: t('Side'), value: '', placeholder: 'Unknown' },
            { name: 'content', label: t('Text'), type: 'textarea', value: '',
                help: t('The text is indexed against the taxonomy; existing words and phrases are counted.') }
        ], function (values) {
            return api(URLS.ingest, { method: 'POST', body: values }).then(function (data) {
                var created = data.file || {};
                pushUndo(t('Add file'), function () {
                    return api(URLS.bulkDelete, {
                        method: 'POST', body: { path_ids: [created.id] }
                    }).then(afterMutation);
                });
                toast(t('File added'));
                refreshTaxonomy();
            });
        }, { submitLabel: t('Add') });
    }

    var MANAGE_FILES = { query: '', type: '', offset: 0, limit: 25, total: 0 };
    var MANAGE_SELECTION = new Set();

    function manageFiles(pane) {
        pane.innerHTML =
            '<div class="lexis-toolbar-row">' +
            '<input type="search" id="lexisManageQuery" value="' + esc(MANAGE_FILES.query) +
            '" placeholder="' + esc(t('Search names, sources, sides and text…')) + '">' +
            '<select id="lexisManageType"><option value="">' + esc(t('All file types')) + '</option>' +
            (S.options.file_types || []).map(function (type) {
                return '<option value="' + esc(type) + '"' +
                    (MANAGE_FILES.type === type ? ' selected' : '') + '>' + esc(type) + '</option>';
            }).join('') + '</select>' +
            '<button type="button" id="lexisManageSearch">' + esc(t('Search')) + '</button>' +
            '<button type="button" id="lexisManageAdd">+ ' + esc(t('File record')) + '</button>' +
            '</div>' +
            '<div id="lexisManageFileBulk"></div>' +
            '<div id="lexisManageFileTable"><p class="muted">' + esc(t('Loading…')) + '</p></div>' +
            '<div class="lexis-vrow"><span class="muted" id="lexisManageFileCount"></span>' +
            '<span><button type="button" id="lexisManagePrev">&#8249; ' + esc(t('Previous')) + '</button> ' +
            '<button type="button" id="lexisManageNext">' + esc(t('Next')) + ' &#8250;</button></span></div>';
        var run = function () {
            MANAGE_FILES.query = $('#lexisManageQuery').value;
            MANAGE_FILES.type = $('#lexisManageType').value;
            MANAGE_FILES.offset = 0;
            loadManageFiles();
        };
        $('#lexisManageSearch').addEventListener('click', run);
        $('#lexisManageQuery').addEventListener('keydown', function (event) {
            if (event.key === 'Enter') { run(); }
        });
        $('#lexisManageAdd').addEventListener('click', addFileDialog);
        $('#lexisManagePrev').addEventListener('click', function () {
            MANAGE_FILES.offset = Math.max(0, MANAGE_FILES.offset - MANAGE_FILES.limit);
            loadManageFiles();
        });
        $('#lexisManageNext').addEventListener('click', function () {
            if (MANAGE_FILES.offset + MANAGE_FILES.limit < MANAGE_FILES.total) {
                MANAGE_FILES.offset += MANAGE_FILES.limit;
                loadManageFiles();
            }
        });
        loadManageFiles();
    }

    function loadManageFiles() {
        var url = URLS.manageFiles + '?limit=' + MANAGE_FILES.limit +
            '&offset=' + MANAGE_FILES.offset +
            '&file_type=' + encodeURIComponent(MANAGE_FILES.type || '') +
            '&mfq=' + encodeURIComponent(MANAGE_FILES.query || '');
        api(url).then(function (data) {
            MANAGE_FILES.total = data.total || 0;
            renderManageFiles(data.files || []);
            var count = $('#lexisManageFileCount');
            if (count) {
                count.textContent = (MANAGE_FILES.offset + 1) + '–' +
                    (MANAGE_FILES.offset + (data.files || []).length) + ' ' + t('of') + ' ' +
                    MANAGE_FILES.total;
            }
        }).catch(function (error) {
            var host = $('#lexisManageFileTable');
            if (host) { host.innerHTML = '<p class="bad">' + esc(error.message) + '</p>'; }
        });
    }

    function renderManageFiles(files) {
        var host = $('#lexisManageFileTable');
        host.innerHTML = '<table class="lexis-table"><thead><tr>' +
            '<th style="width:26px"><input type="checkbox" id="lexisManageSelectAll" title="' +
            esc(t('Select every file in the list, or clear the selection')) + '" aria-label="' +
            esc(t('Select every file in the list, or clear the selection')) + '"></th><th>' + esc(t('Name')) + '</th><th>' + esc(t('Type')) +
            '</th><th>' + esc(t('Source')) + '</th><th>' + esc(t('Side')) + '</th><th>' +
            esc(t('Date')) + '</th><th class="num">' + esc(t('Terms')) + '</th><th>' +
            esc(t('Snippet')) + '</th></tr></thead><tbody>' +
            files.map(function (file) {
                return '<tr><td><input type="checkbox" data-manage-file="' + file.id + '"' +
                    (MANAGE_SELECTION.has(Number(file.id)) ? ' checked' : '') + ' aria-label="' +
                    esc(t('Select file')) + '"></td>' +
                    '<td>' + esc(file.name) + '</td><td>' + typeBadge(file.type) + '</td>' +
                    '<td>' + esc(file.source || '—') + '</td><td>' + esc(file.side || '—') + '</td>' +
                    '<td>' + esc(fmtDate(file.date)) + '</td><td class="num">' +
                    (file.matched_terms_count || 0) + '</td>' +
                    '<td class="muted">' + esc(file.snippet || '') + '</td></tr>';
            }).join('') + '</tbody></table>';
        $$('[data-manage-file]', host).forEach(function (box) {
            box.addEventListener('change', function () {
                var id = Number(box.dataset.manageFile);
                if (box.checked) { MANAGE_SELECTION.add(id); } else { MANAGE_SELECTION.delete(id); }
                renderManageBulk();
            });
        });
        var selectAll = $('#lexisManageSelectAll');
        if (selectAll) {
            selectAll.addEventListener('change', function () {
                files.forEach(function (file) {
                    if (selectAll.checked) { MANAGE_SELECTION.add(Number(file.id)); }
                    else { MANAGE_SELECTION.delete(Number(file.id)); }
                });
                renderManageFiles(files);
                renderManageBulk();
            });
        }
        renderManageBulk();
    }

    function renderManageBulk() {
        var host = $('#lexisManageFileBulk');
        if (!host) { return; }
        if (!MANAGE_SELECTION.size) {
            host.innerHTML = '';
            return;
        }
        host.innerHTML = '<div class="lexis-bulk"><b>' + MANAGE_SELECTION.size + '</b> ' +
            esc(t('selected')) +
            '<span class="grow"></span>' +
            '<select id="lexisManageSide">' + (S.options.sides || []).map(function (side) {
                return '<option value="' + side.id + '">' + esc(side.name) + '</option>';
            }).join('') + '</select>' +
            '<button type="button" id="lexisManageMoveSide">' + esc(t('Move to side')) + '</button>' +
            '<button type="button" class="danger" id="lexisManageDelete">' + esc(t('Delete')) + '</button>' +
            '</div>';
        $('#lexisManageMoveSide').addEventListener('click', function () {
            var sideId = Number($('#lexisManageSide').value);
            api(URLS.bulkSide, {
                method: 'POST',
                body: { path_ids: Array.from(MANAGE_SELECTION), side_id: sideId }
            }).then(function () {
                toast(t('Files moved'));
                MANAGE_SELECTION.clear();
                afterMutation();
                loadManageFiles();
            }).catch(function (error) { toast(error.message); });
        });
        $('#lexisManageDelete').addEventListener('click', function () {
            var ids = Array.from(MANAGE_SELECTION);
            confirmDialog(t('Delete the selected files? This cannot be undone.'), function () {
                return api(URLS.bulkDelete, { method: 'POST', body: { path_ids: ids } })
                    .then(function () {
                        toast(t('Files deleted'));
                        MANAGE_SELECTION.clear();
                        afterMutation();
                        loadManageFiles();
                    });
            }, { confirmLabel: t('Delete'), danger: true });
        });
    }

    function manageViews(pane) {
        pane.innerHTML =
            '<div class="lexis-toolbar-row"><button type="button" id="lexisManageSaveView">+ ' +
            esc(t('Save the current view')) + '</button><span class="grow"></span>' +
            '<button type="button" id="lexisManageReloadViews">' + esc(t('Reload')) + '</button></div>' +
            '<div class="lexis-views" id="lexisManageViewList"><p class="muted">' + esc(t('Loading…')) +
            '</p></div>';
        $('#lexisManageSaveView').addEventListener('click', openSaveView);
        $('#lexisManageReloadViews').addEventListener('click', function () {
            loadViews();
            manageViews(pane);
        });
        api(URLS.views).then(function (data) {
            S.views = data.views || [];
            var host = $('#lexisManageViewList');
            if (!S.views.length) {
                host.innerHTML = '<p class="muted">' + esc(t('No saved views yet.')) + '</p>';
                return;
            }
            host.innerHTML = S.views.map(function (view) {
                var filters = view.filters || {};
                return '<div class="vitem"><div><b>' + esc(view.name) + '</b>' +
                    '<small>' + esc(t('Focus')) + ': ' + esc(filters.focus || 'root') +
                    ' · ' + esc(t('Terms in basket')) + ': ' + ((filters.basket || []).length) +
                    ' · ' + esc(t('Created')) + ' ' + esc(fmtDate(view.created_at)) + '</small></div>' +
                    '<div><button type="button" class="small" data-view-apply="' + view.id + '">' +
                    esc(t('Apply')) + '</button> ' +
                    '<button type="button" class="small" data-view-rename="' + view.id +
                    '" data-name="' + esc(view.name) + '">' + esc(t('Rename')) + '</button> ' +
                    '<button type="button" class="small danger" data-view-delete="' + view.id +
                    '">' + esc(t('Delete')) + '</button></div></div>';
            }).join('');
            $$('[data-view-apply]', host).forEach(function (button) {
                button.addEventListener('click', function () {
                    var view = S.views.filter(function (item) {
                        return String(item.id) === button.dataset.viewApply;
                    })[0];
                    if (view) {
                        closeModals();
                        applyView(view);
                    }
                });
            });
            $$('[data-view-rename]', host).forEach(function (button) {
                button.addEventListener('click', function () {
                    openEditor(t('Rename view'), [
                        { name: 'name', label: t('View name'), value: button.dataset.name }
                    ], function (values) {
                        return api(withId(URLS.viewRename, button.dataset.viewRename), {
                            method: 'PATCH', body: { name: values.name }
                        }).then(function () {
                            toast(t('View renamed'));
                            loadViews();
                            manageViews(pane);
                        });
                    });
                });
            });
            $$('[data-view-delete]', host).forEach(function (button) {
                button.addEventListener('click', function () {
                    confirmDialog(t('Delete this saved view?'), function () {
                        return api(withId(URLS.viewDelete, button.dataset.viewDelete), {
                            method: 'DELETE', body: {}
                        }).then(function () {
                            toast(t('View deleted'));
                            loadViews();
                            manageViews(pane);
                        });
                    }, { confirmLabel: t('Delete'), danger: true });
                });
            });
        }).catch(function (error) {
            pane.innerHTML = '<p class="bad">' + esc(error.message) + '</p>';
        });
    }

    function manageActivity(pane) {
        pane.innerHTML = '<p class="muted">' + esc(t('Loading…')) + '</p>';
        api(URLS.activity).then(function (data) {
            var rows = data.activity || [];
            if (!rows.length) {
                pane.innerHTML = '<p class="muted">' + esc(t('No activity has been recorded yet.')) + '</p>';
                return;
            }
            pane.innerHTML = '<div class="lexis-log">' + rows.map(function (row) {
                return '<div><time>' + esc(row.t || '') + '</time><span>' +
                    esc(row.msg || row.action || '') + '</span></div>';
            }).join('') + '</div>';
        }).catch(function (error) {
            pane.innerHTML = '<p class="bad">' + esc(error.message) + '</p>';
        });
    }

    /* ==================================================================
       Boot
       ================================================================== */

    document.addEventListener('DOMContentLoaded', function () {
        if (!$('#lexisApp')) { return; }
        boot();
    });

    function boot() {
        if (window.__LEXIS_BOOTED__) { return; }
        window.__LEXIS_BOOTED__ = true;
        bindToolbar();
        bindPanel();
        syncEmptyControls();
        renderUndoState();
        updateBasketBadge();
        renderBasketChips();
        renderFilterCounts();
        loadHealth();
        loadLenses();
        loadOptions();
        loadViews();
        Promise.all([loadChildren('root'), loadChildren('lens:words')]).then(function () {
            S.expanded.add('lens:words');
            renderCrumbs();
            renderChart();
            refreshPanel();
        });
    }
})();
