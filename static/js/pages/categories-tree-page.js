/**
 * Category hierarchy page.
 *
 * The page owns everything that changes the *shape* of what is on screen:
 * expand/collapse, the indented list and hierarchy map views, the search that
 * auto-expands to a hit, drag-and-drop re-parenting, keyboard navigation and
 * the CSV export. It reads the tree from ``GET /api/categories/tree`` and
 * writes through the same surface, so the cycle rule, the sibling-name rule and
 * the audit log all live on the server - this file never pretends to enforce
 * them and always shows the server's refusal.
 *
 * Nothing here is inline script (RES-CSP-01): controls are wired with
 * ``data-on-click``/``data-on-change`` and the rows are built with
 * ``createElement`` and ``textContent``, never ``innerHTML``, so a category
 * named ``<img onerror=...>`` is displayed as that text.
 */
(function () {
    'use strict';

    const API = '/api/categories/tree';
    const PAGE = 500;

    const state = {
        nodes: new Map(),      // id -> node
        order: [],             // ids in path order
        children: new Map(),   // parent id (or 'root') -> [ids]
        expanded: new Set(),
        view: 'list',
        search: '',
        matches: new Set(),
        selected: null,
        loading: false,
        loaded: new Set(),   // ids whose children have been fetched
        stats: new Map()     // id -> {descendant_count, total_terms} or null
    };

    let translations = {};
    //: Bumped every time the dialog is opened, so a pending force-close from a
    //: previous save cannot hide a dialog the operator has already reopened.
    let modalGeneration = 0;

    function t(key, fallback) {
        const value = translations[key];
        return (typeof value === 'string' && value) ? value : (fallback || key);
    }

    // ------------------------------------------------------------------
    // Data
    // ------------------------------------------------------------------

    function csrfToken() {
        const meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute('content') : '';
    }

    async function apiFetch(url, options) {
        const headers = Object.assign({'Accept': 'application/json'}, (options || {}).headers || {});
        if ((options || {}).method && options.method !== 'GET') {
            headers['Content-Type'] = 'application/json';
            headers['X-CSRFToken'] = csrfToken();
        }
        const response = await fetch(url, Object.assign({}, options, {headers: headers}));
        let payload = null;
        try {
            payload = await response.json();
        } catch (error) {
            payload = null;
        }
        if (!response.ok) {
            const message = (payload && (payload.error || payload.message)) ||
                ('HTTP ' + response.status);
            const failure = new Error(message);
            failure.status = response.status;
            failure.payload = payload;
            throw failure;
        }
        return payload;
    }

    /**
     * Load only what the page paints: the top level.
     *
     * This is the difference between a page whose cost is the catalogue and one
     * whose cost is the top level. Children arrive one node at a time through
     * loadChildren() when a node is expanded.
     */
    async function loadRoots() {
        state.loading = true;
        setStatus(t('loading', 'Loading...'));
        try {
            const collected = new Map();
            const order = [];
            let cursor = null;
            let pages = 0;
            do {
                const query = new URLSearchParams({limit: String(PAGE)});
                if (cursor) { query.set('cursor', cursor); }
                const payload = await apiFetch(API + '/roots?' + query.toString());
                (payload.rows || []).forEach(function (row) {
                    collected.set(row.id, row);
                    order.push(row.id);
                });
                cursor = payload.next_cursor;
                pages += 1;
            } while (cursor && pages < 400);

            state.nodes = collected;
            state.order = order;
            state.matches = new Set();
            state.search = '';
            state.loaded = new Set();
            state.stats = new Map();
            indexTree();
            render();
            setStatus('');
        } catch (error) {
            showError(error.message);
            setStatus('');
        } finally {
            state.loading = false;
            // A measurable "first paint": from the moment this page started
            // loading its data to the moment the first level is on screen.
            // The shell around the page is measured separately, so this number
            // answers "what does painting the tree cost".
            if (window.performance && performance.mark
                    && document.querySelector('#treeRows .tree-row')) {
                performance.mark('category-tree:first-paint');
            }
        }
    }

    /** Fetch one node's children, once, and remember that they are loaded. */
    async function loadChildren(id) {
        if (state.loaded.has(id)) { return; }
        state.loaded.add(id);
        try {
            let cursor = null;
            let pages = 0;
            do {
                const query = new URLSearchParams({limit: String(PAGE)});
                if (cursor) { query.set('cursor', cursor); }
                const payload = await apiFetch(
                    API + '/' + id + '/children?' + query.toString());
                (payload.rows || []).forEach(function (row) {
                    if (!state.nodes.has(row.id)) {
                        state.nodes.set(row.id, row);
                        state.order.push(row.id);
                    }
                });
                cursor = payload.next_cursor;
                pages += 1;
            } while (cursor && pages < 400);
            indexTree();
        } catch (error) {
            state.loaded.delete(id);
            showError(error.message);
        }
    }

    /**
     * descendant_count / total_terms for one node.
     *
     * Deliberately not part of a paint: this is the only read whose cost is the
     * size of a subtree. It is fetched when the operator expands the node, and
     * until it arrives the row shows the counts that are free.
     */
    async function loadStats(id) {
        if (state.stats.has(id)) { return; }
        state.stats.set(id, null);
        try {
            const payload = await apiFetch(API + '/' + id + '/stats');
            state.stats.set(id, payload.stats || null);
            render();
        } catch (error) {
            state.stats.delete(id);
        }
    }

    /** Search still asks the tree endpoint: it returns hits and their ancestors. */
    async function searchTree(search) {
        state.loading = true;
        setStatus(t('searching', 'Searching...'));
        try {
            // 200 rows is what the table shows at a glance; asking for a
            // full page of 500 cost about a third of a second of rendering
            // without making the answer more useful.
            const query = new URLSearchParams({limit: '200', search: search});
            const payload = await apiFetch(API + '?' + query.toString());
            const collected = new Map();
            const order = [];
            const matches = new Set();
            (payload.rows || []).forEach(function (row) {
                collected.set(row.id, row);
                order.push(row.id);
                if (row.is_match) { matches.add(row.id); }
            });
            state.nodes = collected;
            state.order = order;
            state.matches = matches;
            state.search = search;
            state.loaded = new Set(order);
            state.stats = new Map();
            indexTree();
            // The payload already contains the ancestors of every hit, so
            // expanding all of it is what brings the hit into view.
            state.expanded = new Set(order);
            render();
            setStatus(payload.warning || '');
        } catch (error) {
            showError(error.message);
            setStatus('');
        } finally {
            state.loading = false;
        }
    }

    function loadTree(search) {
        return search ? searchTree(search) : loadRoots();
    }

    function indexTree() {
        state.children = new Map();
        state.order.forEach(function (id) {
            const node = state.nodes.get(id);
            if (!node) { return; }
            const key = node.parent_id === null || node.parent_id === undefined
                ? 'root' : node.parent_id;
            if (!state.children.has(key)) { state.children.set(key, []); }
            state.children.get(key).push(id);
        });
    }

    function childIds(parentId) {
        const key = parentId === null || parentId === undefined ? 'root' : parentId;
        return state.children.get(key) || [];
    }

    function descendants(id) {
        const found = new Set();
        const walk = function (current) {
            childIds(current).forEach(function (child) {
                found.add(child);
                walk(child);
            });
        };
        walk(id);
        return found;
    }

    function rootIds() {
        const roots = childIds(null);
        if (roots.length) { return roots; }
        // A search result set may not have the roots in it (a hit deep in the
        // tree brings only its own ancestors), so fall back to the shallowest
        // nodes present.
        return state.order.filter(function (id) {
            const node = state.nodes.get(id);
            return node && !state.nodes.has(node.parent_id);
        });
    }

    // ------------------------------------------------------------------
    // Rendering
    // ------------------------------------------------------------------

    function render() {
        renderList();
        renderMap();
        const count = document.getElementById('treeNodeCount');
        if (count) { count.textContent = String(state.nodes.size); }
        const empty = document.getElementById('treeEmpty');
        if (empty) { empty.style.display = state.nodes.size ? 'none' : ''; }
    }

    function renderList() {
        const host = document.getElementById('treeRows');
        if (!host) { return; }
        host.textContent = '';
        const visible = [];

        const emit = function (id, depth) {
            const node = state.nodes.get(id);
            if (!node) { return; }
            visible.push(id);
            host.appendChild(rowFor(node, depth));
            if (state.expanded.has(id)) {
                childIds(id).forEach(function (child) { emit(child, depth + 1); });
            }
        };
        rootIds().forEach(function (id) { emit(id, 0); });

        if (!visible.length) {
            const message = document.createElement('div');
            message.className = 'tree-empty text-muted';
            message.textContent = t('noCategoriesFound', 'No categories found');
            host.appendChild(message);
        }
        if (state.selected !== null && !visible.includes(state.selected)) {
            state.selected = null;
        }
    }

    function rowFor(node, depth) {
        const row = document.createElement('div');
        row.className = 'tree-row';
        row.setAttribute('role', 'treeitem');
        row.dataset.id = String(node.id);
        row.dataset.depth = String(depth);
        row.tabIndex = -1;
        row.draggable = true;
        const kids = childIds(node.id);
        // With lazy levels the children of a node are not in memory until it is
        // opened, so "has children" has to come from the count the server sent
        // - deciding it from ``kids.length`` drew an unclickable dot on every
        // branch and made the tree impossible to expand.
        const hasChildren = kids.length > 0 || node.child_count > 0;
        row.setAttribute('aria-level', String(depth + 1));
        row.setAttribute('aria-expanded', hasChildren ? String(state.expanded.has(node.id)) : 'false');
        if (state.selected === node.id) { row.classList.add('selected'); }
        if (state.matches.has(node.id)) { row.classList.add('match'); }
        row.style.setProperty('--tree-depth', String(depth));

        const name = document.createElement('span');
        name.className = 'tree-col-name';
        name.style.paddingInlineStart = 'calc(var(--tree-indent) * ' + depth + ')';

        const toggle = document.createElement('button');
        toggle.type = 'button';
        toggle.className = 'tree-toggle';
        toggle.tabIndex = -1;
        if (hasChildren) {
            toggle.setAttribute('aria-label', state.expanded.has(node.id)
                ? t('collapse', 'Collapse') : t('expand', 'Expand'));
            toggle.textContent = state.expanded.has(node.id) ? '\u25BE' : '\u25B8';
            toggle.addEventListener('click', function (event) {
                event.stopPropagation();
                toggleNode(node.id);
            });
        } else {
            toggle.classList.add('tree-toggle-empty');
            toggle.textContent = '\u00B7';
            toggle.setAttribute('aria-hidden', 'true');
        }
        name.appendChild(toggle);

        const label = document.createElement('a');
        label.className = 'tree-name';
        label.href = '/categories/' + node.id + '/words';
        label.textContent = node.name;
        name.appendChild(label);

        row.appendChild(name);

        const stats = state.stats.get(node.id);
        const totalTerms = stats ? stats.total_terms : (node.total_terms || 0);
        row.appendChild(countCell(kids.length, t('children', 'Children')));
        row.appendChild(countCell(node.own_terms, t('words', 'Words')));
        row.appendChild(countCell(stats ? totalTerms : '\u2013', t('totalWords', 'Total Words')));

        const actions = document.createElement('span');
        actions.className = 'tree-col-actions';

        const deleteBtn = document.createElement('button');
        deleteBtn.type = 'button';
        deleteBtn.className = 'btn btn-sm btn-outline-danger';
        deleteBtn.title = t('delete', 'Delete');
        deleteBtn.setAttribute('aria-label', t('delete', 'Delete') + ': ' + node.name);
        deleteBtn.textContent = '\u2715';
        deleteBtn.addEventListener('click', function (event) {
            event.stopPropagation();
            deleteCategory(node.id, 'block');
        });
        actions.appendChild(deleteBtn);

        if (hasChildren) {
            const reparentBtn = document.createElement('button');
            reparentBtn.type = 'button';
            reparentBtn.className = 'btn btn-sm btn-outline-warning ms-1';
            reparentBtn.title = t('deleteReparent', 'Delete and move children up');
            reparentBtn.setAttribute('aria-label',
                t('deleteReparent', 'Delete and move children up') + ': ' + node.name);
            reparentBtn.textContent = '\u2934';
            reparentBtn.addEventListener('click', function (event) {
                event.stopPropagation();
                deleteCategory(node.id, 'reparent');
            });
            actions.appendChild(reparentBtn);
        }
        row.appendChild(actions);

        row.addEventListener('click', function () { selectNode(node.id); });
        row.addEventListener('focus', function () { selectNode(node.id, {keepFocus: true}); });
        wireDrag(row, node);
        return row;
    }

    function countCell(value, label) {
        const cell = document.createElement('span');
        cell.className = 'tree-col-count';
        cell.setAttribute('aria-label', label + ': ' + value);
        const badge = document.createElement('span');
        badge.className = 'badge bg-light text-dark';
        badge.textContent = String(value);
        cell.appendChild(badge);
        return cell;
    }

    function renderMap() {
        const host = document.getElementById('treeMap');
        if (!host) { return; }
        host.textContent = '';
        const roots = rootIds();
        if (!roots.length) {
            const empty = document.createElement('p');
            empty.className = 'text-muted';
            empty.textContent = t('noCategoriesFound', 'No categories found');
            host.appendChild(empty);
            return;
        }
        const seen = new Set();
        const column = function (ids, depth) {
            if (depth > 4) { return null; }
            const col = document.createElement('div');
            col.className = 'tree-map-column';
            ids.forEach(function (id) {
                if (seen.has(id)) { return; }
                seen.add(id);
                const node = state.nodes.get(id);
                if (!node) { return; }
                const card = document.createElement('div');
                card.className = 'tree-map-card';
                if (state.matches.has(id)) { card.classList.add('match'); }
                card.dataset.id = String(id);
                card.draggable = true;
                card.tabIndex = 0;
                const name = document.createElement('a');
                name.href = '/categories/' + id + '/words';
                name.textContent = node.name;
                card.appendChild(name);
                const meta = document.createElement('span');
                meta.className = 'tree-map-meta';
                const stats = state.stats.get(id);
                meta.textContent = t('words', 'Words') + ' ' + node.own_terms +
                    ' \u00B7 ' + t('totalWords', 'Total Words') + ' ' +
                    (stats ? stats.total_terms : '\u2013');
                card.appendChild(meta);
                wireDrag(card, node);
                card.addEventListener('click', function () { selectNode(id); });
                col.appendChild(card);
                const kids = childIds(id);
                if (kids.length && state.expanded.has(id)) {
                    const next = column(kids, depth + 1);
                    if (next) { col.appendChild(next); }
                }
            });
            return col;
        };
        const first = column(roots, 0);
        if (first) { host.appendChild(first); }
    }

    function selectNode(id, options) {
        state.selected = id;
        const node = state.nodes.get(id);
        const crumb = document.getElementById('treeBreadcrumb');
        if (crumb && node) {
            crumb.textContent = '';
            const path = document.createElement('span');
            path.className = 'tree-breadcrumb-path';
            path.textContent = node.path;
            crumb.appendChild(path);
            const depth = document.createElement('span');
            depth.className = 'text-muted ms-2';
            depth.textContent = t('depth', 'Level') + ' ' + node.depth;
            crumb.appendChild(depth);
        }
        document.querySelectorAll('#treeRows .tree-row.selected').forEach(function (el) {
            el.classList.remove('selected');
        });
        const row = document.querySelector('#treeRows .tree-row[data-id="' + id + '"]');
        if (row) {
            row.classList.add('selected');
            if (options && options.keepFocus) { return; }
            row.focus({preventScroll: true});
        }
    }

    function setStatus(text) {
        const status = document.getElementById('treeStatus');
        if (status) { status.textContent = text || ''; }
    }

    function showError(message) {
        const box = document.getElementById('treeError');
        if (!box) { return; }
        if (!message) {
            box.style.display = 'none';
            box.textContent = '';
            return;
        }
        box.textContent = message;
        box.style.display = '';
        window.clearTimeout(showError._timer);
        showError._timer = window.setTimeout(function () { showError(''); }, 6000);
    }

    function toast(message, kind) {
        if (typeof window.showToast === 'function') {
            window.showToast(message, kind || 'info');
            return;
        }
        if (kind === 'error') { showError(message); } else { setStatus(message); }
    }

    // ------------------------------------------------------------------
    // Expand / collapse, keyboard, drag and drop
    // ------------------------------------------------------------------

    function revealAncestors(id) {
        let current = id;
        let guard = 0;
        while (current !== null && current !== undefined && guard < 1000) {
            state.expanded.add(current);
            const node = state.nodes.get(current);
            current = node ? node.parent_id : null;
            guard += 1;
        }
    }

    function toggleNode(id) {
        if (state.expanded.has(id)) {
            state.expanded.delete(id);
            render();
            return;
        }
        state.expanded.add(id);
        render();
        // Draw the children as soon as they arrive; the node is already open so
        // the row does not jump around.
        loadChildren(id).then(function () { render(); });
        const node = state.nodes.get(id);
        if (node && node.child_count > 0) { loadStats(id); }
    }

    function setTreeView(view) {
        state.view = view;
        const list = document.getElementById('treeViewList');
        const map = document.getElementById('treeViewMap');
        const listBtn = document.getElementById('viewListBtn');
        const mapBtn = document.getElementById('viewMapBtn');
        if (list) { list.style.display = view === 'list' ? '' : 'none'; }
        if (map) { map.style.display = view === 'map' ? '' : 'none'; }
        if (listBtn) {
            listBtn.classList.toggle('active', view === 'list');
            listBtn.setAttribute('aria-pressed', String(view === 'list'));
        }
        if (mapBtn) {
            mapBtn.classList.toggle('active', view === 'map');
            mapBtn.setAttribute('aria-pressed', String(view === 'map'));
        }
    }

    function expandToDepth(depth) {
        const value = Number(depth);
        if (value === 0) {
            state.expanded = new Set();
            render();
            return;
        }
        state.expanded = new Set();
        state.order.forEach(function (id) {
            const node = state.nodes.get(id);
            if (node && node.depth < value) { state.expanded.add(id); }
        });
        render();
        if (state.expanded.size) { loadExpandedBranches(); }
    }

    /**
     * Fetch the children of every expanded node that has not been loaded yet.
     *
     * With lazy levels, "expand all" is a series of level reads; it stops after
     * EXPAND_ALL_CAP nodes rather than turning the page into a full-tree fetch,
     * and says so when it stops.
     */
    const EXPAND_ALL_CAP = 2000;

    async function loadExpandedBranches() {
        let fetched = 0;
        let progressed = true;
        while (progressed && fetched < EXPAND_ALL_CAP) {
            progressed = false;
            const pending = Array.from(state.expanded).filter(function (id) {
                const node = state.nodes.get(id);
                return node && node.child_count > 0 && !state.loaded.has(id);
            });
            for (const id of pending) {
                if (fetched >= EXPAND_ALL_CAP) { break; }
                await loadChildren(id);
                fetched += 1;
                progressed = true;
            }
            if (pending.length) { render(); }
        }
        render();
        if (fetched >= EXPAND_ALL_CAP) {
            setStatus(t('expandCap',
                'Stopped expanding after 2000 branches; search or open a subtree instead.'));
        }
    }

    function visibleRows() {
        return Array.prototype.slice.call(document.querySelectorAll('#treeRows .tree-row'));
    }

    function onKeyDown(event) {
        const row = event.target.closest ? event.target.closest('.tree-row') : null;
        if (!row) { return; }
        const id = Number(row.dataset.id);
        const rows = visibleRows();
        const index = rows.indexOf(row);
        if (event.key === 'ArrowDown' && index < rows.length - 1) {
            event.preventDefault();
            rows[index + 1].focus();
        } else if (event.key === 'ArrowUp' && index > 0) {
            event.preventDefault();
            rows[index - 1].focus();
        } else if (event.key === 'ArrowRight') {
            event.preventDefault();
            if (!state.expanded.has(id)) { toggleNode(id); }
        } else if (event.key === 'ArrowLeft') {
            event.preventDefault();
            if (state.expanded.has(id)) { toggleNode(id); }
        } else if (event.key === 'Home' && rows.length) {
            event.preventDefault();
            rows[0].focus();
        } else if (event.key === 'End' && rows.length) {
            event.preventDefault();
            rows[rows.length - 1].focus();
        } else if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            const node = state.nodes.get(id);
            if (node) { window.location.href = '/categories/' + id + '/words'; }
        }
    }

    let dragging = null;

    function wireDrag(element, node) {
        element.addEventListener('dragstart', function (event) {
            dragging = node.id;
            element.classList.add('dragging');
            if (event.dataTransfer) {
                event.dataTransfer.effectAllowed = 'move';
                event.dataTransfer.setData('text/plain', String(node.id));
            }
        });
        element.addEventListener('dragend', function () {
            element.classList.remove('dragging');
            document.querySelectorAll('.drop-target').forEach(function (el) {
                el.classList.remove('drop-target');
            });
            dragging = null;
        });
        element.addEventListener('dragover', function (event) {
            if (dragging === null || dragging === node.id) { return; }
            if (descendants(dragging).has(node.id)) { return; }
            event.preventDefault();
            element.classList.add('drop-target');
            element.title = t('dropHere', 'Drop to make this the parent');
        });
        element.addEventListener('dragleave', function () {
            element.classList.remove('drop-target');
        });
        element.addEventListener('drop', async function (event) {
            event.preventDefault();
            element.classList.remove('drop-target');
            const dragged = dragging !== null ? dragging : Number(event.dataTransfer.getData('text/plain'));
            if (!dragged || dragged === node.id) { return; }
            await moveCategory(dragged, node.id);
        });
    }

    // ------------------------------------------------------------------
    // Writes
    // ------------------------------------------------------------------

    async function moveCategory(id, parentId) {
        try {
            await apiFetch(API + '/' + id + '/move', {
                method: 'POST',
                body: JSON.stringify({parent_id: parentId})
            });
            toast(t('categoryMoved', 'Category moved'), 'success');
            await loadTree(state.search);
        } catch (error) {
            showError(t('moveFailed', 'Could not move the category') + ': ' + error.message);
        }
    }

    async function deleteCategory(id, mode) {
        if (!window.confirm(t('confirmDelete', 'Are you sure you want to delete this category?'))) {
            return;
        }
        try {
            await apiFetch(API + '/' + id + '/delete', {
                method: 'POST',
                body: JSON.stringify({mode: mode})
            });
            toast(t('categoryDeleted', 'Category deleted'), 'success');
            await loadTree(state.search);
        } catch (error) {
            if (mode === 'block' && error.payload && error.payload.code === 'has_children') {
                showError(t('hasChildren',
                    'This category has children. Move them first or choose "reparent".'));
            } else {
                showError(error.message);
            }
        }
    }

    // ------------------------------------------------------------------
    // Modal
    // ------------------------------------------------------------------


/**
 * Close a Bootstrap dialog and make sure it actually closes.
 *
 * ``bootstrap.Modal.getOrCreateInstance(el).hide()`` returns without touching
 * the DOM when the live instance believes the dialog is not shown - which is
 * what happens for this bundle after ``show()``: ``.show`` and the backdrop
 * stay while ``_isShown`` is false, so the dialog covered the tree after every
 * save. Calling hide() first keeps the normal path; if the element is still
 * marked shown a moment later the presentation state is cleared here.
 */
function closeModal(id) {
    const element = document.getElementById(id);
    if (!element || typeof bootstrap === 'undefined') { return; }
    const generation = modalGeneration;
    try {
        bootstrap.Modal.getOrCreateInstance(element).hide();
    } catch (error) {
        console.warn('modal hide failed:', error);
    }
    window.setTimeout(function () {
        if (generation !== modalGeneration) { return; }
        if (!element.classList.contains('show')) { return; }
        element.classList.remove('show');
        element.setAttribute('aria-hidden', 'true');
        element.style.display = 'none';
        document.querySelectorAll('.modal-backdrop').forEach(function (backdrop) {
            backdrop.remove();
        });
        document.body.classList.remove('modal-open');
        document.body.style.removeProperty('padding-right');
    }, 400);
}

    async function fillParentOptions() {
        const parent = document.getElementById('treeParentSelect');
        const adopt = document.getElementById('treeAdoptSelect');
        if (!parent && !adopt) { return; }

        // The page holds one level at a time, so the selector cannot read the
        // tree from memory. It asks the whole-tree endpoint for its first page
        // (the selector is not a navigation aid for a hundred thousand nodes)
        // and falls back to what is loaded if that request fails.
        let rows = state.order.map(function (id) {
            const node = state.nodes.get(id);
            return {id: id, name: node.name, depth: node.depth};
        });
        // Paint what the page already has before the request: on a large
        // catalogue the whole-tree first page takes about a second, and an
        // operator who opens the dialog and immediately picks a parent was
        // looking at an empty selector.
        paintParentOptions(rows);
        try {
            const payload = await apiFetch(API + '?limit=500&total=0');
            if (payload && payload.rows && payload.rows.length) {
                rows = payload.rows;
            }
        } catch (error) {
            console.warn('parent selector: falling back to the loaded level', error);
        }
        paintParentOptions(rows);
    }

    function paintParentOptions(rows) {
        const parent = document.getElementById('treeParentSelect');
        const adopt = document.getElementById('treeAdoptSelect');
        if (parent) {
            parent.textContent = '';
            const none = document.createElement('option');
            none.value = '';
            none.textContent = t('none', 'None');
            parent.appendChild(none);
            rows.forEach(function (row) {
                const el = document.createElement('option');
                el.value = String(row.id);
                el.textContent = '\u00A0\u00A0'.repeat(Math.max(0, row.depth - 1)) + row.name;
                parent.appendChild(el);
            });
        }
        if (adopt) {
            adopt.textContent = '';
            rows.filter(function (row) { return row.depth <= 2; }).forEach(function (row) {
                const el = document.createElement('option');
                el.value = String(row.id);
                el.textContent = '\u00A0\u00A0'.repeat(Math.max(0, row.depth - 1)) + row.name;
                adopt.appendChild(el);
            });
        }
    }

    function syncMode() {
        const mode = currentMode();
        const parentGroup = document.getElementById('treeParentGroup');
        const adoptGroup = document.getElementById('treeAdoptGroup');
        const childrenGroup = document.getElementById('treeChildrenGroup');
        if (parentGroup) { parentGroup.style.display = mode === 'child' ? '' : 'none'; }
        if (adoptGroup) { adoptGroup.style.display = mode === 'parent-above' ? '' : 'none'; }
        if (childrenGroup) { childrenGroup.style.display = mode === 'parent-above' ? 'none' : ''; }
    }

    function currentMode() {
        const checked = document.querySelector('input[name="treeMode"]:checked');
        return checked ? checked.value : 'root';
    }

    function openTreeCategoryModal(parentId) {
        const modalElement = document.getElementById('treeCategoryModal');
        if (!modalElement || typeof bootstrap === 'undefined') {
            showError('Bootstrap is not loaded yet; reload the page.');
            return;
        }
        const nameInput = document.getElementById('treeCategoryName');
        const childrenInput = document.getElementById('treeChildren');
        if (nameInput) { nameInput.value = ''; }
        if (childrenInput) { childrenInput.value = ''; }
        fillParentOptions();
        const rootRadio = document.getElementById('treeModeRoot');
        const childRadio = document.getElementById('treeModeChild');
        if (parentId) {
            if (childRadio) { childRadio.checked = true; }
            const parent = document.getElementById('treeParentSelect');
            if (parent) { parent.value = String(parentId); }
        } else if (rootRadio) {
            rootRadio.checked = true;
        }
        syncMode();
        modalGeneration += 1;
        bootstrap.Modal.getOrCreateInstance(modalElement).show();
    }

    async function saveTreeCategory() {
        const nameInput = document.getElementById('treeCategoryName');
        const name = nameInput ? nameInput.value.trim() : '';
        if (!name) {
            showError(t('nameRequired', 'Category name is required'));
            return;
        }
        const mode = currentMode();
        try {
            if (mode === 'parent-above') {
                const adopt = document.getElementById('treeAdoptSelect');
                const childIds = adopt ? Array.from(adopt.selectedOptions).map(function (o) {
                    return Number(o.value);
                }) : [];
                if (!childIds.length) {
                    showError(t('adoptRequired', 'Choose at least one category to move'));
                    return;
                }
                await apiFetch(API + '/parent-above', {
                    method: 'POST',
                    body: JSON.stringify({name: name, child_ids: childIds})
                });
            } else {
                const body = {name: name, mode: mode};
                if (mode === 'child') {
                    const parent = document.getElementById('treeParentSelect');
                    if (!parent || !parent.value) {
                        showError(t('parentRequired', 'Choose a parent category'));
                        return;
                    }
                    body.parent_id = Number(parent.value);
                }
                const childrenInput = document.getElementById('treeChildren');
                const lines = childrenInput ? childrenInput.value.split('\n')
                    .map(function (line) { return line.trim(); })
                    .filter(Boolean) : [];
                if (lines.length) { body.children = lines; }
                await apiFetch(API, {method: 'POST', body: JSON.stringify(body)});
            }
            closeModal('treeCategoryModal');
            showError('');
            toast(t('categoryAdded', 'Category added successfully'), 'success');
            await loadTree(state.search);
            // Reveal what was just created: a child filed under a collapsed
            // parent would otherwise be created and then invisible, which reads
            // as a failure. The parent and its ancestors are expanded.
            if (mode === 'child' && body.parent_id) {
                revealAncestors(body.parent_id);
            }
            render();
        } catch (error) {
            showError(error.message);
        }
    }

    // ------------------------------------------------------------------
    // CSV export
    // ------------------------------------------------------------------

    function csvCell(value) {
        const text = value === null || value === undefined ? '' : String(value);
        return '"' + text.replace(/"/g, '""') + '"';
    }

    async function exportTreeCsv() {
        setStatus(t('exporting', 'Preparing CSV...'));
        try {
            const rows = [];
            let cursor = null;
            let pages = 0;
            do {
                const query = new URLSearchParams({limit: String(PAGE), total: '0'});
                if (state.search) { query.set('search', state.search); }
                if (cursor) { query.set('cursor', cursor); }
                const payload = await apiFetch(API + '?' + query.toString());
                rows.push.apply(rows, payload.rows || []);
                cursor = payload.next_cursor;
                pages += 1;
            } while (cursor && pages < 400);

            const header = [t('id', 'Id'), t('name', 'Category Name'), t('path', 'Path'),
                t('depth', 'Level'), t('children', 'Children'), t('words', 'Words'),
                t('totalWords', 'Total Words')];
            const lines = [header.map(csvCell).join(',')];
            rows.forEach(function (row) {
                lines.push([row.id, row.name, row.path, row.depth, row.child_count,
                    row.own_terms, row.total_terms].map(csvCell).join(','));
            });
            const blob = new Blob(['\uFEFF' + lines.join('\r\n')],
                {type: 'text/csv;charset=utf-8;'});
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = 'category-hierarchy-' +
                new Date().toISOString().slice(0, 10) + '.csv';
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            URL.revokeObjectURL(url);
            setStatus(t('exported', 'CSV downloaded') + ' (' + rows.length + ')');
        } catch (error) {
            setStatus('');
            showError(error.message);
        }
    }

    // ------------------------------------------------------------------
    // Wiring
    // ------------------------------------------------------------------

    function debounce(fn, delay) {
        let timer = null;
        return function () {
            const args = arguments;
            window.clearTimeout(timer);
            timer = window.setTimeout(function () { fn.apply(null, args); }, delay);
        };
    }

    document.addEventListener('DOMContentLoaded', function () {
        if (window.performance && performance.mark) {
            performance.mark('category-tree:load-start');
        }
        const dataEl = document.getElementById('category-tree-page-data');
        if (dataEl) {
            try {
                const data = JSON.parse(dataEl.textContent.trim());
                translations = (data && data.translations) || {};
            } catch (error) {
                translations = {};
            }
        }

        const search = document.getElementById('treeSearch');
        if (search) {
            search.addEventListener('input', debounce(function () {
                loadTree(search.value.trim());
            }, 200));
        }
        document.querySelectorAll('input[name="treeMode"]').forEach(function (radio) {
            radio.addEventListener('change', syncMode);
        });
        const rowsHost = document.getElementById('treeRows');
        if (rowsHost) { rowsHost.addEventListener('keydown', onKeyDown); }
        loadTree('');
    });

    window.setTreeView = setTreeView;
    window.expandToDepth = expandToDepth;
    window.openTreeCategoryModal = openTreeCategoryModal;
    window.saveTreeCategory = saveTreeCategory;
    window.exportTreeCsv = exportTreeCsv;
    window.reloadTree = function () { loadTree(state.search); };
    window.clearTreeSearch = function () {
        const search = document.getElementById('treeSearch');
        if (search) { search.value = ''; }
        loadTree('');
    };
})();
