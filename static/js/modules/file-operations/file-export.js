/**
 * File Export Functions
 * Handles exporting files and file content
 */

import { endpoints } from '../api/endpoints.js';
import { getCSRFToken } from '../core/utils.js';
import {
    chooseExportDestination,
    ensureExportExtension,
    saveExportBlob,
} from '../core/export-download.js';

/**
 * Export a single file
 * @param {number} fileId - File ID
 */
export function exportFile(fileId) {
    const link = document.createElement('a');
    link.href = endpoints.fileExport(fileId);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
}

/**
 * The two batch export kinds, as one function: the extracted content of
 * the selection (mode "text") or the original source files (mode
 * "originals", one ZIP for many). Both are Save As: the reader names the
 * archive and picks its location before anything is fetched.
 *
 * @param {Array<number|string>} fileIds - the selection
 * @param {string} mode - "text" (content) | "originals" (source files)
 */
export async function exportSelectedFiles(fileIds, mode = 'text') {
    if (!fileIds || fileIds.length === 0) {
        if (window.showWarning) {
            window.showWarning(window.translations?.pleaseSelectFilesToExport || 'Please select files to export');
        }
        return;
    }

    const kind = mode === 'originals' ? 'originals' : 'text';
    const suggested = kind === 'originals' ? 'original_files' : 'extracted_content';
    const extension = 'zip';

    // The count is part of the ask: the archive is about to carry this many
    // files, and the name is the reader's to choose or keep.
    const name = await askArchiveName(
        `${suggested}.${extension}`,
        fileIds.length,
        kind === 'originals'
            ? (window.translations?.exportOriginalsCount || 'original files')
            : (window.translations?.exportContentCount || 'extracted contents'));
    if (name === null) return;

    let destination;
    try {
        destination = await chooseExportDestination(ensureExportExtension(name, extension, suggested));
        if (destination === false) return;
    } catch (error) {
        console.warn('Save location picker unavailable:', error);
    }

    try {
        const response = await fetch(endpoints.bulkExport(), {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCSRFToken(),
            },
            body: JSON.stringify({
                file_ids: fileIds.map((id) => parseInt(id, 10)).filter(Number.isFinite),
                mode: kind,
                filename: name,
            }),
        });
        if (!response.ok) {
            const problem = await response.json().catch(() => ({}));
            throw new Error(problem.error || `HTTP ${response.status}`);
        }
        const blob = await response.blob();
        const saved = await saveExportBlob(
            blob, ensureExportExtension(name, extension, suggested), destination);
        if (saved && window.showSuccess) {
            window.showSuccess(`${fileIds.length} files exported.`);
        }
    } catch (error) {
        console.error('Bulk export failed:', error);
        if (window.showError) {
            window.showError(error.message || 'Could not export the selection.');
        } else if (window.alert) {
            window.alert(error.message || 'Could not export the selection.');
        }
    }
}

/** Name the archive: an inline dialog, never a browser prompt. Resolves
 *  with the chosen name, or null when the reader cancelled. */
export function askArchiveName(defaultName, count, kindLabel = 'files') {
    return new Promise((resolve) => {
        const backdrop = document.createElement('div');
        backdrop.className = 'file-actions-backdrop';
        backdrop.style.zIndex = '1080';
        const panel = document.createElement('div');
        panel.className = 'file-actions-panel file-actions-dialog';
        panel.setAttribute('role', 'dialog');
        panel.setAttribute('aria-modal', 'true');
        panel.innerHTML = ''
            + '<div class="file-actions-panel-head"><h3 class="file-actions-title"></h3></div>'
            + '<div class="file-actions-panel-body">'
            + '<p class="file-actions-count"></p>'
            + '<label class="file-actions-field"><span></span>'
            + '<input type="text" class="form-control"></label>'
            + '<div class="file-actions-dialog-actions">'
            + '<button type="button" class="btn btn-primary" data-archive-save></button>'
            + '<button type="button" class="btn btn-outline-secondary" data-archive-cancel></button>'
            + '</div></div>';
        panel.querySelector('.file-actions-title').textContent =
            window.translations?.nameTheArchive || 'Name the archive';
        panel.querySelector('.file-actions-count').textContent = `${count} ${kindLabel}`;
        panel.querySelector('.file-actions-field span').textContent =
            window.translations?.archiveName || 'File name';
        const input = panel.querySelector('input');
        input.value = defaultName;
        panel.querySelector('[data-archive-save]').textContent =
            window.translations?.save || 'Save';
        panel.querySelector('[data-archive-cancel]').textContent =
            window.translations?.cancel || 'Cancel';

        const done = (value) => {
            backdrop.remove();
            document.removeEventListener('keydown', onKey, true);
            resolve(value);
        };
        const onKey = (event) => {
            if (event.key === 'Escape') { event.stopPropagation(); done(null); }
            if (event.key === 'Enter') { event.stopPropagation(); done(input.value.trim() || defaultName); }
        };
        panel.querySelector('[data-archive-save]').addEventListener('click',
            () => done(input.value.trim() || defaultName));
        panel.querySelector('[data-archive-cancel]').addEventListener('click', () => done(null));
        backdrop.addEventListener('click', (event) => {
            if (event.target === backdrop) done(null);
        });
        document.addEventListener('keydown', onKey, true);
        backdrop.appendChild(panel);
        document.body.appendChild(backdrop);
        input.focus();
        input.select();
    });
}

/**
 * Copy modal content to clipboard
 */
export async function copyModalContent() {
    const content = window.currentModalFileContent || document.getElementById('modalContentText')?.textContent || '';
    if (!content) {
        if (window.showWarning) {
            window.showWarning('No content available to copy');
        }
        return;
    }
    
    try {
        await navigator.clipboard.writeText(content);
        // Show temporary feedback
        const btn = document.querySelector('button[data-on-click*="copyModalContent"]');
        if (btn) {
            const originalText = btn.innerHTML;
            btn.innerHTML = '<i class="bi bi-check me-1"></i>Copied!';
            btn.classList.add('btn-success');
            btn.classList.remove('btn-outline-primary');
            setTimeout(() => {
                btn.innerHTML = originalText;
                btn.classList.remove('btn-success');
                btn.classList.add('btn-outline-primary');
            }, 2000);
        } else {
            if (window.showSuccess) {
                window.showSuccess('Content copied to clipboard!');
            }
        }
    } catch (err) {
        console.error('Failed to copy:', err);
        if (window.showError) {
            window.showError('Failed to copy content to clipboard');
        }
    }
}

/**
 * Normalize coordinates to a map-compatible format
 * Handles various input formats and converts to "latitude, longitude" (decimal degrees)
 * @param {string|number} lat - Latitude value or coordinate string
 * @param {number} lon - Longitude value (optional if lat is a string)
 * @returns {string} Normalized coordinates in format "latitude, longitude"
 */
function normalizeCoordinatesForMaps(lat, lon = null) {
    // If lat is a string and lon is null, parse the string
    if (typeof lat === 'string' && lon === null) {
        const coordStr = lat.trim();
        
        // Remove parentheses if present
        const cleaned = coordStr.replace(/[()]/g, '').trim();
        
        // Try to parse as "lat, lon" format
        const parts = cleaned.split(/[,\s]+/).filter(p => p.trim());
        
        if (parts.length >= 2) {
            // Extract numeric values, handling formats like "32.123456°N, 34.789012°E"
            const latMatch = parts[0].match(/([-+]?\d+\.?\d*)/);
            const lonMatch = parts[1].match(/([-+]?\d+\.?\d*)/);
            
            if (latMatch && lonMatch) {
                let latNum = parseFloat(latMatch[1]);
                let lonNum = parseFloat(lonMatch[1]);
                
                // Handle cardinal directions (N/S/E/W)
                if (parts[0].toUpperCase().includes('S')) {
                    latNum = -Math.abs(latNum);
                } else if (parts[0].toUpperCase().includes('N')) {
                    latNum = Math.abs(latNum);
                }
                
                if (parts[1].toUpperCase().includes('W')) {
                    lonNum = -Math.abs(lonNum);
                } else if (parts[1].toUpperCase().includes('E')) {
                    lonNum = Math.abs(lonNum);
                }
                
                // Validate ranges
                if (latNum >= -90 && latNum <= 90 && lonNum >= -180 && lonNum <= 180) {
                    // Format to 6 decimal places (standard GPS precision)
                    return `${latNum.toFixed(6)}, ${lonNum.toFixed(6)}`;
                }
            }
        }
        
        // If parsing failed, return cleaned string as fallback
        return cleaned;
    }
    
    // If we have separate lat and lon values
    if (lat !== null && lat !== undefined && lon !== null && lon !== undefined) {
        const latNum = typeof lat === 'string' ? parseFloat(lat) : lat;
        const lonNum = typeof lon === 'string' ? parseFloat(lon) : lon;
        
        if (!isNaN(latNum) && !isNaN(lonNum)) {
            // Validate ranges
            if (latNum >= -90 && latNum <= 90 && lonNum >= -180 && lonNum <= 180) {
                // Format to 6 decimal places (standard GPS precision)
                return `${latNum.toFixed(6)}, ${lonNum.toFixed(6)}`;
            }
        }
    }
    
    // Fallback: return original string if provided
    return typeof lat === 'string' ? lat : '';
}

/**
 * Copy geolocation coordinates to clipboard in map-compatible format
 * @param {string|number} coordinates - Coordinates string (e.g., "32.123456, 34.789012") or latitude
 * @param {HTMLElement} button - The button element that triggered the copy
 * @param {string|number} longitude - Longitude value (if coordinates is latitude number/string)
 */
export async function copyGeolocationCoordinates(coordinates, button, longitude = null) {
    if (!coordinates && (longitude === null || longitude === '')) {
        if (window.showWarning) {
            window.showWarning('No coordinates available to copy');
        }
        return;
    }
    
    try {
        // Convert string parameters to numbers if they're numeric strings
        let lat = coordinates;
        let lon = longitude;
        
        // If longitude is provided and both are strings that look like numbers, convert them
        if (longitude !== null && longitude !== '') {
            const latNum = typeof coordinates === 'string' ? parseFloat(coordinates) : coordinates;
            const lonNum = typeof longitude === 'string' ? parseFloat(longitude) : longitude;
            
            if (!isNaN(latNum) && !isNaN(lonNum)) {
                lat = latNum;
                lon = lonNum;
            }
        }
        
        // Normalize coordinates to map-compatible format
        const normalizedCoords = normalizeCoordinatesForMaps(lat, lon);
        
        if (!normalizedCoords) {
            if (window.showWarning) {
                window.showWarning('Invalid coordinates format');
            }
            return;
        }
        
        // Copy normalized coordinates to clipboard
        await navigator.clipboard.writeText(normalizedCoords);
        
        // Show visual feedback on the button
        if (button) {
            const originalHTML = button.innerHTML;
            const originalClasses = button.className;
            
            button.innerHTML = '<i class="bi bi-check"></i>';
            button.classList.remove('btn-outline-secondary');
            button.classList.add('btn-success');
            button.disabled = true;
            
            setTimeout(() => {
                button.innerHTML = originalHTML;
                button.className = originalClasses;
                button.disabled = false;
            }, 2000);
        }
        
        // Show success notification if available
        if (window.showSuccess) {
            window.showSuccess(`Coordinates copied: ${normalizedCoords}`);
        }
    } catch (err) {
        console.error('Failed to copy coordinates:', err);
        if (window.showError) {
            window.showError('Failed to copy coordinates to clipboard');
        }
        // Reset button state on error
        if (button) {
            const originalHTML = button.innerHTML;
            const originalClasses = button.className;
            button.innerHTML = originalHTML;
            button.className = originalClasses;
            button.disabled = false;
        }
    }
}

/**
 * Download modal content as file
 */
export function downloadModalContent() {
    const content = window.currentModalFileContent || document.getElementById('modalContentText')?.textContent || '';
    if (!content) {
        if (window.showWarning) {
            window.showWarning('No content available to download');
        }
        return;
    }
    
    const fileName = window.currentModalFileName || 'file';
    const blob = new Blob([content], { type: 'text/plain' });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${fileName}_content.txt`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    window.URL.revokeObjectURL(url);
}

/**
 * Export modal file
 */
export function exportModalFile() {
    const fileId = window.currentModalFileId || (window.fms && window.fms.core && window.fms.core.state && window.fms.core.state.fileNavigationState && window.fms.core.state.fileNavigationState.currentFiles && window.fms.core.state.fileNavigationState.currentFiles[window.fms.core.state.fileNavigationState.currentIndex]?.id);
    if (!fileId) {
        if (window.showError) {
            window.showError('File ID not available for export');
        }
        return;
    }
    
    window.open(`/file/${fileId}/export?format=pdf`, '_blank');
}

