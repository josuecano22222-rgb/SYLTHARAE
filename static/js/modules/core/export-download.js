/** Shared browser-side save helpers for generated exports.
 *
 * When the File System Access API is available, let the user choose both a
 * name and destination before the request starts. Other browsers fall back to
 * their standard download manager, whose save destination is browser-managed.
 */
const EXPORT_MIME_TYPES = {
    csv: 'text/csv',
    xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    xls: 'application/vnd.ms-excel',
    json: 'application/json',
    txt: 'text/plain',
    docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    zip: 'application/zip',
};

export function ensureExportExtension(value, extension, fallback = 'export') {
    const suffix = String(extension || '').replace(/^\.+/, '').toLowerCase();
    let filename = String(value || '').split(/[\\/]/).pop()
        .replace(/[\u0000-\u001f<>:"|?*]/g, '_')
        .replace(/^\.+$/, '')
        .trim();
    if (!filename || filename === '.' || filename === '..') filename = fallback;
    if (suffix && !filename.toLowerCase().endsWith(`.${suffix}`)) {
        filename += `.${suffix}`;
    }
    return filename.slice(0, 180) || `${fallback}${suffix ? `.${suffix}` : ''}`;
}

/** Return a FileSystemFileHandle, null when unsupported, or false if cancelled. */
export async function chooseExportDestination(filename) {
    if (typeof window.showSaveFilePicker !== 'function') return null;
    const suggestedName = ensureExportExtension(filename, '');
    const extension = suggestedName.includes('.')
        ? suggestedName.split('.').pop().toLowerCase()
        : '';
    const mimeType = EXPORT_MIME_TYPES[extension];
    const options = { suggestedName };
    if (mimeType && extension) {
        options.types = [{
            description: `${extension.toUpperCase()} file`,
            accept: { [mimeType]: [`.${extension}`] },
        }];
    }
    try {
        return await window.showSaveFilePicker(options);
    } catch (error) {
        if (error?.name === 'AbortError') return false;
        throw error;
    }
}

/** Save to the chosen local handle, or fall back to a browser download. */
export async function saveExportBlob(blob, filename, destination = null) {
    if (destination === false) return false;
    if (destination) {
        const writable = await destination.createWritable();
        await writable.write(blob);
        await writable.close();
        return true;
    }

    const objectUrl = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = objectUrl;
    anchor.download = filename;
    anchor.hidden = true;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
    return true;
}
