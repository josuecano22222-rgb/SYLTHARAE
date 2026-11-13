/**
 * Build MapLibre GL styles from /api/map/layers definitions.
 */

/** Font glyphs for symbol labels (MapLibre requires glyphs when using text-field). */
export const MAP_STYLE_GLYPHS = 'https://demotiles.maplibre.org/font/{fontstack}/{range}.pif';

function cacheTileUrl(origin, layerId, sourceId) {
    const lid = encodeURIComponent(layerId);
    const sid = encodeURIComponent(sourceId);
    return `${origin}/api/map/cache/${lid}/${sid}/{z}/{x}/{y}.png`;
}

export function buildStyleFromLayer(layer, origin, options = {}) {
    const autoCache = Boolean(options.autoCacheOnline && layer?.auto_cache_eligible !== false);
    if (!layer || !Array.isArray(layer.sources) || !layer.sources.length) {
        return {
            version: 8,
            glyphs: MAP_STYLE_GLYPHS,
            sources: {},
            layers: [{ id: 'bg', type: 'background', paint: { 'background-color': '#1e293b' } }],
        };
    }

    const sources = {};
    const layers = [];
    let firstRaster = true;

    for (const src of layer.sources) {
        const sid = src.id || 'base';
        if (src.type === 'raster' || (!src.type && src.tiles)) {
            const tileUrls = autoCache && layer.id
                ? [cacheTileUrl(origin, layer.id, sid)]
                : src.tiles;
            sources[sid] = {
                type: 'raster',
                tiles: tileUrls,
                tileSize: src.tile_size || 256,
                maxzoom: src.maxzoom || 19,
                attribution: src.attribution || layer.attribution || '',
            };
            layers.push({
                id: `layer-${sid}`,
                type: 'raster',
                source: sid,
                paint: src.opacity != null ? { 'raster-opacity': src.opacity } : {},
            });
            firstRaster = false;
            continue;
        }
        if (src.type === 'offline_raster' && src.package_id) {
            const pid = encodeURIComponent(src.package_id);
            sources[sid] = {
                type: 'raster',
                tiles: [`${origin}/api/map/raster/${pid}/{z}/{x}/{y}.png`],
                tileSize: 256,
                maxzoom: src.maxzoom || 18,
                attribution: src.attribution || '',
            };
            layers.push({ id: `layer-${sid}`, type: 'raster', source: sid });
            continue;
        }
        if (src.type === 'pmtiles' && src.package_id) {
            const pid = encodeURIComponent(src.package_id);
            const url = `${origin}/api/map/pmtiles/${pid}`;
            sources[sid] = {
                type: 'vector',
                url: `pmtiles://${url}`,
                attribution: src.attribution || '',
            };
            layers.push({
                id: `layer-${sid}-fill`,
                type: 'fill',
                source: sid,
                'source-layer': src.source_layer || 'earth',
                paint: { 'fill-color': '#cbd5e1' },
            });
            layers.push({
                id: `layer-${sid}-line`,
                type: 'line',
                source: sid,
                'source-layer': src.source_layer || 'earth',
                paint: { 'line-color': '#64748b', 'line-width': 0.5 },
            });
        }
    }

    if (!layers.length && firstRaster) {
        layers.push({ id: 'bg', type: 'background', paint: { 'background-color': '#1e293b' } });
    }

    return { version: 8, glyphs: MAP_STYLE_GLYPHS, sources, layers };
}

export function layerKindLabel(kind, t) {
    const map = {
        standard: t('mapKindStandard', 'Standard'),
        satellite: t('mapKindSatellite', 'Satellite'),
        hybrid: t('mapKindHybrid', 'Hybrid'),
        terrain: t('mapKindTerrain', 'Terrain'),
        dark: t('mapKindDark', 'Dark'),
        offline: t('mapKindOffline', 'Offline'),
    };
    return map[kind] || kind;
}
