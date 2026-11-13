/**
 * Canvas-generated marker sprites for MapLibre symbol layers.
 */

export const MARKER_ICON_IDS = ['pin', 'document', 'camera', 'video', 'flag', 'star', 'diamond'];

export const MARKER_COLORS = [
    { id: 'cyan', value: '#06b6d4' },
    { id: 'indigo', value: '#4f46e5' },
    { id: 'emerald', value: '#10b981' },
    { id: 'amber', value: '#f59e0b' },
    { id: 'rose', value: '#f43f5e' },
    { id: 'slate', value: '#64748b' },
];

function drawPin(ctx, size, color) {
    const w = size;
    const h = size;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(w / 2, h * 0.38, w * 0.28, 0, Math.PI * 2);
    ctx.fill();
    ctx.beginPath();
    ctx.moveTo(w / 2, h * 0.62);
    ctx.lineTo(w * 0.22, h * 0.95);
    ctx.lineTo(w * 0.78, h * 0.95);
    ctx.closePath();
    ctx.fill();
    ctx.fillStyle = '#fff';
    ctx.beginPath();
    ctx.arc(w / 2, h * 0.38, w * 0.1, 0, Math.PI * 2);
    ctx.fill();
}

function drawDocument(ctx, size, color) {
    ctx.clearRect(0, 0, size, size);
    ctx.fillStyle = color;
    ctx.fillRect(size * 0.22, size * 0.12, size * 0.56, size * 0.76);
    ctx.fillStyle = '#fff';
    ctx.fillRect(size * 0.3, size * 0.28, size * 0.4, size * 0.08);
    ctx.fillRect(size * 0.3, size * 0.42, size * 0.34, size * 0.08);
    ctx.fillRect(size * 0.3, size * 0.56, size * 0.28, size * 0.08);
}

function drawVideo(ctx, size, color) {
    ctx.clearRect(0, 0, size, size);
    ctx.fillStyle = color;
    ctx.fillRect(size * 0.14, size * 0.28, size * 0.72, size * 0.44);
    ctx.beginPath();
    ctx.moveTo(size * 0.72, size * 0.36);
    ctx.lineTo(size * 0.88, size * 0.28);
    ctx.lineTo(size * 0.88, size * 0.72);
    ctx.lineTo(size * 0.72, size * 0.64);
    ctx.closePath();
    ctx.fill();
    ctx.fillStyle = '#fff';
    ctx.fillRect(size * 0.22, size * 0.36, size * 0.42, size * 0.28);
}

function drawCamera(ctx, size, color) {
    ctx.clearRect(0, 0, size, size);
    ctx.fillStyle = color;
    ctx.fillRect(size * 0.12, size * 0.32, size * 0.76, size * 0.48);
    ctx.fillRect(size * 0.32, size * 0.18, size * 0.28, size * 0.16);
    ctx.fillStyle = '#fff';
    ctx.beginPath();
    ctx.arc(size * 0.5, size * 0.56, size * 0.16, 0, Math.PI * 2);
    ctx.fill();
}

function drawFlag(ctx, size, color) {
    ctx.clearRect(0, 0, size, size);
    ctx.strokeStyle = color;
    ctx.lineWidth = size * 0.08;
    ctx.beginPath();
    ctx.moveTo(size * 0.28, size * 0.12);
    ctx.lineTo(size * 0.28, size * 0.88);
    ctx.stroke();
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.moveTo(size * 0.28, size * 0.16);
    ctx.lineTo(size * 0.78, size * 0.32);
    ctx.lineTo(size * 0.28, size * 0.48);
    ctx.closePath();
    ctx.fill();
}

function drawStar(ctx, size, color) {
    ctx.clearRect(0, 0, size, size);
    ctx.fillStyle = color;
    const cx = size / 2;
    const cy = size / 2;
    const spikes = 5;
    const outer = size * 0.38;
    const inner = size * 0.16;
    ctx.beginPath();
    for (let i = 0; i < spikes * 2; i += 1) {
        const r = i % 2 === 0 ? outer : inner;
        const a = (Math.PI / spikes) * i - Math.PI / 2;
        const x = cx + Math.cos(a) * r;
        const y = cy + Math.sin(a) * r;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
    }
    ctx.closePath();
    ctx.fill();
}

function drawDiamond(ctx, size, color) {
    ctx.clearRect(0, 0, size, size);
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.moveTo(size / 2, size * 0.1);
    ctx.lineTo(size * 0.88, size / 2);
    ctx.lineTo(size / 2, size * 0.9);
    ctx.lineTo(size * 0.12, size / 2);
    ctx.closePath();
    ctx.fill();
}

const DRAWERS = {
    pin: drawPin,
    document: drawDocument,
    camera: drawCamera,
    video: drawVideo,
    flag: drawFlag,
    star: drawStar,
    diamond: drawDiamond,
};

export function markerImageId(iconId, color) {
    const safeColor = String(color || '#06b6d4').replace(/[^#a-fA-F0-9]/g, '') || '#06b6d4';
    return `marker-${iconId}-${safeColor.slice(1)}`;
}

export function createMarkerImageData(iconId, color, size = 64) {
    const canvas = document.createElement('canvas');
    canvas.width = size;
    canvas.height = size;
    const ctx = canvas.getContext('2d');
    const drawer = DRAWERS[iconId] || drawPin;
    drawer(ctx, size, color);
    return ctx.getImageData(0, 0, size, size);
}

export async function registerMarkerImages(map, iconId, color) {
    const id = markerImageId(iconId, color);
    if (map.hasImage(id)) return id;
    const data = createMarkerImageData(iconId, color);
    map.addImage(id, data, { pixelRatio: 2 });
    return id;
}

/** Pick marker shape from file type / extension (geotagged files). */
export function iconShapeForFileType(fileType, fileName) {
    const raw = `${fileType || ''} ${fileName || ''}`.toLowerCase();
    if (/\.?(jpe?g|png|gif|webp|bmp|tif|heic|raw|dng)\b/.test(raw)) return 'camera';
    if (/\.?(mp4|mov|avi|mkv|webm|m4v|wmv)\b/.test(raw)) return 'video';
    if (/\.?(mp3|wav|flac|aac|ogg|m4a)\b/.test(raw)) return 'star';
    if (/\.?(pdf|docx?|rtf|odt|txt|md|csv|xlsx?)\b/.test(raw)) return 'document';
    if (/\.?(kml|kmz|gpx|geojson)\b/.test(raw)) return 'flag';
    if (/\.?(zip|rar|7z|tar|gz)\b/.test(raw)) return 'diamond';
    return 'pin';
}

export async function registerMarkerPalette(map, color, shapes = MARKER_ICON_IDS) {
    const ids = [];
    for (const shape of shapes) {
        ids.push(await registerMarkerImages(map, shape, color));
    }
    return ids;
}
