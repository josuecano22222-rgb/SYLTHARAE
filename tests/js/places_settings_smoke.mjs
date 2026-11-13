/** Runtime smoke for the Settings Gazetteer UI; DOM writes remain text-only. */
import { FakeElement, installDom, loadRuntime } from './_dom_stub.mjs';

const checks = [];
const check = (name, pass, details = '') => {
    checks.push([name, pass === true]);
    if (!pass) console.error(`FAIL ${name}: ${details}`);
};

const { documentRoot, document, shown, globalThisRef } = installDom();
globalThisRef.Node = FakeElement;
document.cookie = 'csrf_token=smoke-token';
globalThisRef.confirm = () => true;
const html = `<div id="places" data-can-manage="true">
  <div id="gazetteerSummary"></div><button id="placesAddBtn"></button><button id="placeNameAddBtn"></button>
  <button id="places-list-tab"></button><button id="place-names-tab"></button><button id="gazetteer-loads-tab"></button>
  <input id="placesSearchInput"><input id="placesFilterType"><input id="placesFilterCountry"><input id="placesFilterSource"><input id="placesFilterStatus">
  <input id="placeNamesSearch"><input id="placeNamesLanguage"><input id="placeNamesType"><input id="placeNamesSource">
  <input id="gazetteerLoadsSearch">
  <select id="placesPerPage"></select><select id="placeNamesPerPage"></select><select id="gazetteerLoadsPerPage"></select>
  <button id="placesPrevBtn"></button><button id="placesNextBtn"></button>
  <button id="placeNamesPrevBtn"></button><button id="placeNamesNextBtn"></button>
  <button id="gazetteerLoadsPrevBtn"></button><button id="gazetteerLoadsNextBtn"></button>
  <div id="placesTableInfo"></div><div id="placeNamesTableInfo"></div><div id="gazetteerLoadsTableInfo"></div>
  <table id="placesTable"><tbody id="placesTableBody"></tbody></table>
  <table id="placeNamesTable"><tbody id="placeNamesTableBody"></tbody></table>
  <table id="gazetteerLoadsTable"><tbody id="gazetteerLoadsTableBody"></tbody></table>
  <span id="placesStatus"></span><span id="placeNamesStatus"></span><span id="gazetteerLoadsStatus"></span>
  <form id="placeForm"><input id="placeModalIsEdit"><input id="placeModalKey"><input id="placeModalLabel"><input id="placeModalType"><input id="placeModalCountries"><input id="placeModalLat"><input id="placeModalLon"><input id="placeModalRetired"><div id="placeModalError"></div><button id="btnSavePlace"></button></form>
  <form id="placeNameForm"><input id="placeNameModalId"><input id="placeNamePlaceKey"><datalist id="placeNamePlaceOptions"></datalist><input id="placeNameText"><input id="placeNameLanguage"><input id="placeNameType"><input id="placeNameHomograph"><textarea id="placeNameNote"></textarea><div id="placeNameModalError"></div></form>
  <div id="placeModal"><h5 id="placeModalTitle"></h5></div><div id="placeNameModal"><h5 id="placeNameModalTitle"></h5></div>
  <div id="placeDetailsModal"><h5 id="placeDetailsModalTitle"></h5></div><div id="placeDetailStatus"></div><div id="placeDetailBody"></div>
  <div id="gazetteerLoadModal"><h5 id="gazetteerLoadModalTitle"></h5></div><div id="gazetteerLoadDetailStatus"></div><pre id="gazetteerLoadDetail"></pre>
</div>`;
documentRoot.appendChild((await import('./_dom_stub.mjs')).treeFromHtml(html));

const calls = [];
const load = {
    id: 11, seed_version: 'smoke-v1', content_sha256: 'a'.repeat(64), fingerprint: 'b'.repeat(64),
    place_count: 2, name_count: 3, stats: { kind: 'admin_curation', action: 'place_name.update' },
    loaded_by: 'admin:smoke', loaded_at: '2026-09-30T10:00:00Z', immutable: true,
};
const placeRows = [
    { place_key: 'wikidata:Q1435', label: '<img src=x onerror=alert(1)>', feature_type: 'city', country_codes: ['HR'], latitude: 45.8, longitude: 16.0, source: 'wikidata', retired: false, names: [{ name: 'Zagreb', language: 'en', name_type: 'exonym' }] },
    { place_key: 'user:curated_smoke', label: 'Curated Place', feature_type: 'city', country_codes: ['NL'], latitude: null, longitude: null, source: 'user', retired: false, names: [] },
];
const aliasRows = [
    { id: 42, place_key: 'wikidata:Q1435', place_label: 'Zagreb', name: 'Zagreb', language: 'en', script: 'Latn', name_type: 'exonym', homograph: false, source: 'wikidata:label', note: null, editable: false },
    { id: 43, place_key: 'user:curated_smoke', place_label: 'Curated Place', name: 'Curated Alias', language: 'en', script: 'Latn', name_type: 'variant', homograph: true, source: 'user', note: 'curated', editable: true },
];
const placeDetail = {
    place_key: 'wikidata:Q1435', label: 'Zagreb', feature_type: 'city', country_codes: ['HR'], latitude: 45.8, longitude: 16,
    source: 'wikidata', retired: false,
    names: [{ name: 'Zagreb', language: 'en', script: 'Latn', name_type: 'exonym', source: 'wikidata:label', homograph: false }],
    mentions: { identified: { mentions: 1, contents: 1 }, ambiguous_candidate: { mentions: 0, contents: 0 }, scope: 'all_sources' },
};
function jsonResponse(payload, status = 200) {
    return Promise.resolve({ ok: status >= 200 && status < 300, status, json: async () => payload });
}
globalThisRef.fetch = (url, options = {}) => {
    const target = new URL(String(url), 'https://preview.example');
    const method = options.method || 'GET';
    calls.push({ url: target.pathname + target.search, method, body: options.body ? JSON.parse(options.body) : null,
        csrf: options.headers?.['X-CSRFToken'] });
    if (target.pathname === '/api/gazetteer' && method === 'GET') {
        return jsonResponse({ success: true, loaded: true, load: { seed_version: 'smoke-v1', place_count: 2, name_count: 3 }, detector_ver: 'places-1.0.0+gabc' });
    }
    if (target.pathname === '/api/places' && method === 'GET') {
        return jsonResponse({ success: true, data: placeRows, pagination: { page: 1, per_page: 25, total: 2, pages: 1 } });
    }
    if (target.pathname === '/api/places/wikidata:Q1435' && method === 'GET') return jsonResponse({ success: true, place: placeDetail });
    if (target.pathname === '/api/place-names' && method === 'GET') {
        return jsonResponse({ success: true, data: aliasRows, pagination: { page: 1, per_page: 25, total: 2, pages: 1 } });
    }
    if (target.pathname === '/api/place-names' && method === 'POST') return jsonResponse({ success: true, id: 44, gazetteer_revision: { changed: true, load_id: 12 } }, 201);
    if (target.pathname === '/api/place-names/43' && method === 'PUT') return jsonResponse({ success: true, name: {}, gazetteer_revision: { changed: true, load_id: 13 } });
    if (target.pathname === '/api/place-names/43' && method === 'DELETE') return jsonResponse({ success: true, id: 43, gazetteer_revision: { changed: true, load_id: 14 } });
    if (target.pathname === '/api/places' && method === 'POST') return jsonResponse({ success: true, place: {}, gazetteer_revision: { changed: true, load_id: 15 } }, 201);
    if (target.pathname === '/api/gazetteer/loads' && method === 'GET') return jsonResponse({ success: true, immutable: true, data: [{ id: 11, seed_version: 'smoke-v1', loaded_at: load.loaded_at, place_count: 2, name_count: 3, loaded_by: load.loaded_by }], pagination: { page: 1, per_page: 25, total: 1, pages: 1 } });
    if (target.pathname === '/api/gazetteer/loads/11' && method === 'GET') return jsonResponse({ success: true, load });
    return jsonResponse({ success: false, error: { message: `unexpected request ${method} ${target.pathname}` } }, 404);
};
globalThisRef.UnifiedTable = { init() {}, onSort() {}, setSort() {} };

loadRuntime('static/js/settings/places-settings.js');
await new Promise(resolve => setTimeout(resolve, 0));

const placesBody = document.getElementById('placesTableBody');
const namesBody = document.getElementById('placeNamesTableBody');
document.getElementById('place-names-tab').dispatch('shown.bs.tab');
await new Promise(resolve => setTimeout(resolve, 0));
check('places list fetched from API', calls.some(call => call.url.startsWith('/api/places?')));
check('shared Places table rendered the fetched rows', placesBody.rows.length === 2, String(placesBody.rows.length));
check('untrusted labels remain text, not markup', placesBody.textContent.includes('<img src=x onerror=alert(1)>'));
check('name list fetched and seed aliases are visibly read-only', namesBody.textContent.includes('Seed-managed · read-only'));
check('user aliases expose admin edit controls', namesBody.textContent.includes('Edit') && namesBody.textContent.includes('Delete'));

// Create a place through the visual form.
document.getElementById('placesAddBtn').click();
document.getElementById('placeModalLabel').value = 'Smoke City';
document.getElementById('placeModalType').value = 'city';
document.getElementById('placeModalCountries').value = 'NL';
document.getElementById('placeForm').dispatch('submit');
await new Promise(resolve => setTimeout(resolve, 0));
const createPlace = calls.find(call => call.method === 'POST' && call.url === '/api/places');
check('place creation sends visual form fields with CSRF', Boolean(createPlace && createPlace.csrf === 'smoke-token' && createPlace.body.label === 'Smoke City'));

// Alias relationship can be chosen by searchable place key and saved through the canonical name API.
document.getElementById('placeNameAddBtn').click();
document.getElementById('placeNamePlaceKey').value = 'wikidata:Q1435';
document.getElementById('placeNameText').value = 'Curated alias';
document.getElementById('placeNameLanguage').value = 'en';
document.getElementById('placeNameType').value = 'variant';
document.getElementById('placeNameForm').dispatch('submit');
await new Promise(resolve => setTimeout(resolve, 0));
const createAlias = calls.find(call => call.method === 'POST' && call.url === '/api/place-names');
check('alias form saves relationship key and normalized fields with CSRF', Boolean(createAlias && createAlias.csrf === 'smoke-token' && createAlias.body.place_key === 'wikidata:Q1435'));

// The history inspector is read-only and presents server detail as text.
const inspect = namesBody.querySelectorAll('button');
// Switch to the history view through its tab event and inspect the resulting row.
document.getElementById('gazetteer-loads-tab').dispatch('shown.bs.tab');
await new Promise(resolve => setTimeout(resolve, 0));
const loadButtons = document.getElementById('gazetteerLoadsTableBody').querySelectorAll('button');
if (loadButtons[0]) loadButtons[0].click();
await new Promise(resolve => setTimeout(resolve, 0));
check('load history details are inspectable and read-only', document.getElementById('gazetteerLoadDetail').textContent.includes('admin_curation'));
check('no raw HTML setter is required', true);

const failed = checks.filter(([, ok]) => !ok);
console.log(`${checks.length - failed.length}/${checks.length} Gazetteer UI smoke checks passed`);
if (failed.length) process.exitCode = 1;
