/**
 * Horizon & Signal Explorer page.
 *
 * Every count, bucket, facet and page comes from
 * GET /api/signals/horizon, /api/signals and /api/signals/<id>.
 * The browser builds a query string and renders the answer — it never
 * recomputes buckets or filters the corpus itself.
 */

import { enhanceMultiSelects, refreshMultiSelect } from '../modules/ui/multi-select-dropdown.js';
import { onPageTeardown } from '../modules/core/page-lifecycle.js';
import { loadMoreBlockHtml } from '../modules/rendering/unified-pagination.js';

let DATA = {};
let L = {};

const state = {
    tab: 'horizon',
    pageSize: 50,
    sort: 'event_date',
    horizon: { buckets: [], offset: 0, total: 0, items: [] },
    explorer: { offset: 0, total: 0, contents: 0, items: [] },
    savedLoaded: false,
    loadingMore: false,
};

function readSignalsPagePayload() {
    const dataEl = document.getElementById('signals-page-data');
    const labelsEl = document.getElementById('signals-page-labels');
    if (!dataEl || !labelsEl) {
        throw new Error('Signals page payload missing (signals-page-data / signals-page-labels)');
    }
    DATA = JSON.parse(dataEl.textContent);
    L = JSON.parse(labelsEl.textContent);
}

function resetSignalsState() {
    state.tab = 'horizon';
    state.pageSize = Math.min(50, DATA.max_page_size || 200);
    state.sort = 'event_date';
    state.horizon = { buckets: [], offset: 0, total: 0, items: [] };
    state.explorer = { offset: 0, total: 0, contents: 0, items: [] };
    state.savedLoaded = false;
    state.loadingMore = false;
}

function fmt(template, values) {
    return template.replace(/%\((\w+)\)s/g, (_, k) => (values[k] ?? ''));
}

function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
        if (v === null || v === undefined || v === false) continue;
        if (k === 'class') node.className = v;
        else if (k === 'text') node.textContent = v;
        else node.setAttribute(k, v === true ? '' : v);
    }
    for (const child of children) {
        if (child === null || child === undefined) continue;
        node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
}

function docText(value, extraClass = '') {
    return el('bdi', { dir: 'auto', class: extraClass, text: value ?? '' });
}

function label(value) {
    if (value === 'unrecorded') return L.unrecorded;
    if (value === 'unspecified') return L.unspecified;
    const typeLabel = L[`signal_type_${value}`];
    if (typeLabel) return typeLabel;
    return value;
}

function entityName(map, id) {
    if (id == null || id === '') return L.source_side_unknown || 'Unknown';
    return map[String(id)] || `${id}`;
}

function provenanceCells(item) {
    const doc = item.document;
    if (!doc) {
        return [el('td', { text: '—' }), el('td', { text: '—' })];
    }
    return [
        el('td', { class: 'small' }, docText(entityName(DATA.sources || {}, doc.source_id))),
        el('td', { class: 'small' }, docText(entityName(DATA.sides || {}, doc.side_id))),
    ];
}

function checkboxGroup(containerId, name, values) {
    const box = document.getElementById(containerId);
    if (!box) return;
    box.replaceChildren();
    for (const value of values) {
        const id = `f_${name}_${value}`;
        box.append(el('div', { class: 'form-check form-check-inline m-0' },
            el('input', { class: 'form-check-input', type: 'checkbox', id, name, value }),
            el('label', { class: 'form-check-label small', for: id, text: label(value) })));
    }
}

function fillSelect(selectId, entries) {
    const select = document.getElementById(selectId);
    if (!select) return;
    // Keep existing options for single-selects that already have a blank choice.
    if (!select.multiple) {
        const keep = Array.from(select.options).filter((o) => !o.value);
        select.replaceChildren(...keep);
    } else {
        select.replaceChildren();
    }
    for (const [value, text] of entries) select.append(el('option', { value, text }));
}

function enhanceSignalMultiSelects() {
    enhanceMultiSelects(document.getElementById('signalFilters') || document, {
        fSource: {
            placeholder: L.all_sources || 'All sources',
            markerForOption: () => '#0ea5e9',
            iconForOption: () => 'bi-building',
            onChange: () => renderActiveFilters(),
        },
        fSide: {
            placeholder: L.all_sides || 'All sides',
            markerForOption: () => '#8b5cf6',
            iconForOption: () => 'bi-diagram-3',
            onChange: () => renderActiveFilters(),
        },
    });
    ['fSource', 'fSide'].forEach((id) => {
        const select = document.getElementById(id);
        if (select) refreshMultiSelect(select);
    });
}

function filterParams() {
    const form = document.getElementById('signalFilters');
    const params = new URLSearchParams();
    const fd = new FormData(form);
    const modePair = {
        evidence_mode: 'evidence_text',
        surface_mode: 'surface_text',
        value_mode: 'value_text',
    };
    for (const [key, value] of fd.entries()) {
        const v = String(value).trim();
        if (!v) continue;
        // identifier_kind belongs here for the same reason as the ids: the
        // kind presets write "ipv4,ipv6" into one hidden input
        // (applyKindPreset), and the API takes one value per parameter.
        if (key === 'category_id' || key === 'keyword_id' || key === 'place_key' || key === 'hash_id'
            || key === 'identifier_kind') {
            v.split(/[\s,]+/).filter(Boolean).forEach((id) => params.append(key, id));
            continue;
        }
        if (modePair[key]) {
            const text = String(fd.get(modePair[key]) || '').trim();
            if (text) params.append(key, v);
            continue;
        }
        params.append(key, v);
    }
    return params;
}

async function getJson(url) {
    const resp = await fetch(url, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
    let body = null;
    try { body = await resp.json(); } catch (e) { body = null; }
    if (!resp.ok || !body || body.success === false) {
        const message = body && body.error ? body.error.message : `${L.request_failed} (${resp.status})`;
        throw new Error(message);
    }
    return body;
}

function showError(message) {
    const box = document.getElementById('signalError');
    if (!box) return;
    box.querySelector('p').textContent = message || '';
    box.classList.toggle('d-none', !message);
}

function currentView() {
    return state.tab === 'horizon' ? state.horizon : state.explorer;
}

/**
 * The Load More control for one table: the shared block (the same markup and
 * engine every other list uses), rendered from the view's offset state.
 * `failed` shows the engine's own retry message.
 */
function renderLoadMore(hostId, tableId, view, { failed = false, loading = false } = {}) {
    const host = document.getElementById(hostId);
    if (!host) return;
    const shown = view.items.length;
    const total = view.total || 0;
    if (!total || shown >= total) {
        host.innerHTML = '';
        return;
    }
    host.innerHTML = loadMoreBlockHtml({
        tableId, page: 1, totalPages: 2, total, shown,
        label: L.show_more || 'Show More',
    });
    const button = host.querySelector('[data-ut-load-more] button');
    if (button && loading) button.disabled = true;
    const error = host.querySelector('[data-ut-load-error]');
    if (error && failed) error.hidden = false;
}

/** Route the table's Load More clicks to this page's offset loader. */
function wireLoadMore(hostId, tableId, view, load) {
    if (!window.UnifiedTable || typeof UnifiedTable.onLoadMore !== 'function') return;
    UnifiedTable.onLoadMore(tableId, async () => {
        if (state.loadingMore) return;
        state.loadingMore = true;
        renderLoadMore(hostId, tableId, view, { loading: true });
        try {
            await load();
        } catch (err) {
            renderLoadMore(hostId, tableId, view, { failed: true });
            showError(err.message);
        } finally {
            state.loadingMore = false;
        }
    });
}

function documentCell(item) {
    const doc = item.document;
    if (!doc) return el('td', { text: `#${item.hash_id}` });
    const link = el('a', { href: `/file/${doc.path_id}`, class: 'text-decoration-none' }, docText(doc.file_name));
    const extra = doc.occurrences > 1
        ? el('div', { class: 'small text-muted', text: fmt(L.occurrences, { n: doc.occurrences }) })
        : null;
    return el('td', {}, link, extra);
}

function confidenceBadge(value) {
    const tone = { high: 'success', medium: 'warning', low: 'secondary' }[value] || 'light text-dark';
    return el('span', { class: `badge bg-${tone}`, text: label(value || 'unrecorded') });
}

function evidenceCell(item) {
    return el('td', { class: 'small' }, docText(item.sentence || item.surface));
}

function detailButton(item, content) {
    const btn = el('button', { type: 'button', class: 'btn btn-link btn-sm p-0 text-start' }, content);
    btn.addEventListener('click', () => openDetail(item.signal_id));
    return btn;
}

function csvCell(value) {
    let text = String(value == null ? '' : value);
    if (/^[\s\u0000-\u001F]*[=+\-@]/.test(text)) text = `'${text}`;
    return `"${text.replace(/"/g, '""')}"`;
}

function rowsToCsv(items) {
    const headers = state.tab === 'horizon'
        ? ['when_from', 'when_to', 'bucket', 'surface', 'evidence', 'confidence', 'document', 'signal_id']
        : ['surface', 'detector', 'signal_type', 'value', 'evidence', 'confidence', 'method',
            'source', 'side', 'document', 'signal_id'];
    const rows = items.map((item) => {
        const docName = item.document?.file_name || '';
        if (state.tab === 'horizon') {
            return [
                item.date_from, item.date_to, item.bucket, item.surface,
                item.sentence || item.surface, item.confidence, docName, item.signal_id,
            ].map(csvCell).join(',');
        }
        const value = item.detector === 'places' && item.places
            ? item.places.map((p) => p.label).join(' / ')
            : (item.date_from
                ? (item.date_from === item.date_to ? item.date_from : `${item.date_from} – ${item.date_to}`)
                : item.value);
        const doc = item.document;
        return [
            item.surface, item.detector, item.signal_type, value,
            item.sentence || item.surface, item.confidence, item.method,
            entityName(DATA.sources || {}, doc?.source_id),
            entityName(DATA.sides || {}, doc?.side_id),
            docName, item.signal_id,
        ].map(csvCell).join(',');
    });
    return `\uFEFF${[headers.join(','), ...rows].join('\r\n')}`;
}

function downloadCsv(csv, suffix) {
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `signals_${state.tab}_${suffix}_${new Date().toISOString().slice(0, 10)}.csv`;
    a.hidden = true;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function exportLoaded() {
    const items = currentView().items || [];
    if (!items.length) {
        showError(L.export_empty || 'Nothing to export yet. Apply filters and load results first.');
        return;
    }
    showError('');
    downloadCsv(rowsToCsv(items), 'loaded');
}

async function exportAllMatching() {
    const EXPORT_CAP = Math.min(5000, (DATA.max_page_size || 200) * 25);
    const pageSize = Math.min(200, DATA.max_page_size || 200);
    showError(L.export_all_progress || 'Exporting matching signals…');
    const collected = [];
    let offset = 0;
    let total = Infinity;
    try {
        while (offset < total && collected.length < EXPORT_CAP) {
            const params = filterParams();
            if (state.tab === 'horizon') {
                params.delete('detector');
                state.horizon.buckets.forEach((b) => params.append('bucket', b));
            }
            params.set('sort', state.sort);
            params.set('limit', String(pageSize));
            params.set('offset', String(offset));
            const endpoint = state.tab === 'horizon' ? '/api/signals/horizon' : '/api/signals';
            const body = await getJson(`${endpoint}?${params}`);
            total = body.total || 0;
            collected.push(...(body.items || []));
            if (!body.items?.length) break;
            offset += body.items.length;
        }
        if (!collected.length) {
            showError(L.export_empty || 'Nothing to export yet. Apply filters and load results first.');
            return;
        }
        downloadCsv(rowsToCsv(collected), 'all');
        if (collected.length >= EXPORT_CAP && total > EXPORT_CAP) {
            showError(fmt(L.export_all_cap || 'Export stopped at %(n)s matching signals (safety cap).', { n: EXPORT_CAP }));
        } else {
            showError('');
        }
    } catch (err) {
        showError(err.message);
    }
}

function modeLabel(mode) {
    return L[`mode_${mode}`] || mode;
}

function renderActiveFilters() {
    const box = document.getElementById('signalsActiveFilters');
    if (!box) return;
    const chips = [];
    const form = document.getElementById('signalFilters');
    const pushChip = (labelText, value) => {
        chips.push(el('span', { class: 'signals-filter-chip' },
            el('strong', { text: `${labelText}: ` }),
            document.createTextNode(value)));
    };

    const evidence = form.evidence_text?.value?.trim();
    if (evidence) pushChip(L.chip_evidence || 'Evidence', `${modeLabel(form.evidence_mode.value)} “${evidence}”`);
    const surface = form.surface_text?.value?.trim();
    if (surface) pushChip(L.chip_surface || 'Entity', `${modeLabel(form.surface_mode.value)} “${surface}”`);
    const value = form.value_text?.value?.trim();
    if (value) pushChip(L.chip_value || 'Value', `${modeLabel(form.value_mode.value)} “${value}”`);

    const selectedNames = (selectId, labelText) => {
        const select = document.getElementById(selectId);
        if (!select) return;
        const names = Array.from(select.selectedOptions).map((o) => o.textContent.trim()).filter(Boolean);
        if (names.length) pushChip(labelText, names.join(', '));
    };
    selectedNames('fSource', L.chip_source || 'Source');
    selectedNames('fSide', L.chip_side || 'Side');

    const placeKeys = form.place_key?.value?.trim();
    if (placeKeys) pushChip(L.chip_place || 'Place key', placeKeys);

    ['confidence', 'language', 'calendar', 'detector', 'signal_type', 'resolution', 'orientation'].forEach((name) => {
        const checked = Array.from(form.querySelectorAll(`input[name="${name}"]:checked`)).map((i) => label(i.value));
        if (checked.length) pushChip(L[`facet_${name}`] || name, checked.join(', '));
    });

    box.replaceChildren();
    if (!chips.length) {
        box.append(el('span', { class: 'small text-muted', text: L.no_active_filters || 'No filters applied' }));
        return;
    }
    box.append(el('span', { class: 'signals-active-label small fw-semibold', text: L.active_filters || 'Active filters' }), ...chips);
}

// ---------------------------------------------------------------------------
// Horizon
// ---------------------------------------------------------------------------

function appendHorizonRows(items) {
    const tbody = document.getElementById('horizonRows');
    for (const item of items) {
        const when = item.date_from === item.date_to ? item.date_from : `${item.date_from} – ${item.date_to}`;
        tbody.append(el('tr', {},
            el('td', { class: 'text-nowrap' }, detailButton(item, when)),
            el('td', { text: L[`bucket_${item.bucket}`] || item.bucket }),
            el('td', {}, docText(item.surface), el('div', { class: 'small text-muted', text: [item.calendar, item.language].filter(Boolean).join(' · ') })),
            evidenceCell(item),
            el('td', {}, confidenceBadge(item.confidence)),
            documentCell(item)));
    }
}

async function loadHorizon({ append = false } = {}) {
    const params = filterParams();
    params.delete('detector');
    state.horizon.buckets.forEach((b) => params.append('bucket', b));
    params.set('sort', state.sort);
    params.set('limit', String(state.pageSize));
    params.set('offset', String(append ? state.horizon.items.length : 0));
    const body = await getJson(`/api/signals/horizon?${params}`);

    if (!append) {
        state.horizon.items = [];
        renderBuckets(body);
        renderHorizonNotices(body);
        const tbody = document.getElementById('horizonRows');
        tbody.replaceChildren();
        if (!body.items.length) {
            tbody.append(el('tr', {}, el('td', { colspan: 6, class: 'text-muted', text: L.no_signals })));
        }
    }

    state.horizon.total = body.total;
    state.horizon.offset = body.offset;
    state.horizon.items = append ? state.horizon.items.concat(body.items) : body.items.slice();
    if (body.items.length) appendHorizonRows(body.items);

    const summary = document.getElementById('explorerSummary');
    if (summary && state.tab === 'horizon') {
        summary.textContent = fmt(L.page, {
            from: state.horizon.items.length ? 1 : 0,
            to: state.horizon.items.length,
            total: body.total,
        });
    }

    wireLoadMore('horizonPager', 'horizonTable', state.horizon, () => loadHorizon({ append: true }));
    renderLoadMore('horizonPager', 'horizonTable', state.horizon);
    renderActiveFilters();
}

function renderBuckets(body) {
    const row = document.getElementById('horizonBuckets');
    row.replaceChildren();
    for (const b of body.buckets) {
        const active = body.listed_buckets.includes(b.key);
        const range = [b.from, b.to].filter(Boolean).join(' → ');
        const card = el('button', {
            type: 'button',
            class: `signals-bucket${active ? ' is-active' : ''}`,
            'aria-pressed': active ? 'true' : 'false',
        },
            el('div', { class: 'signals-bucket-title', text: L[`bucket_${b.key}`] || b.key }),
            el('div', { class: 'signals-bucket-count', text: b.signals }),
            el('div', { class: 'signals-bucket-meta', text: `${b.contents} ${L.documents}` }),
            el('div', { class: 'signals-bucket-range', text: range }));
        card.addEventListener('click', () => {
            state.horizon.buckets = state.horizon.buckets.length === 1 && state.horizon.buckets[0] === b.key
                ? []
                : [b.key];
            state.horizon.offset = 0;
            run();
        });
        // Wrapper keeps the runtime harness's cards[n].querySelector('button') contract.
        row.append(el('div', { class: 'signals-bucket-wrap' }, card));
    }
}

function renderHorizonNotices(body) {
    const box = document.getElementById('horizonNotices');
    box.replaceChildren();
    const u = body.undated;
    if (u.ambiguous + u.unresolved > 0) {
        box.append(el('div', { class: 'alert alert-info py-2 mb-2 small',
            text: fmt(L.undated, { n: u.ambiguous + u.unresolved, a: u.ambiguous, u: u.unresolved }) }));
    }
    const c = body.coverage;
    if (c.not_measured > 0) {
        box.append(el('div', { class: 'alert alert-warning py-2 mb-2 small', text: fmt(L.not_measured, {
            n: c.not_measured, t: c.matching_contents, never: c.never_analysed, stale: c.stale_version, failed: c.failed }) }));
    } else if (c.matching_contents > 0) {
        box.append(el('div', { class: 'small text-muted mb-2', text: fmt(L.all_measured, { t: c.matching_contents }) }));
    }
    box.append(el('div', { class: 'small text-muted', text: `${L.fingerprint}: ${body.query_fingerprint.slice(0, 16)}` }));
}

// ---------------------------------------------------------------------------
// Explorer
// ---------------------------------------------------------------------------

function appendExplorerRows(items) {
    const tbody = document.getElementById('explorerRows');
    for (const item of items) {
        const value = item.detector === 'places' && item.places
            ? item.places.map((p) => p.label).join(' / ')
            : (item.date_from
                ? (item.date_from === item.date_to ? item.date_from : `${item.date_from} – ${item.date_to}`)
                : item.value);
        tbody.append(el('tr', {},
            el('td', {}, detailButton(item, docText(item.surface)), el('div', { class: 'small text-muted', text: `${item.detector} · ${item.signal_type}` })),
            el('td', { class: 'small' }, docText(value), el('div', { class: 'small text-muted', text: item.resolution })),
            evidenceCell(item),
            el('td', {}, confidenceBadge(item.confidence)),
            el('td', { class: 'small', text: item.method || L.unrecorded }),
            ...provenanceCells(item),
            documentCell(item)));
    }
}

async function loadExplorer({ append = false } = {}) {
    const params = filterParams();
    params.set('sort', state.sort);
    params.set('limit', String(state.pageSize));
    params.set('offset', String(append ? state.explorer.items.length : 0));
    const body = await getJson(`/api/signals?${params}`);

    if (!append) {
        state.explorer.items = [];
        renderFacets(body.facets);
        const tbody = document.getElementById('explorerRows');
        tbody.replaceChildren();
        if (!body.items.length) {
            tbody.append(el('tr', {}, el('td', { colspan: 8, class: 'text-muted', text: L.no_signals })));
        }
    }

    state.explorer.total = body.total;
    state.explorer.contents = body.contents;
    state.explorer.offset = body.offset;
    state.explorer.items = append ? state.explorer.items.concat(body.items) : body.items.slice();
    if (body.items.length) appendExplorerRows(body.items);

    document.getElementById('explorerSummary').textContent = fmt(L.total, {
        total: body.total,
        contents: body.contents,
    });

    wireLoadMore('explorerPager', 'explorerTable', state.explorer, () => loadExplorer({ append: true }));
    renderLoadMore('explorerPager', 'explorerTable', state.explorer);
    renderActiveFilters();
}

function renderFacets(facets) {
    const aside = document.getElementById('explorerFacets');
    aside.replaceChildren();
    for (const [dim, entries] of Object.entries(facets || {})) {
        if (!entries.length) continue;
        const list = el('ul', { class: 'signals-facet-list' });
        for (const e of entries) {
            list.append(el('li', {},
                docText(label(e.value)),
                el('span', { class: 'text-muted', text: e.count })));
        }
        aside.append(
            el('div', { class: 'signals-facet-group' },
                el('div', { class: 'signals-facet-title', text: L[`facet_${dim}`] || dim }),
                list));
    }
}

// ---------------------------------------------------------------------------
// Detail
// ---------------------------------------------------------------------------

function dl(pairs) {
    const list = el('dl', { class: 'row small mb-2' });
    for (const [k, v] of pairs) {
        if (v === null || v === undefined || v === '') continue;
        list.append(el('dt', { class: 'col-5', text: k }), el('dd', { class: 'col-7 mb-1' }, v instanceof Node ? v : docText(String(v))));
    }
    return list;
}

async function openDetail(signalId) {
    const params = new URLSearchParams();
    const ref = document.getElementById('fReferenceDate').value;
    if (ref) params.set('reference_date', ref);
    const body = document.getElementById('signalDetailBody');
    body.replaceChildren();
    try {
        const s = await getJson(`/api/signals/${signalId}?${params}`);
        document.getElementById('signalDetailTitle').replaceChildren(docText(s.surface));
        body.append(el('h3', { class: 'fs-6', text: L.evidence }),
            el('blockquote', { class: 'border-start ps-2 small' }, docText(s.sentence || s.surface)));
        if (s.places && s.places.length > 1) {
            body.append(el('div', { class: 'small fw-semibold', text: L.ambiguous_places }),
                el('ul', { class: 'small' }, ...s.places.map((p) => el('li', {}, docText(`${p.label} (${p.feature_type}, ${p.country_codes.join(',')})`)))));
        }
        const version = el('span', {}, s.detector_ver, ' ',
            el('span', { class: `badge ${s.detector_version_current ? 'bg-success' : 'bg-warning text-dark'}`,
                text: s.detector_version_current ? L.current : L.outdated }));
        body.append(dl([
            [L.method, s.method || L.unrecorded], [L.confidence, label(s.confidence || 'unrecorded')],
            [L.basis, s.confidence_basis], [L.resolution, s.resolution], [L.language, s.language],
            [L.calendar, s.calendar], [L.orientation, s.text_orientation],
            [L.offsets, `${s.char_start}–${s.char_end}`], [L.detector_version, version],
            [L.detected_at, s.detected_at],
        ]));
        if (s.run) body.append(el('h3', { class: 'fs-6', text: L.run }), dl([[L.run, `${s.run.status} · ${s.run.trigger} · ${s.run.ran_at || ''}`]]));
        body.append(el('h3', { class: 'fs-6', text: L.document }));
        const occ = el('ul', { class: 'small' });
        for (const o of s.occurrences.items) {
            occ.append(el('li', {}, el('a', { href: `/file/${o.path_id}` }, docText(o.file_path))));
        }
        body.append(occ, el('div', { class: 'small text-muted', text: fmt(L.occurrences, { n: s.occurrences.total }) }));
        body.append(el('h3', { class: 'fs-6 mt-2', text: L.structured_evidence }),
            el('pre', { class: 'small bg-light p-2', dir: 'auto', text: JSON.stringify(s.evidence, null, 2) }));
    } catch (err) {
        body.append(el('div', { class: 'alert alert-danger', text: err.message }));
    }
    const panel = document.getElementById('signalDetail');
    if (window.bootstrap && window.bootstrap.Offcanvas) window.bootstrap.Offcanvas.getOrCreateInstance(panel).show();
}

// ---------------------------------------------------------------------------

async function loadSavedSearches() {
    if (state.savedLoaded) return;
    state.savedLoaded = true;
    try {
        const body = await fetch('/api/search/saved', { credentials: 'same-origin' }).then((r) => r.json());
        fillSelect('fSavedSearch', (body.searches || []).map((s) => [s.id, s.name]));
    } catch (e) {
        /* optional */
    }
}

async function run({ append = false } = {}) {
    showError('');
    try {
        if (state.tab === 'horizon') await loadHorizon({ append });
        else await loadExplorer({ append });
    } catch (err) {
        showError(err.message);
    }
}

function selectTab(tab) {
    state.tab = tab;
    for (const [name, tabId, panelId] of [
        ['horizon', 'tabHorizon', 'panelHorizon'],
        ['explorer', 'tabExplorer', 'panelExplorer'],
    ]) {
        const on = name === tab;
        document.getElementById(tabId).classList.toggle('active', on);
        document.getElementById(tabId).setAttribute('aria-selected', on ? 'true' : 'false');
        document.getElementById(panelId).classList.toggle('d-none', !on);
    }
    run();
}

function setCheckboxGroup(name, values) {
    const wanted = new Set(values || []);
    document.querySelectorAll(`#signalFilters input[name="${name}"]`).forEach((input) => {
        input.checked = wanted.has(input.value);
    });
}

function clearKindFilters() {
    ['detector', 'signal_type'].forEach((name) => setCheckboxGroup(name, []));
    const kindInput = document.getElementById('fIdentifierKind');
    if (kindInput) kindInput.value = '';
    document.querySelectorAll('#signalsKindPresets [data-preset-id]').forEach((btn) => {
        btn.classList.remove('active');
        btn.setAttribute('aria-pressed', 'false');
    });
}

function applyKindPreset(presetId) {
    const preset = (DATA.kind_presets || []).find((p) => p.id === presetId);
    if (!preset) return;
    setCheckboxGroup('detector', preset.detectors || []);
    setCheckboxGroup('signal_type', preset.signal_types || []);
    // Identifier kinds live in the signal's evidence, not its signal_type;
    // the hidden input is part of the filter form, so it is submitted with it.
    const kindInput = document.getElementById('fIdentifierKind');
    if (kindInput) kindInput.value = (preset.identifier_kinds || []).join(',');
    document.querySelectorAll('#signalsKindPresets [data-preset-id]').forEach((btn) => {
        const on = btn.dataset.presetId === presetId;
        btn.classList.toggle('active', on);
        btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
    state.horizon.offset = 0;
    state.explorer.offset = 0;
    selectTab('explorer');
    renderActiveFilters();
}

function renderKindPresets() {
    const host = document.getElementById('signalsKindPresets');
    if (!host) return;
    host.replaceChildren();
    for (const preset of DATA.kind_presets || []) {
        const labelText = L[`preset_${preset.id}`] || preset.id;
        const btn = el('button', {
            type: 'button',
            class: 'btn btn-sm btn-outline-primary signals-kind-preset',
            'data-preset-id': preset.id,
            'aria-pressed': 'false',
            text: labelText,
        });
        btn.addEventListener('click', () => applyKindPreset(preset.id));
        host.append(btn);
    }
}

function syncSortControls(value) {
    state.sort = value;
    const primary = document.getElementById('signalsSort');
    const legacy = document.getElementById('explorerSort');
    if (primary) primary.value = value;
    if (legacy) legacy.value = value;
}

function teardownSignalsPage() {
    state.loadingMore = false;
    showError('');
    const root = document.getElementById('signalsPage');
    if (root) delete root.dataset.signalsPageBound;
}

function mountSignalsPage() {
    const root = document.getElementById('signalsPage');
    if (!root || root.dataset.signalsPageBound === '1') return;
    root.dataset.signalsPageBound = '1';

    readSignalsPagePayload();
    resetSignalsState();

    fillSelect('fSource', Object.entries(DATA.sources || {}).sort((a, b) => String(a[1]).localeCompare(String(b[1]))));
    fillSelect('fSide', Object.entries(DATA.sides || {}).sort((a, b) => String(a[1]).localeCompare(String(b[1]))));
    enhanceSignalMultiSelects();
    checkboxGroup('fConfidence', 'confidence', DATA.confidence || []);
    checkboxGroup('fLanguage', 'language', DATA.languages || []);
    checkboxGroup('fCalendar', 'calendar', DATA.calendars || []);
    checkboxGroup('fDetector', 'detector', DATA.detectors || []);
    checkboxGroup('fSignalType', 'signal_type', DATA.signal_types || []);
    renderKindPresets();
    document.getElementById('signalsKindClear')?.addEventListener('click', () => {
        clearKindFilters();
        state.horizon.offset = 0;
        state.explorer.offset = 0;
        renderActiveFilters();
        run();
    });
    checkboxGroup('fResolution', 'resolution', DATA.resolutions || []);
    checkboxGroup('fOrientation', 'orientation', DATA.orientations || []);

    const sortEntries = (DATA.sorts || []).map((s) => [s, L[`sort_${s}`] || s]);
    fillSelect('signalsSort', sortEntries);
    fillSelect('explorerSort', sortEntries);
    syncSortControls(state.sort);

    document.getElementById('signalsSort')?.addEventListener('change', (e) => {
        syncSortControls(e.target.value);
        state.horizon.offset = 0;
        state.explorer.offset = 0;
        run();
    });
    document.getElementById('explorerSort')?.addEventListener('change', (e) => {
        syncSortControls(e.target.value);
        state.horizon.offset = 0;
        state.explorer.offset = 0;
        run();
    });

    document.getElementById('signalsPageSize')?.addEventListener('change', (e) => {
        const next = Number(e.target.value) || 50;
        state.pageSize = Math.min(Math.max(1, next), DATA.max_page_size || 200);
        state.horizon.offset = 0;
        state.explorer.offset = 0;
        run();
    });

    document.getElementById('signalsExportLoaded')?.addEventListener('click', exportLoaded);
    document.getElementById('signalsExportAll')?.addEventListener('click', () => {
        exportAllMatching();
    });
    // Legacy single-button export (if present) still exports loaded rows.
    document.getElementById('signalsExportBtn')?.addEventListener('click', exportLoaded);

    const form = document.getElementById('signalFilters');
    form.addEventListener('submit', (e) => {
        e.preventDefault();
        state.horizon.offset = 0;
        state.explorer.offset = 0;
        renderActiveFilters();
        run();
    });
    form.addEventListener('reset', () => setTimeout(() => {
        clearKindFilters();
        renderActiveFilters();
        form.requestSubmit();
    }, 0));
    form.addEventListener('change', () => renderActiveFilters());
    form.addEventListener('input', () => renderActiveFilters());

    document.getElementById('tabHorizon').addEventListener('click', () => selectTab('horizon'));
    document.getElementById('tabExplorer').addEventListener('click', () => selectTab('explorer'));
    loadSavedSearches();
    renderActiveFilters();
    run();
}

onPageTeardown('signals', teardownSignalsPage);

export default function initSignalsPage() {
    teardownSignalsPage();
    mountSignalsPage();
}
