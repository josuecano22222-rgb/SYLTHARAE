/**
 * Map Viewer — layers, offline packs, localized UI, customizable markers.
 */

import { onPageTeardown, markPageActive } from '../modules/core/page-lifecycle.js';
import { buildStyleFromLayer, layerKindLabel } from '../modules/map/map-layer-styles.js';
import {
    MARKER_ICON_IDS,
    MARKER_COLORS,
    registerMarkerImages,
    registerMarkerPalette,
    markerImageId,
    iconShapeForFileType,
} from '../modules/map/map-marker-icons.js';

const MAP_SCRIPTS = {
    pmtiles: '/static/vendor/map/pmtiles.js',
    maplibre: '/static/vendor/map/maplibre-gl.js',
    worker: '/static/vendor/map/maplibre-gl-csp-worker.js',
};
const MAP_STYLESHEET = '/static/vendor/map/maplibre-gl.css';
const PREFS_KEY = 'syltharae.mapViewer.prefs';

const state = {
    map: null,
    popup: null,
    pmtilesProtocol: null,
    layers: [],
    packages: [],
    points: [],
    activeLayerId: null,
    pollTimer: null,
    storageRefreshTimer: null,
    booted: false,
    prefs: {
        icon: 'pin',
        color: '#06b6d4',
        scale: 1,
        showLabels: false,
        autoIconByType: true,
        openFileOnClick: true,
        autoCacheOnline: true,
        layerId: 'standard-osm',
    },
};

let config = {};
let i18n = {};

function readJson(id) {
    const el = document.getElementById(id);
    if (!el) return {};
    try {
        return JSON.parse(el.textContent || '{}');
    } catch (_e) {
        return {};
    }
}

function t(key, fallback) {
    if (i18n[key]) return i18n[key];
    if (typeof window.t === 'function') {
        const v = window.t(key);
        if (v && v !== key) return v;
    }
    return fallback || key;
}

function escapeHtml(value) {
    return String(value || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function isMapViewerActive() {
    return !!document.getElementById('mapViewerPage');
}

function loadPrefs() {
    try {
        const raw = localStorage.getItem(PREFS_KEY);
        if (raw) Object.assign(state.prefs, JSON.parse(raw));
    } catch (_e) { /* */ }
}

function savePrefs() {
    try {
        localStorage.setItem(PREFS_KEY, JSON.stringify(state.prefs));
    } catch (_e) { /* */ }
}

function csrfToken() {
    return (window.CSRF && window.CSRF.getToken && window.CSRF.getToken())
        || document.querySelector('meta[name="csrf-token"]')?.getAttribute('content')
        || '';
}

async function fetchJson(url, options = {}) {
    const method = (options.method || 'GET').toUpperCase();
    const headers = { Accept: 'application/json', ...(options.headers || {}) };
    if (method !== 'GET' && method !== 'HEAD') headers['X-CSRFToken'] = csrfToken();
    const response = await fetch(url, { credentials: 'same-origin', headers, ...options });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || data.message || response.statusText);
    return data;
}

function loadStylesheet(href) {
    const path = href.split('?')[0];
    if (Array.from(document.querySelectorAll('link[rel="stylesheet"]'))
        .some((l) => (l.getAttribute('href') || '').split('?')[0] === path)) {
        return Promise.resolve();
    }
    return new Promise((resolve, reject) => {
        const link = document.createElement('link');
        link.rel = 'stylesheet';
        link.href = href;
        link.onload = () => resolve();
        link.onerror = () => reject(new Error(`stylesheet ${href}`));
        document.head.appendChild(link);
    });
}

function loadClassicScript(src) {
    const path = src.split('?')[0];
    if (path.endsWith('maplibre-gl.js') && globalThis.maplibregl) return Promise.resolve();
    if (path.endsWith('pmtiles.js') && globalThis.pmtiles) return Promise.resolve();
    const existing = Array.from(document.querySelectorAll('script[src]'))
        .find((s) => (s.getAttribute('src') || '').split('?')[0] === path);
    if (existing) {
        return new Promise((resolve, reject) => {
            if (globalThis.maplibregl || globalThis.pmtiles) resolve();
            existing.addEventListener('load', () => resolve(), { once: true });
            existing.addEventListener('error', () => reject(new Error(src)), { once: true });
        });
    }
    return new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = src;
        script.async = false;
        script.onload = () => resolve();
        script.onerror = () => reject(new Error(src));
        document.body.appendChild(script);
    });
}

async function waitForGlobal(name, attempts = 80) {
    for (let i = 0; i < attempts; i += 1) {
        if (globalThis[name] !== undefined) return globalThis[name];
        await new Promise((r) => setTimeout(r, 50));
    }
    return undefined;
}

async function ensureMapLibraries() {
    await loadStylesheet(MAP_STYLESHEET);
    await loadClassicScript(MAP_SCRIPTS.pmtiles);
    await loadClassicScript(MAP_SCRIPTS.maplibre);
    await waitForGlobal('maplibregl');
    if (!globalThis.maplibregl) {
        throw new Error(t('mapInitFailed', 'Could not initialize map viewer'));
    }
    if (typeof globalThis.maplibregl.setWorkerUrl === 'function') {
        globalThis.maplibregl.setWorkerUrl(MAP_SCRIPTS.worker);
    }
}

function setNetworkStatus() {
    const pill = document.getElementById('mapNetworkStatus');
    if (!pill) return;
    const online = navigator.onLine;
    pill.dataset.state = online ? 'online' : 'offline';
    pill.textContent = online ? t('online', 'Online') : t('offlineTilesOnly', 'Offline — local tiles only');
}

function formatBytes(n) {
    const v = Number(n) || 0;
    if (v < 1024) return `${v} B`;
    if (v < 1024 * 1024) return `${(v / 1024).toFixed(1)} KB`;
    if (v < 1024 * 1024 * 1024) return `${(v / (1024 * 1024)).toFixed(1)} MB`;
    return `${(v / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

async function refreshStorageSummary() {
    const el = document.getElementById('mapStorageSummary');
    if (!el || !config.storageApi) return;
    try {
        const data = await fetchJson(config.storageApi);
        const cachePart = data.cache_tiles
            ? ` · ${data.cache_tiles} ${t('cachedTiles', 'cached tiles')}`
            : '';
        el.textContent = `${t('storageLabel', 'Offline storage')}: ${formatBytes(data.total_bytes)}${cachePart}`;
        el.title = [data.auto_cache_hint, data.portable_hint].filter(Boolean).join(' ');
    } catch (_e) {
        el.textContent = '—';
    }
}

async function loadAllGeolocationPoints() {
    const base = config.geolocationApi || '/api/archives/geolocation';
    const collected = [];
    let cursor = null;
    for (let page = 0; page < 200; page += 1) {
        const url = new URL(base, window.location.origin);
        url.searchParams.set('limit', '500');
        if (cursor != null) url.searchParams.set('cursor', String(cursor));
        const data = await fetchJson(url.toString());
        collected.push(...(data.data || []));
        if (!data.has_next || data.next_cursor == null) break;
        cursor = data.next_cursor;
    }
    return collected.filter((p) => p.latitude != null && p.longitude != null);
}

function iconPropertyForPoint(point) {
    const shape = state.prefs.autoIconByType
        ? iconShapeForFileType(point.file_type, point.file_name || point.name)
        : state.prefs.icon;
    return markerImageId(shape, state.prefs.color);
}

function buildGeoJson(points) {
    return {
        type: 'FeatureCollection',
        features: points.map((p) => ({
            type: 'Feature',
            geometry: { type: 'Point', coordinates: [Number(p.longitude), Number(p.latitude)] },
            properties: {
                id: p.id,
                name: p.file_name || p.name || '',
                type: p.file_type || '',
                path: p.file_path || '',
                icon: iconPropertyForPoint(p),
            },
        })),
    };
}

function removePointLayers() {
    if (!state.map) return;
    if (state.map.getLayer('geolocations-symbol')) state.map.removeLayer('geolocations-symbol');
    if (state.map.getSource('geolocations')) state.map.removeSource('geolocations');
}

function styleSupportsLabels() {
    if (!state.map || !state.prefs.showLabels) return false;
    try {
        const glyphs = state.map.getStyle()?.glyphs;
        return typeof glyphs === 'string' && glyphs.length > 0;
    } catch (_e) {
        return false;
    }
}

function symbolLayerLayout() {
    const layout = {
        'icon-image': ['get', 'icon'],
        'icon-size': state.prefs.scale,
        'icon-anchor': 'bottom',
        'icon-allow-overlap': true,
    };
    if (styleSupportsLabels()) {
        layout['text-field'] = ['get', 'name'];
        layout['text-font'] = ['Open Sans Regular'];
        layout['text-size'] = 11;
        layout['text-offset'] = [0, 0.6];
        layout['text-anchor'] = 'top';
        layout['text-max-width'] = 14;
    }
    return layout;
}

function schedulePointLayers() {
    if (!state.map || !state.points.length) return;
    state.map.once('idle', () => {
        if (isMapViewerActive() && state.map) ensurePointLayers();
    });
}

async function ensurePointLayers() {
    if (!state.map || !state.points.length) return;
    if (!state.map.isStyleLoaded()) {
        schedulePointLayers();
        return;
    }
    await registerMarkerPalette(state.map, state.prefs.color, MARKER_ICON_IDS);
    if (!state.prefs.autoIconByType) {
        await registerMarkerImages(state.map, state.prefs.icon, state.prefs.color);
    }
    const geojson = buildGeoJson(state.points);
    const src = state.map.getSource('geolocations');
    if (src) src.setData(geojson);
    else state.map.addSource('geolocations', { type: 'geojson', data: geojson });

    if (state.map.getLayer('geolocations-symbol')) {
        state.map.removeLayer('geolocations-symbol');
    }
    state.map.addLayer({
        id: 'geolocations-symbol',
        type: 'symbol',
        source: 'geolocations',
        layout: symbolLayerLayout(),
        paint: styleSupportsLabels() ? {
            'text-color': '#e2e8f0',
            'text-halo-color': '#0f172a',
            'text-halo-width': 1.2,
        } : {},
    });
    if (!state.map._geolocationHandlersBound) {
        state.map.on('click', 'geolocations-symbol', onMarkerClick);
        state.map.on('mouseenter', 'geolocations-symbol', () => {
            state.map.getCanvas().style.cursor = 'pointer';
        });
        state.map.on('mouseleave', 'geolocations-symbol', () => {
            state.map.getCanvas().style.cursor = '';
        });
        state.map._geolocationHandlersBound = true;
    }
}

function onMarkerClick(event) {
    const maplibregl = globalThis.maplibregl;
    const feature = event.features && event.features[0];
    if (!feature || !maplibregl) return;
    const { id, name, type, path } = feature.properties || {};
    const coords = feature.geometry.coordinates.slice();
    if (state.prefs.openFileOnClick && id && config.fileDetailPattern) {
        window.location.href = `${config.fileDetailPattern}${id}`;
        return;
    }
    const html = `<div class="map-popup">
        <strong>${escapeHtml(name || `#${id}`)}</strong>
        ${type ? `<div class="map-popup-meta">${escapeHtml(type)}</div>` : ''}
        ${path ? `<div class="map-popup-path">${escapeHtml(path)}</div>` : ''}
        ${id ? `<a class="map-popup-link" href="${config.fileDetailPattern}${id}">${escapeHtml(t('openFile', 'Open file'))}</a>` : ''}
    </div>`;
    if (state.popup) state.popup.remove();
    state.popup = new maplibregl.Popup({ closeButton: true, maxWidth: '280px' })
        .setLngLat(coords)
        .setHTML(html)
        .addTo(state.map);
}

function applyPointsToMap(points) {
    state.points = points;
    const countEl = document.getElementById('mapPointCount');
    if (countEl) countEl.textContent = `${points.length} ${t('points', 'points')}`;
    renderCoordList(points);
    if (state.map) schedulePointLayers();
}

function renderCoordList(points) {
    const list = document.getElementById('mapCoordList');
    if (!list) return;
    const query = (document.getElementById('mapCoordSearch')?.value || '').trim().toLowerCase();
    const filtered = query
        ? points.filter((p) => [p.file_name, p.name, p.file_path, p.coordinates, p.latitude, p.longitude]
            .join(' ').toLowerCase().includes(query))
        : points.slice(0, 200);

    if (!filtered.length) {
        list.innerHTML = `<p class="map-rail-hint">${escapeHtml(t('noMatchingCoords', 'No matching coordinates.'))}</p>`;
        return;
    }
    list.innerHTML = filtered.map((p) => {
        const label = escapeHtml(p.file_name || p.name || `File ${p.id}`);
        const coords = escapeHtml(`${Number(p.latitude).toFixed(6)}, ${Number(p.longitude).toFixed(6)}`);
        return `<button type="button" class="map-coord-row" data-lon="${p.longitude}" data-lat="${p.latitude}">
            <span class="map-coord-row__label">${label}</span><span class="map-coord-row__coords">${coords}</span></button>`;
    }).join('');
    list.querySelectorAll('.map-coord-row').forEach((btn) => {
        btn.addEventListener('click', () => {
            const lon = Number(btn.getAttribute('data-lon'));
            const lat = Number(btn.getAttribute('data-lat'));
            if (state.map && !Number.isNaN(lon) && !Number.isNaN(lat)) {
                state.map.flyTo({ center: [lon, lat], zoom: Math.max(state.map.getZoom(), 12) });
            }
        });
    });
}

function layerAvailable(layer) {
    if (!layer) return false;
    if (layer.installed) return true;
    if (layer.requires_network && !navigator.onLine) {
        return Boolean(layer.has_tile_cache);
    }
    if (layer.requires_network) return true;
    return Boolean(layer.downloadable);
}

function initMap(style) {
    const maplibregl = globalThis.maplibregl;
    if (typeof globalThis.pmtiles !== 'undefined' && !state.pmtilesProtocol) {
        state.pmtilesProtocol = new globalThis.pmtiles.Protocol();
        maplibregl.addProtocol('pmtiles', state.pmtilesProtocol.tile);
    }
    if (state.map) {
        try { state.map.remove(); } catch (_e) { /* */ }
        state.map = null;
    }
    state.map = new maplibregl.Map({
        container: 'mapCanvas',
        style,
        center: [-98, 39],
        zoom: 3,
        attributionControl: true,
    });
    state.map._geolocationHandlersBound = false;
    state.map.addControl(new maplibregl.NavigationControl(), 'top-right');
    state.map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-left');
    state.map.on('mousemove', (e) => {
        const readout = document.getElementById('mapCursorReadout');
        if (readout) readout.textContent = `${e.lngLat.lat.toFixed(6)}°, ${e.lngLat.lng.toFixed(6)}°`;
    });
    state.map.on('load', () => schedulePointLayers());
    state.map.on('idle', () => {
        clearTimeout(state.storageRefreshTimer);
        state.storageRefreshTimer = setTimeout(() => refreshStorageSummary(), 900);
    });
}

function applyLayer(layer) {
    if (!layer || !isMapViewerActive()) return;
    if (!layerAvailable(layer)) {
        const hint = document.getElementById('mapLayerHint');
        if (hint) {
            hint.textContent = layer.requires_network
                ? t('networkRequired', 'This layer requires network access.')
                : t('installToUseOffline', 'Download or install this pack to use it offline.');
        }
        return;
    }
    state.activeLayerId = layer.id;
    state.prefs.layerId = layer.id;
    savePrefs();
    const hint = document.getElementById('mapLayerHint');
    if (hint) hint.textContent = layer.description || layer.attribution || '';
    const style = buildStyleFromLayer(layer, window.location.origin, {
        autoCacheOnline: state.prefs.autoCacheOnline !== false,
    });
    if (!state.map) initMap(style);
    else {
        state.map.setStyle(style);
        state.map._geolocationHandlersBound = false;
        schedulePointLayers();
    }
}

function renderLayerGroups(layers) {
    const container = document.getElementById('mapLayerGroups');
    if (!container) return;
    const kinds = ['standard', 'satellite', 'hybrid', 'terrain', 'dark', 'offline'];
    const grouped = {};
    layers.forEach((layer) => {
        const k = layer.layer_kind || 'offline';
        if (!grouped[k]) grouped[k] = [];
        grouped[k].push(layer);
    });

    container.innerHTML = kinds.filter((k) => grouped[k]?.length).map((kind) => {
        const heading = escapeHtml(layerKindLabel(kind, t));
        const options = grouped[kind].map((layer) => {
            const disabled = !layerAvailable(layer) ? ' disabled' : '';
            const checked = layer.id === state.prefs.layerId ? ' checked' : '';
            let badge = layer.installed ? t('installed', 'Installed')
                : (layer.requires_network ? t('online', 'Online') : t('notInstalled', 'Not installed'));
            if (layer.cached_tiles > 0) {
                badge += ` · ${layer.cached_tiles} ${t('cachedTiles', 'cached tiles')}`;
            }
            return `<label class="map-layer-option">
                <input type="radio" name="mapLayer" value="${escapeHtml(layer.id)}"${checked}${disabled}>
                <span class="map-layer-option__body">
                    <span class="map-layer-option__title">${escapeHtml(layer.name)}</span>
                    <span class="map-layer-option__meta">${escapeHtml(badge)} · ${escapeHtml(layer.map_type || '')}</span>
                </span>
            </label>`;
        }).join('');
        return `<div class="map-layer-group"><h3 class="map-layer-group__title">${heading}</h3>${options}</div>`;
    }).join('');

    container.querySelectorAll('input[name="mapLayer"]').forEach((input) => {
        input.addEventListener('change', () => {
            const layer = state.layers.find((l) => l.id === input.value);
            applyLayer(layer);
        });
    });
}

function renderIconPicker() {
    const wrap = document.getElementById('mapIconPicker');
    if (!wrap) return;
    const labels = {
        pin: t('iconPin', 'Pin'),
        document: t('iconDocument', 'Document'),
        camera: t('iconCamera', 'Camera'),
        video: t('iconVideo', 'Video'),
        flag: t('iconFlag', 'Flag'),
        star: t('iconStar', 'Star'),
        diamond: t('iconDiamond', 'Diamond'),
    };
    wrap.innerHTML = MARKER_ICON_IDS.map((id) => {
        const checked = state.prefs.icon === id ? ' checked' : '';
        return `<label class="map-icon-option" title="${escapeHtml(labels[id] || id)}">
            <input type="radio" name="mapIcon" value="${id}"${checked}>
            <span class="map-icon-option__glyph" data-icon="${id}"></span>
        </label>`;
    }).join('');
    wrap.querySelectorAll('input[name="mapIcon"]').forEach((input) => {
        input.addEventListener('change', () => {
            state.prefs.icon = input.value;
            state.prefs.autoIconByType = false;
            savePrefs();
            const autoEl = document.getElementById('mapAutoIconByType');
            if (autoEl) autoEl.checked = false;
            if (state.map) ensurePointLayers();
        });
    });
    wrap.querySelectorAll('.map-icon-option__glyph').forEach((el) => {
        const id = el.getAttribute('data-icon');
        el.style.backgroundColor = state.prefs.color;
        el.dataset.shape = id;
    });
}

function renderColorPicker() {
    const wrap = document.getElementById('mapColorPicker');
    if (!wrap) return;
    wrap.innerHTML = MARKER_COLORS.map((c) => {
        const checked = state.prefs.color === c.value ? ' is-active' : '';
        return `<button type="button" class="map-color-swatch${checked}" data-color="${c.value}" style="background:${c.value}" aria-label="${c.id}"></button>`;
    }).join('');
    wrap.querySelectorAll('.map-color-swatch').forEach((btn) => {
        btn.addEventListener('click', () => {
            state.prefs.color = btn.getAttribute('data-color');
            savePrefs();
            renderIconPicker();
            renderColorPicker();
            if (state.map) ensurePointLayers();
        });
    });
}

function renderPackageList(packages) {
    const list = document.getElementById('mapPackageList');
    if (!list) return;
    const offline = packages.filter((p) => !p.builtin);
    if (!offline.length) {
        list.innerHTML = `<p class="map-rail-hint">—</p>`;
        return;
    }
    list.innerHTML = offline.map((pkg) => {
        const st = pkg.installed ? t('installed', 'Installed') : t('notInstalled', 'Not installed');
        const actions = [];
        if (!pkg.installed && (pkg.download_url || '').trim()) {
            actions.push(`<button type="button" class="btn btn-sm btn-primary" data-map-download="${escapeHtml(pkg.id)}">${escapeHtml(t('download', 'Download'))}</button>`);
        }
        if (pkg.installed) {
            actions.push(`<button type="button" class="btn btn-sm btn-outline-danger" data-map-remove="${escapeHtml(pkg.id)}">${escapeHtml(t('remove', 'Remove'))}</button>`);
        }
        const layerId = `offline-${pkg.id}`;
        actions.push(`<button type="button" class="btn btn-sm btn-outline-secondary" data-map-use="${escapeHtml(layerId)}">${escapeHtml(t('useLayer', 'Use this layer'))}</button>`);
        return `<article class="map-package-item">
            <p class="map-package-item__title">${escapeHtml(pkg.name)}</p>
            <p class="map-package-item__meta">${escapeHtml(st)} · ${escapeHtml(pkg.layer_kind || pkg.map_type || '')}</p>
            <p class="map-package-item__desc">${escapeHtml(pkg.description || '')}</p>
            <div class="map-package-item__actions">${actions.join('')}</div>
            <div class="map-package-progress" hidden data-progress-for="${escapeHtml(pkg.id)}"><span></span></div>
        </article>`;
    }).join('');

    list.querySelectorAll('[data-map-download]').forEach((btn) => {
        btn.addEventListener('click', async () => {
            try {
                await fetchJson(`/api/map/packages/${encodeURIComponent(btn.getAttribute('data-map-download'))}/download`, { method: 'POST' });
                pollDownload(btn.getAttribute('data-map-download'));
            } catch (err) {
                window.alert(err.message || t('downloadFailed', 'Download failed'));
            }
        });
    });
    list.querySelectorAll('[data-map-remove]').forEach((btn) => {
        btn.addEventListener('click', async () => {
            if (!window.confirm(t('removeConfirm', 'Remove this offline map package from disk?'))) return;
            await fetchJson(`/api/map/packages/${encodeURIComponent(btn.getAttribute('data-map-remove'))}`, { method: 'DELETE' });
            await refreshAll();
        });
    });
    list.querySelectorAll('[data-map-use]').forEach((btn) => {
        btn.addEventListener('click', () => {
            const lid = btn.getAttribute('data-map-use');
            state.prefs.layerId = lid;
            savePrefs();
            const layer = state.layers.find((l) => l.id === lid);
            applyLayer(layer);
            renderLayerGroups(state.layers);
        });
    });
}

async function pollDownload(packageId) {
    const bar = document.querySelector(`[data-progress-for="${packageId}"]`);
    if (bar) bar.hidden = false;
    clearInterval(state.pollTimer);
    state.pollTimer = setInterval(async () => {
        try {
            const status = await fetchJson(`/api/map/packages/${encodeURIComponent(packageId)}/download/status`);
            const span = bar?.querySelector('span');
            if (span && status.percent != null) span.style.width = `${status.percent}%`;
            if (status.state === 'ready') {
                clearInterval(state.pollTimer);
                if (bar) bar.hidden = true;
                await refreshAll();
            } else if (status.state === 'error') {
                clearInterval(state.pollTimer);
                window.alert(status.error || t('downloadFailed', 'Download failed'));
            }
        } catch (_e) {
            clearInterval(state.pollTimer);
        }
    }, 1500);
}

async function refreshLayers() {
    const data = await fetchJson(config.layersApi || '/api/map/layers');
    state.layers = data.layers || [];
    renderLayerGroups(state.layers);
    let active = state.layers.find((l) => l.id === state.prefs.layerId);
    if (!layerAvailable(active)) {
        active = state.layers.find((l) => l.layer_kind === 'standard' && layerAvailable(l))
            || state.layers.find((l) => layerAvailable(l))
            || state.layers[0];
        if (active) {
            state.prefs.layerId = active.id;
            savePrefs();
        }
    }
    if (active) applyLayer(active);
}

async function refreshPackages() {
    const data = await fetchJson(config.packagesApi || '/api/map/packages');
    state.packages = data.packages || [];
    renderPackageList(state.packages);
}

async function refreshAll() {
    await Promise.all([refreshLayers(), refreshPackages(), refreshStorageSummary()]);
}

function fitAllPoints() {
    const maplibregl = globalThis.maplibregl;
    if (!state.map || !state.points.length || !maplibregl) return;
    const bounds = new maplibregl.LngLatBounds();
    state.points.forEach((p) => bounds.extend([Number(p.longitude), Number(p.latitude)]));
    state.map.fitBounds(bounds, { padding: 48, maxZoom: 14 });
}

function teardownMapViewer() {
    clearInterval(state.pollTimer);
    if (state.popup) {
        try { state.popup.remove(); } catch (_e) { /* */ }
        state.popup = null;
    }
    if (state.map) {
        try { state.map.remove(); } catch (_e) { /* */ }
        state.map = null;
    }
    state.pmtilesProtocol = null;
    state.booted = false;
}

function exportPortableBundle() {
    window.location.href = '/api/map/bundle/export';
}

async function importPortableBundle(file) {
    if (!file) return;
    const fd = new FormData();
    fd.append('bundle', file, file.name);
    const headers = { Accept: 'application/json', 'X-CSRFToken': csrfToken() };
    const response = await fetch('/api/map/bundle/import', {
        method: 'POST',
        credentials: 'same-origin',
        headers,
        body: fd,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || response.statusText);
    await refreshAll();
    window.alert(t('importBundleOk', `Imported ${data.files_extracted || 0} files into map_data.`));
}

onPageTeardown('map-viewer', teardownMapViewer);

async function boot() {
    if (!isMapViewerActive() || state.booted) return;
    state.booted = true;
    config = readJson('map-viewer-config');
    i18n = readJson('map-viewer-i18n');
    loadPrefs();

    setNetworkStatus();
    window.addEventListener('online', () => { setNetworkStatus(); renderLayerGroups(state.layers); });
    window.addEventListener('offline', () => { setNetworkStatus(); renderLayerGroups(state.layers); });

    document.getElementById('mapRefreshPackages')?.addEventListener('click', () => refreshAll());
    document.getElementById('mapFitPoints')?.addEventListener('click', fitAllPoints);
    document.getElementById('mapRecenter')?.addEventListener('click', fitAllPoints);
    document.getElementById('mapCoordSearch')?.addEventListener('input', () => renderCoordList(state.points));
    document.getElementById('mapMarkerScale')?.addEventListener('input', (e) => {
        state.prefs.scale = Number(e.target.value) || 1;
        savePrefs();
        if (state.map?.getLayer('geolocations-symbol')) {
            state.map.setLayoutProperty('geolocations-symbol', 'icon-size', state.prefs.scale);
        }
    });
    document.getElementById('mapShowLabels')?.addEventListener('change', (e) => {
        state.prefs.showLabels = e.target.checked;
        savePrefs();
        if (state.map) ensurePointLayers();
    });
    document.getElementById('mapAutoIconByType')?.addEventListener('change', (e) => {
        state.prefs.autoIconByType = e.target.checked;
        savePrefs();
        if (state.map) ensurePointLayers();
    });
    document.getElementById('mapOpenFileOnClick')?.addEventListener('change', (e) => {
        state.prefs.openFileOnClick = e.target.checked;
        savePrefs();
    });
    document.getElementById('mapAutoCacheOnline')?.addEventListener('change', (e) => {
        state.prefs.autoCacheOnline = e.target.checked;
        savePrefs();
        const layer = state.layers.find((l) => l.id === state.activeLayerId);
        if (layer) applyLayer(layer);
    });
    document.getElementById('mapExportBundle')?.addEventListener('click', exportPortableBundle);
    const importInput = document.getElementById('mapImportBundleInput');
    document.getElementById('mapImportBundle')?.addEventListener('click', () => importInput?.click());
    importInput?.addEventListener('change', async () => {
        const file = importInput.files && importInput.files[0];
        importInput.value = '';
        if (!file) return;
        try {
            await importPortableBundle(file);
        } catch (err) {
            window.alert(err.message || t('importBundleFailed', 'Import failed'));
        }
    });

    const scaleEl = document.getElementById('mapMarkerScale');
    if (scaleEl) scaleEl.value = String(state.prefs.scale);
    const labelsEl = document.getElementById('mapShowLabels');
    if (labelsEl) labelsEl.checked = state.prefs.showLabels;
    const autoIconEl = document.getElementById('mapAutoIconByType');
    if (autoIconEl) autoIconEl.checked = state.prefs.autoIconByType !== false;
    const openClickEl = document.getElementById('mapOpenFileOnClick');
    if (openClickEl) openClickEl.checked = state.prefs.openFileOnClick !== false;
    const autoCacheEl = document.getElementById('mapAutoCacheOnline');
    if (autoCacheEl) autoCacheEl.checked = state.prefs.autoCacheOnline !== false;

    renderIconPicker();
    renderColorPicker();

    await ensureMapLibraries();
    await refreshAll();
    const points = await loadAllGeolocationPoints();
    applyPointsToMap(points);
    fitAllPoints();
}

export default async function initMapViewerPage() {
    if (!isMapViewerActive()) return;
    teardownMapViewer();
    try {
        await boot();
        markPageActive('map-viewer');
    } catch (err) {
        console.error(err);
        const stage = document.querySelector('.map-viewer-stage');
        if (stage) {
            stage.innerHTML = `<div class="alert alert-danger m-3">${escapeHtml(err.message || t('mapInitFailed', 'Could not initialize map viewer'))}</div>`;
        }
    }
}
