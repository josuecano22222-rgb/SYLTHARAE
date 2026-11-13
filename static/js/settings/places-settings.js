/**
 * Gazetteer management for Settings → Geographic Gazetteer.
 *
 * Tables use the shared record_table/unified-table structure. API writes are
 * administrator-only; seed rows and seed names are intentionally immutable
 * from this interface. Admin curation writes advance the detector fingerprint
 * and load history through the server's single curation service.
 */
(function () {
    'use strict';

    const pane = document.getElementById('places');
    if (!pane) return;
    const canManage = pane.dataset.canManage === 'true';
    const requestStates = new Map();
    const tableState = {
        places: { page: 1, perPage: 25, sort: 'label', order: 'asc', query: '' },
        names: { page: 1, perPage: 25, sort: 'name', order: 'asc', query: '' },
    };
    let selectorRequest = 0;
    let placeDetailRequest = 0;
    let summaryRequest = 0;

    const byId = id => document.getElementById(id);

    function csrfToken() {
        const meta = document.querySelector('meta[name="csrf-token"]');
        if (meta) return meta.getAttribute('content') || '';
        if (window.__csrfToken) return window.__csrfToken;
        const item = document.cookie.split('; ').find(part => part.startsWith('csrf_token='));
        return item ? decodeURIComponent(item.substring('csrf_token='.length)) : '';
    }

    function clear(node) {
        if (!node) return;
        node.replaceChildren();
    }

    function element(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = String(text);
        return node;
    }

    function cell(row, content, className) {
        const td = element('td', className || '', '');
        if (content instanceof Node) td.appendChild(content);
        else td.textContent = content === null || content === undefined || content === '' ? '—' : String(content);
        row.appendChild(td);
        return td;
    }

    function button(label, className, handler, title) {
        const result = element('button', className || 'btn btn-sm btn-outline-secondary', label);
        result.type = 'button';
        if (title) result.title = title;
        result.addEventListener('click', handler);
        return result;
    }

    function setStatus(id, message, isError) {
        const target = byId(id);
        if (!target) return;
        target.textContent = message || '';
        target.classList.toggle('text-danger', Boolean(isError));
        target.classList.toggle('text-success', Boolean(message) && !isError);
    }

    async function requestJSON(url, options) {
        const opts = Object.assign({ headers: { Accept: 'application/json' } }, options || {});
        opts.headers = Object.assign({ Accept: 'application/json' }, opts.headers || {});
        const response = await fetch(url, opts);
        let payload = {};
        try { payload = await response.json(); } catch (_) { /* sanitized below */ }
        if (!response.ok) {
            throw new Error(payload.error?.message || payload.error || `HTTP ${response.status}`);
        }
        return payload;
    }

    function manageHeaders(method, body) {
        const headers = { Accept: 'application/json', 'X-CSRFToken': csrfToken() };
        if (body !== undefined) headers['Content-Type'] = 'application/json';
        return { method, headers, ...(body !== undefined ? { body: JSON.stringify(body) } : {}) };
    }

    function showRowState(tbody, colspan, message, type) {
        clear(tbody);
        const tr = element('tr');
        const td = element('td', type === 'error' ? 'text-danger text-center py-4' : 'text-muted text-center py-4', message);
        td.colSpan = colspan;
        tr.appendChild(td);
        tbody.appendChild(tr);
    }

    function searchParams(state, extra) {
        const params = new URLSearchParams({
            page: String(state.page), per_page: String(state.perPage),
            sort: state.sort, order: state.order,
        });
        if (state.query) params.set('q', state.query);
        Object.entries(extra || {}).forEach(([key, value]) => {
            if (value !== undefined && value !== null && value !== '') params.set(key, value);
        });
        return params;
    }

    function installTableSort(tableId, state, load) {
        if (window.UnifiedTable && typeof window.UnifiedTable.onSort === 'function') {
            window.UnifiedTable.onSort(tableId, (key, direction) => {
                state.sort = direction === null ? (tableId === 'placeNamesTable' ? 'name' : 'label') : key;
                state.order = direction === null ? 'asc' : direction;
                state.page = 1;
                load();
            });
        }
    }

    function syncTable(tableId, state, pagination, load) {
        const info = byId(`${tableId}Info`);
        const page = Number(pagination.page || state.page);
        const perPage = Number(pagination.per_page || state.perPage);
        const total = Number(pagination.total || 0);
        const totalPages = Number(pagination.pages || 0);
        state.page = page;
        state.perPage = perPage;
        if (info) info.textContent = `Showing ${Math.min((page - 1) * perPage + 1, total)}–${Math.min(page * perPage, total)} of ${total}`;
        const prev = byId(tableId === 'placesTable' ? 'placesPrevBtn' : 'placeNamesPrevBtn');
        const next = byId(tableId === 'placesTable' ? 'placesNextBtn' : 'placeNamesNextBtn');
        if (prev) prev.disabled = page <= 1;
        if (next) next.disabled = totalPages === 0 || page >= totalPages;
        if (window.UnifiedTable?.setSort) window.UnifiedTable.setSort(tableId, state.sort, state.order);
        const pageSizeId = tableId === 'placesTable' ? 'placesPerPage' : 'placeNamesPerPage';
        const perPageSelect = byId(pageSizeId);
        if (perPageSelect) perPageSelect.value = String(perPage);
    }

    function requestForTable(key, url, renderer, colspan, statusId) {
        const previous = requestStates.get(key);
        if (previous) previous.controller.abort();
        const state = { controller: new AbortController(), generation: (previous?.generation || 0) + 1 };
        requestStates.set(key, state);
        const tbodyId = key === 'places' ? 'placesTableBody' : 'placeNamesTableBody';
        const tbody = byId(tbodyId);
        if (!tbody) return;
        showRowState(tbody, colspan, 'Loading…', 'loading');
        setStatus(statusId, '', false);
        fetch(url, { headers: { Accept: 'application/json' }, signal: state.controller.signal })
            .then(async response => {
                let payload = {};
                try { payload = await response.json(); } catch (_) { /* handled below */ }
                if (!response.ok) throw new Error(payload.error?.message || `HTTP ${response.status}`);
                return payload;
            })
            .then(payload => {
                if (requestStates.get(key) !== state) return;
                if (!payload.success) throw new Error(payload.error?.message || 'The request was not successful');
                renderer(tbody, payload);
            })
            .catch(error => {
                if (error.name === 'AbortError' || requestStates.get(key) !== state) return;
                showRowState(tbody, colspan, `Could not load data: ${error.message}`, 'error');
                setStatus(statusId, error.message, true);
            });
    }

    function placeNamesText(names, total) {
        const labels = (names || []).map(item => item.name).filter(Boolean);
        const visible = labels.slice(0, 5);
        const nameCount = Number.isFinite(Number(total)) ? Math.max(visible.length, Number(total)) : labels.length;
        return visible.join(', ') + (nameCount > visible.length ? ` +${nameCount - visible.length} more` : '');
    }

    function loadPlaces() {
        const state = tableState.places;
        const extra = {
            feature_type: byId('placesFilterType')?.value,
            country: byId('placesFilterCountry')?.value.trim().toUpperCase(),
            status: byId('placesFilterStatus')?.value || 'all',
            source: byId('placesFilterSource')?.value || 'all',
        };
        const params = searchParams(state, extra);
        requestForTable('places', `/api/places?${params}`, (tbody, payload) => {
            clear(tbody);
            const rows = payload.data || [];
            if (!rows.length) {
                showRowState(tbody, 8, 'No places match these filters.', 'empty');
                syncTable('placesTable', state, payload.pagination || {}, loadPlaces);
                return;
            }
            rows.forEach(place => {
                const tr = element('tr');
                if (place.retired) tr.classList.add('table-secondary');
                cell(tr, place.place_key, 'font-monospace small');
                const label = element('div', 'fw-semibold', place.label);
                const names = placeNamesText(place.names, place.names_total);
                if (names) label.appendChild(element('div', 'small text-muted fw-normal', names));
                cell(tr, label);
                cell(tr, place.feature_type);
                cell(tr, (place.country_codes || []).join(', '));
                const coordinates = place.latitude !== null && place.longitude !== null
                    ? `${Number(place.latitude).toFixed(4)}, ${Number(place.longitude).toFixed(4)}` : '—';
                cell(tr, coordinates, 'font-monospace small');
                cell(tr, place.source === 'user' ? 'User-curated' : 'Seed-managed');
                const status = element('span', `badge ${place.retired ? 'bg-secondary' : 'bg-success'}`, place.retired ? 'Retired' : 'Active');
                cell(tr, status);
                const actions = element('td', 'text-nowrap');
                actions.appendChild(button('Details', 'btn btn-sm btn-outline-secondary me-1', () => showPlaceDetails(place.place_key)));
                if (canManage && place.source === 'user') {
                    actions.appendChild(button('Edit', 'btn btn-sm btn-outline-primary me-1', () => openPlaceModal(place)));
                }
                if (canManage) {
                    actions.appendChild(button(place.retired ? 'Activate' : 'Retire',
                        `btn btn-sm ${place.retired ? 'btn-outline-success' : 'btn-outline-warning'} me-1`,
                        () => togglePlace(place.place_key, !place.retired)));
                    if (place.source === 'user') {
                        actions.appendChild(button('Delete', 'btn btn-sm btn-outline-danger', () => deletePlace(place.place_key)));
                    }
                }
                tr.appendChild(actions);
                tbody.appendChild(tr);
            });
            syncTable('placesTable', state, payload.pagination || {}, loadPlaces);
        }, 8, 'placesStatus');
    }

    function loadNames() {
        const state = tableState.names;
        const params = searchParams(state, {
            language: byId('placeNamesLanguage')?.value,
            name_type: byId('placeNamesType')?.value,
            source: byId('placeNamesSource')?.value || 'all',
            status: 'all',
        });
        requestForTable('names', `/api/place-names?${params}`, (tbody, payload) => {
            clear(tbody);
            const rows = payload.data || [];
            if (!rows.length) {
                showRowState(tbody, 8, 'No place names match these filters.', 'empty');
                syncTable('placeNamesTable', state, payload.pagination || {}, loadNames);
                return;
            }
            rows.forEach(name => {
                const tr = element('tr');
                cell(tr, name.name, 'fw-semibold');
                cell(tr, `${name.language} (${name.script})`);
                cell(tr, name.name_type);
                cell(tr, name.place_label);
                cell(tr, name.place_key, 'font-monospace small');
                cell(tr, name.source === 'user' ? 'User-curated' : name.source);
                cell(tr, name.homograph ? 'Yes' : 'No');
                const actions = element('td', 'text-nowrap');
                if (canManage && name.editable) {
                    actions.appendChild(button('Edit', 'btn btn-sm btn-outline-primary me-1', () => openNameModal(name)));
                    actions.appendChild(button('Delete', 'btn btn-sm btn-outline-danger', () => deleteName(name)));
                } else {
                    actions.appendChild(element('span', 'small text-muted', 'Seed-managed · read-only'));
                }
                tr.appendChild(actions);
                tbody.appendChild(tr);
            });
            syncTable('placeNamesTable', state, payload.pagination || {}, loadNames);
        }, 8, 'placeNamesStatus');
    }

    async function loadSummary() {
        const summary = byId('gazetteerSummary');
        if (!summary) return;
        const generation = ++summaryRequest;
        try {
            const data = await requestJSON('/api/gazetteer');
            if (generation !== summaryRequest) return;
            if (!data.loaded) {
                summary.textContent = 'Gazetteer is not loaded.';
                return;
            }
            const fp = data.fingerprint ? String(data.fingerprint).slice(0, 12) : '—';
            summary.textContent = `Fingerprint ${fp} · ${data.place_count} places · ${data.name_count} names · Detector ${data.detector_ver}`;
        } catch (error) {
            if (generation !== summaryRequest) return;
            summary.textContent = `Gazetteer status unavailable: ${error.message}`;
            summary.classList.add('text-danger');
        }
    }

    function showModal(id) {
        const modal = byId(id);
        if (!modal || !window.bootstrap?.Modal) return;
        window.bootstrap.Modal.getOrCreateInstance(modal).show();
    }

    function hideModal(id) {
        const modal = byId(id);
        if (modal && window.bootstrap?.Modal) window.bootstrap.Modal.getInstance(modal)?.hide();
    }

    function openPlaceModal(place) {
        byId('placeModalTitle').textContent = place ? `Edit Location: ${place.label}` : 'Add Geographic Location';
        byId('placeModalIsEdit').value = place ? '1' : '0';
        byId('placeModalKey').value = place?.place_key || '';
        byId('placeModalKey').disabled = Boolean(place);
        byId('placeModalLabel').value = place?.label || '';
        byId('placeModalType').value = place?.feature_type || 'city';
        byId('placeModalCountries').value = (place?.country_codes || []).join(', ');
        byId('placeModalLat').value = place?.latitude ?? '';
        byId('placeModalLon').value = place?.longitude ?? '';
        byId('placeModalRetired').checked = Boolean(place?.retired);
        const error = byId('placeModalError');
        error.textContent = '';
        error.classList.add('d-none');
        showModal('placeModal');
    }

    async function showPlaceDetails(placeKey) {
        const status = byId('placeDetailStatus');
        const detail = byId('placeDetailBody');
        if (!status || !detail) return;
        const generation = ++placeDetailRequest;
        status.textContent = 'Loading place details…';
        clear(detail);
        showModal('placeDetailsModal');
        try {
            const payload = await requestJSON(`/api/places/${encodeURIComponent(placeKey)}`);
            if (generation !== placeDetailRequest) return;
            const place = payload.place;
            status.textContent = `Mention counts are scoped to the current account: ${place.mentions.scope}.`;
            const dl = element('dl', 'row mb-0');
            const pairs = [
                ['Place key', place.place_key], ['Label', place.label], ['Feature type', place.feature_type],
                ['Country codes', (place.country_codes || []).join(', ') || '—'],
                ['Coordinates', place.latitude === null || place.longitude === null ? '—' : `${place.latitude}, ${place.longitude}`],
                ['Source', place.source], ['Status', place.retired ? 'Retired' : 'Active'],
                ['Identified mentions', place.mentions.identified.mentions],
                ['Identified contents', place.mentions.identified.contents],
                ['Ambiguous candidate mentions', place.mentions.ambiguous_candidate.mentions],
            ];
            for (const [label, value] of pairs) {
                dl.append(element('dt', 'col-sm-4', label), element('dd', 'col-sm-8', value));
            }
            detail.appendChild(dl);
            const namesTitle = element('h6', 'mt-3', 'Recorded names');
            detail.appendChild(namesTitle);
            const list = element('ul', 'mb-0');
            for (const name of place.names || []) {
                list.appendChild(element('li', '', `${name.name} · ${name.language} / ${name.script} · ${name.name_type} · ${name.source}${name.homograph ? ' · homograph' : ''}`));
            }
            detail.appendChild(list);
        } catch (error) {
            if (generation !== placeDetailRequest) return;
            status.textContent = `Could not load place details: ${error.message}`;
            status.classList.add('text-danger');
        }
    }

    async function togglePlace(placeKey, retired) {
        try {
            const result = await requestJSON(`/api/places/${encodeURIComponent(placeKey)}`, manageHeaders('PUT', { retired }));
            setStatus('placesStatus', `${placeKey} ${retired ? 'retired' : 'activated'}. Detector revision: ${result.gazetteer_revision?.load_id ?? 'unchanged'}.`, false);
            loadPlaces(); loadSummary();
        } catch (error) { setStatus('placesStatus', `Could not update place: ${error.message}`, true); }
    }

    async function deletePlace(placeKey) {
        if (!window.confirm(`Delete custom location ${placeKey}? If stored signals refer to it, it will be retired instead.`)) return;
        try {
            const result = await requestJSON(`/api/places/${encodeURIComponent(placeKey)}`, manageHeaders('DELETE'));
            setStatus('placesStatus', result.action === 'deleted' ? 'Location deleted.' : 'Location has stored signals and was retired to preserve evidence.', false);
            loadPlaces(); loadSummary();
        } catch (error) { setStatus('placesStatus', `Could not delete place: ${error.message}`, true); }
    }

    function openNameModal(name) {
        byId('placeNameModalTitle').textContent = name ? 'Edit User-Curated Place Name' : 'Add Place Name';
        byId('placeNameModalId').value = name?.id || '';
        byId('placeNamePlaceKey').value = name?.place_key || '';
        byId('placeNameText').value = name?.name || '';
        byId('placeNameLanguage').value = name?.language || 'en';
        byId('placeNameType').value = name?.name_type || 'variant';
        byId('placeNameHomograph').checked = Boolean(name?.homograph);
        byId('placeNameNote').value = name?.note || '';
        const error = byId('placeNameModalError');
        error.textContent = '';
        error.classList.add('d-none');
        showModal('placeNameModal');
        if (!name) searchPlaceOptions('');
    }

    async function searchPlaceOptions(query) {
        const generation = ++selectorRequest;
        const list = byId('placeNamePlaceOptions');
        if (!list) return;
        try {
            const params = new URLSearchParams({ page: '1', per_page: '50', status: 'active' });
            if (query.trim()) params.set('q', query.trim());
            const data = await requestJSON(`/api/places?${params}`);
            if (generation !== selectorRequest) return;
            clear(list);
            for (const place of data.data || []) {
                const option = document.createElement('option');
                option.value = place.place_key;
                option.label = `${place.label} · ${place.feature_type}`;
                list.appendChild(option);
            }
        } catch (error) {
            setStatus('placeNamesStatus', `Place selector could not load options: ${error.message}`, true);
        }
    }

    async function deleteName(name) {
        if (!window.confirm(`Delete the user-curated alias “${name.name}” from ${name.place_label}?`)) return;
        try {
            const result = await requestJSON(`/api/place-names/${name.id}`, manageHeaders('DELETE'));
            setStatus('placeNamesStatus', `Alias deleted. Gazetteer revision ${result.gazetteer_revision?.load_id ?? 'unchanged'}.`, false);
            loadNames(); loadSummary();
        } catch (error) { setStatus('placeNamesStatus', `Could not delete alias: ${error.message}`, true); }
    }

    async function savePlace(event) {
        event.preventDefault();
        const isEdit = byId('placeModalIsEdit').value === '1';
        const key = byId('placeModalKey').value.trim();
        const label = byId('placeModalLabel').value.trim();
        const countries = byId('placeModalCountries').value.split(',').map(value => value.trim().toUpperCase()).filter(Boolean);
        const latValue = byId('placeModalLat').value;
        const lonValue = byId('placeModalLon').value;
        const body = {
            label,
            feature_type: byId('placeModalType').value,
            country_codes: countries,
            retired: byId('placeModalRetired').checked,
        };
        if (latValue !== '') body.latitude = Number(latValue);
        if (lonValue !== '') body.longitude = Number(lonValue);
        if (!isEdit && key) body.place_key = key;
        const error = byId('placeModalError');
        error.textContent = '';
        error.classList.add('d-none');
        try {
            const response = await requestJSON(isEdit ? `/api/places/${encodeURIComponent(key)}` : '/api/places',
                manageHeaders(isEdit ? 'PUT' : 'POST', body));
            hideModal('placeModal');
            setStatus('placesStatus', `${isEdit ? 'Location updated' : 'Location created'}. Detector revision ${response.gazetteer_revision?.load_id ?? 'unchanged'}.`, false);
            loadPlaces(); loadSummary();
        } catch (failure) {
            error.textContent = failure.message;
            error.classList.remove('d-none');
        }
    }

    async function saveName(event) {
        event.preventDefault();
        const id = byId('placeNameModalId').value;
        const body = {
            place_key: byId('placeNamePlaceKey').value.trim(),
            name: byId('placeNameText').value.trim(),
            language: byId('placeNameLanguage').value,
            name_type: byId('placeNameType').value,
            homograph: byId('placeNameHomograph').checked,
            note: byId('placeNameNote').value,
        };
        const error = byId('placeNameModalError');
        error.textContent = '';
        error.classList.add('d-none');
        try {
            const response = await requestJSON(id ? `/api/place-names/${id}` : '/api/place-names',
                manageHeaders(id ? 'PUT' : 'POST', body));
            hideModal('placeNameModal');
            setStatus('placeNamesStatus', `${id ? 'Alias updated' : 'Alias added'}. Detector revision ${response.gazetteer_revision?.load_id ?? 'unchanged'}.`, false);
            loadNames(); loadPlaces(); loadSummary();
        } catch (failure) {
            error.textContent = failure.message;
            error.classList.remove('d-none');
        }
    }

    function wireTable(tableId, state, load, prevId, nextId, perPageId) {
        installTableSort(tableId, state, load);
        byId(prevId)?.addEventListener('click', () => { if (state.page > 1) { state.page -= 1; load(); } });
        byId(nextId)?.addEventListener('click', () => { state.page += 1; load(); });
        byId(perPageId)?.addEventListener('change', event => {
            state.perPage = Number(event.target.value) || 25;
            state.page = 1;
            load();
        });
    }

    function wireSearch(inputId, state, load) {
        const input = byId(inputId);
        if (!input) return;
        input.addEventListener('input', () => {
            state.query = input.value.trim();
            state.page = 1;
            load();
        });
    }

    function init() {
        if (!byId('placesTable')) return;
        if (window.UnifiedTable?.init) window.UnifiedTable.init(document);
        if (!byId('placesTable').dataset.placesWired) {
            byId('placesTable').dataset.placesWired = '1';
            wireTable('placesTable', tableState.places, loadPlaces, 'placesPrevBtn', 'placesNextBtn', 'placesPerPage');
            wireTable('placeNamesTable', tableState.names, loadNames, 'placeNamesPrevBtn', 'placeNamesNextBtn', 'placeNamesPerPage');
            wireSearch('placesSearchInput', tableState.places, loadPlaces);
            wireSearch('placeNamesSearch', tableState.names, loadNames);
            for (const id of ['placesFilterType', 'placesFilterCountry', 'placesFilterSource', 'placesFilterStatus']) {
                byId(id)?.addEventListener(id === 'placesFilterCountry' ? 'input' : 'change', () => {
                    tableState.places.page = 1;
                    loadPlaces();
                });
            }
            for (const id of ['placeNamesLanguage', 'placeNamesType', 'placeNamesSource']) {
                byId(id)?.addEventListener('change', () => { tableState.names.page = 1; loadNames(); });
            }
            byId('placesAddBtn')?.addEventListener('click', () => openPlaceModal(null));
            byId('placeNameAddBtn')?.addEventListener('click', () => openNameModal(null));
            byId('placeForm')?.addEventListener('submit', savePlace);
            byId('placeNameForm')?.addEventListener('submit', saveName);
            byId('placeNamePlaceKey')?.addEventListener('input', event => searchPlaceOptions(event.target.value));
            byId('placeNamePlaceKey')?.addEventListener('focus', event => searchPlaceOptions(event.target.value));
            byId('places-list-tab')?.addEventListener('shown.bs.tab', loadPlaces);
            byId('place-names-tab')?.addEventListener('shown.bs.tab', loadNames);
        }
        loadSummary();
        loadPlaces();
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
    document.addEventListener('syltharae:soft-nav', function () {
        if (window.location.pathname.indexOf('/settings') === 0) init();
    });
})();
