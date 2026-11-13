/**
 * Structured folder export — builds a directory tree from metadata
 * (analyst category, source, side, …) and writes via the File System Access API.
 */

import { getCSRFToken } from '../core/utils.js';
import { collectSelectedIds } from './bulk-analyst-actions.js';
import { navigationState } from '../core/state.js';

let _translationCache = null;

function translationDict() {
    if (_translationCache) return _translationCache;
    const merged = { ...(typeof window !== 'undefined' && window.translations) || {} };
    const structuredEl = typeof document !== 'undefined' && document.getElementById('structured-export-i18n');
    if (structuredEl) {
        try {
            Object.assign(merged, JSON.parse(structuredEl.textContent || '{}'));
        } catch (_e) { /* ignore */ }
    }
    for (const id of ['analyst-categorization-page-data', 'page-data', 'files-list-page-data']) {
        const el = typeof document !== 'undefined' && document.getElementById(id);
        if (!el) continue;
        try {
            const data = JSON.parse(el.textContent || '{}');
            if (data.translations) Object.assign(merged, data.translations);
        } catch (_e) { /* ignore */ }
    }
    _translationCache = merged;
    return merged;
}

const LAYOUT_OPTIONS = [
    { id: 'analyst_category', labelKey: 'layoutAnalystCategory', fallback: 'By analyst category' },
    { id: 'source__analyst_category', labelKey: 'layoutSourceCategory', fallback: 'Source → analyst category' },
    { id: 'analyst_category__source', labelKey: 'layoutCategorySource', fallback: 'Analyst category → source' },
    { id: 'source__side__analyst_category', labelKey: 'layoutSourceSideCategory', fallback: 'Source → side → category' },
    { id: 'source', labelKey: 'layoutSource', fallback: 'By source' },
    { id: 'side', labelKey: 'layoutSide', fallback: 'By side' },
    { id: 'file_type', labelKey: 'layoutFileType', fallback: 'By file type' },
    { id: 'year_month', labelKey: 'layoutYearMonth', fallback: 'By year and month' },
    { id: 'smart_category__analyst_category', labelKey: 'layoutSmartAnalyst', fallback: 'Smart category → analyst category' },
];

function t(key, fallback) {
    return translationDict()[key] || fallback;
}

function notify(kind, message) {
    const fn = { success: window.showSuccess, error: window.showError, warning: window.showWarning, info: window.showInfo }[kind];
    if (typeof fn === 'function') fn(message);
    else if (kind === 'error') window.alert(message);
}

function safeClientFilename(value, fallback) {
    const raw = String(value || fallback || 'export.bin').trim() || fallback;
    return raw.replace(/[\u0000-\u001f<>:"/\\|?*]/g, '_').slice(0, 200);
}

function safeAppUrl(value) {
    if (!value || typeof value !== 'string') return null;
    if (value.startsWith('/') || value.startsWith('http://') || value.startsWith('https://')) return value;
    return null;
}

async function fetchPlan(options) {
    const response = await fetch('/api/export/structured/plan', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCSRFToken(),
        },
        body: JSON.stringify(options),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.success === false) {
        throw new Error(data.error || `HTTP ${response.status}`);
    }
    return data;
}

async function ensureDir(root, relativePath) {
    const parts = String(relativePath || '').split('/').filter(Boolean);
    let dir = root;
    for (const part of parts) {
        dir = await dir.getDirectoryHandle(part, { create: true });
    }
    return dir;
}

async function writeBlobToDir(dirHandle, filename, blob) {
    const handle = await dirHandle.getFileHandle(filename, { create: true });
    const writable = await handle.createWritable();
    await writable.write(blob);
    await writable.close();
}

async function downloadEntry(fileId, payloadKind) {
    const descriptorResponse = await fetch(`/api/file/${fileId}/original`, { credentials: 'same-origin' });
    const descriptorData = await descriptorResponse.json();
    if (!descriptorResponse.ok || !descriptorData.success) {
        throw new Error(descriptorData.error || `HTTP ${descriptorResponse.status}`);
    }
    const original = descriptorData.original || {};
    if (payloadKind === 'originals') {
        if (!original.available) throw new Error(original.message || 'Original unavailable');
        const url = safeAppUrl(original.download_url);
        if (!url) throw new Error('Invalid download URL');
        const fileResponse = await fetch(url, { credentials: 'same-origin' });
        if (!fileResponse.ok) throw new Error(`HTTP ${fileResponse.status}`);
        return {
            blob: await fileResponse.blob(),
            filename: safeClientFilename(original.name, `file_${fileId}`),
        };
    }
    const url = `/api/files/${fileId}/export`;
    const fileResponse = await fetch(url, { credentials: 'same-origin' });
    if (!fileResponse.ok) {
        const problem = await fileResponse.json().catch(() => ({}));
        throw new Error(problem.error || `HTTP ${fileResponse.status}`);
    }
    const sourceName = safeClientFilename(original.name, `file_${fileId}`);
    const stem = sourceName.replace(/\.[^.]+$/, '') || `file_${fileId}`;
    return {
        blob: await fileResponse.blob(),
        filename: `${stem}_extracted.txt`,
    };
}

function folderPreview(plan, maxRows = 8) {
    const stats = plan.folder_stats || {};
    const rows = Object.entries(stats).sort((a, b) => b[1] - a[1]);
    const top = rows.slice(0, maxRows);
    const more = rows.length - top.length;
    return { top, more, folderCount: rows.length };
}

function ensureDialog() {
    let dialog = document.getElementById('structuredExportDialog');
    if (dialog) return dialog;

    dialog = document.createElement('div');
    dialog.id = 'structuredExportDialog';
    dialog.className = 'structured-export-dialog';
    dialog.hidden = true;
    dialog.setAttribute('role', 'dialog');
    dialog.setAttribute('aria-modal', 'true');
    dialog.innerHTML = `
        <div class="structured-export-backdrop" data-structured-dismiss></div>
        <div class="structured-export-panel">
            <header class="structured-export-header">
                <div>
                    <h2 class="structured-export-title">${t('structuredExportTitle', 'Structured folder export')}</h2>
                    <p class="structured-export-subtitle">${t('structuredExportSubtitle', 'Organize copies by analyst category, source, and other metadata.')}</p>
                </div>
                <button type="button" class="structured-export-close" data-structured-dismiss aria-label="${t('close', 'Close')}">&times;</button>
            </header>
            <div class="structured-export-body">
                <section class="structured-export-section">
                    <h3>${t('structuredExportScope', 'What to export')}</h3>
                    <div class="structured-export-scope-grid" id="structuredExportScope"></div>
                </section>
                <section class="structured-export-section">
                    <h3>${t('structuredExportLayout', 'Folder layout')}</h3>
                    <select id="structuredExportLayout" class="form-select form-select-sm"></select>
                    <label class="structured-export-check mt-2">
                        <input type="checkbox" id="structuredExportSmart" />
                        ${t('structuredExportIncludeSmart', 'Include smart (system) category in layout when needed')}
                    </label>
                </section>
                <section class="structured-export-section">
                    <h3>${t('structuredExportPayload', 'File contents')}</h3>
                    <div class="structured-export-payload" id="structuredExportPayload"></div>
                </section>
                <section class="structured-export-section">
                    <h3>${t('structuredExportOptions', 'Options')}</h3>
                    <label class="structured-export-check">
                        <input type="checkbox" id="structuredExportManifest" checked />
                        ${t('structuredExportManifest', 'Write export manifest (JSON) at destination root')}
                    </label>
                    <label class="structured-export-check">
                        <input type="checkbox" id="structuredExportSidecar" />
                        ${t('structuredExportSidecar', 'Write metadata sidecar (.meta.json) next to each file')}
                    </label>
                    <label class="structured-export-field">
                        <span>${t('structuredExportRootSubfolder', 'Optional root subfolder name')}</span>
                        <input type="text" id="structuredExportRootName" class="form-control form-control-sm"
                               placeholder="${t('structuredExportRootPlaceholder', 'e.g. Case_2026-03-28')}" maxlength="80" />
                    </label>
                </section>
                <section class="structured-export-preview" id="structuredExportPreview" hidden>
                    <h3>${t('structuredExportPreview', 'Preview')}</h3>
                    <p class="structured-export-preview-summary" id="structuredExportPreviewSummary"></p>
                    <ul class="structured-export-folder-list" id="structuredExportFolderList"></ul>
                </section>
                <div class="structured-export-progress" id="structuredExportProgress" hidden>
                    <div class="progress" role="progressbar" aria-valuemin="0" aria-valuemax="100">
                        <div class="progress-bar progress-bar-striped progress-bar-animated" id="structuredExportProgressBar" style="width:0%"></div>
                    </div>
                    <p class="small text-muted mb-0 mt-1" id="structuredExportProgressLabel"></p>
                </div>
            </div>
            <footer class="structured-export-footer">
                <button type="button" class="btn btn-outline-secondary btn-sm" data-structured-dismiss>${t('cancel', 'Cancel')}</button>
                <button type="button" class="btn btn-outline-primary btn-sm" id="structuredExportPreviewBtn">${t('structuredExportPreviewBtn', 'Preview layout')}</button>
                <button type="button" class="btn btn-primary btn-sm" id="structuredExportRunBtn">${t('structuredExportRun', 'Choose folder & export')}</button>
            </footer>
        </div>
    `;

    dialog.addEventListener('click', (event) => {
        if (event.target.closest('[data-structured-dismiss]')) dialog.hidden = true;
    });

    const layoutSelect = dialog.querySelector('#structuredExportLayout');
    layoutSelect.innerHTML = LAYOUT_OPTIONS.map((opt) =>
        `<option value="${opt.id}">${t(opt.labelKey, opt.fallback)}</option>`).join('');

    const payloadWrap = dialog.querySelector('#structuredExportPayload');
    payloadWrap.innerHTML = `
        <label class="structured-export-radio"><input type="radio" name="structuredPayload" value="originals" checked /> ${t('structuredExportOriginals', 'Original files')}</label>
        <label class="structured-export-radio"><input type="radio" name="structuredPayload" value="text" /> ${t('structuredExportExtracted', 'Extracted text')}</label>
        <label class="structured-export-radio"><input type="radio" name="structuredPayload" value="both" /> ${t('structuredExportBoth', 'Both (original + extracted text)')}</label>
    `;

    document.body.appendChild(dialog);
    return dialog;
}

function renderScopeOptions(dialog, context) {
    const wrap = dialog.querySelector('#structuredExportScope');
    const scopes = context === 'analyst'
        ? [
            { id: 'analyst_filtered', label: t('structuredScopeFiltered', 'Current analyst view filters') },
            { id: 'analyst_all', label: t('structuredScopeAllCategorized', 'All analyst-categorized files') },
        ]
        : [
            { id: 'selection', label: t('structuredScopeSelection', 'Selected files only') },
            { id: 'loaded', label: t('structuredScopeLoaded', 'All files loaded on this page') },
            { id: 'analyst_all', label: t('structuredScopeAllCategorized', 'All analyst-categorized files in the library') },
        ];
    wrap.innerHTML = scopes.map((s, i) => `
        <label class="structured-export-radio">
            <input type="radio" name="structuredScope" value="${s.id}" ${i === 0 ? 'checked' : ''} />
            ${s.label}
        </label>`).join('');
    dialog.dataset.context = context;
}

function readForm(dialog) {
    const scope = dialog.querySelector('input[name="structuredScope"]:checked')?.value || 'selection';
    const layout = dialog.querySelector('#structuredExportLayout')?.value || 'analyst_category';
    const payload = dialog.querySelector('input[name="structuredPayload"]:checked')?.value || 'originals';
    const includeSmart = dialog.querySelector('#structuredExportSmart')?.checked;
    const manifest = dialog.querySelector('#structuredExportManifest')?.checked;
    const sidecar = dialog.querySelector('#structuredExportSidecar')?.checked;
    const rootName = dialog.querySelector('#structuredExportRootName')?.value?.trim() || '';
    return { scope, layout, payload, includeSmart, manifest, sidecar, rootName };
}

function loadedPageFileIds() {
    const fromState = (navigationState.loadedFiles || [])
        .map((f) => Number(f.id))
        .filter((n) => Number.isFinite(n) && n > 0);
    if (fromState.length) return fromState;
    return Array.from(document.querySelectorAll('.file-checkbox, .file-select-checkbox'))
        .map((el) => Number(el.value))
        .filter((n) => Number.isFinite(n) && n > 0);
}

function buildPlanRequest(form, extra = {}) {
    const body = {
        scope: form.scope,
        layout: form.layout,
        include_smart_category: form.includeSmart,
        ...extra,
    };
    if (form.scope === 'selection') {
        body.scope = 'selection';
        body.file_ids = collectSelectedIds();
    }
    if (form.scope === 'loaded') {
        body.scope = 'selection';
        body.file_ids = loadedPageFileIds();
    }
    if (form.scope === 'analyst_filtered' && typeof extra.analystFilters === 'function') {
        body.filters = extra.analystFilters();
    }
    return body;
}

export async function openStructuredFolderExport(options = {}) {
    _translationCache = null;
    const dialog = ensureDialog();
    renderScopeOptions(dialog, options.context || 'files');
    dialog.hidden = false;
    dialog.querySelector('#structuredExportPreview').hidden = true;
    dialog.querySelector('#structuredExportProgress').hidden = true;
    dialog._analystFilters = options.analystFilters || null;

    const previewBtn = dialog.querySelector('#structuredExportPreviewBtn');
    const runBtn = dialog.querySelector('#structuredExportRunBtn');

    previewBtn.onclick = async () => {
        const form = readForm(dialog);
        try {
            const req = buildPlanRequest(form, {
                analystFilters: dialog._analystFilters,
            });
            if ((form.scope === 'selection' || form.scope === 'loaded') && (!req.file_ids || !req.file_ids.length)) {
                notify('warning', t('pleaseSelectFilesToExport', 'Please select files to export'));
                return;
            }
            const plan = await fetchPlan(req);
            const { top, more, folderCount } = folderPreview(plan);
            dialog.querySelector('#structuredExportPreview').hidden = false;
            const summary = dialog.querySelector('#structuredExportPreviewSummary');
            summary.textContent = t('structuredExportPreviewSummary', '{files} files → {folders} folders')
                .replace('{files}', String(plan.total || 0))
                .replace('{folders}', String(folderCount));
            if (plan.truncated) {
                summary.textContent += ' ' + t('structuredExportTruncated', `(showing first ${plan.total} of ${plan.total_matched})`);
            }
            const list = dialog.querySelector('#structuredExportFolderList');
            list.innerHTML = top.map(([folder, count]) =>
                `<li><code>${folder === '.' ? '/' : folder}/</code> <span class="text-muted">(${count})</span></li>`).join('')
                + (more > 0 ? `<li class="text-muted">+ ${more} ${t('structuredExportMoreFolders', 'more folders')}</li>` : '');
            dialog._lastPlan = plan;
        } catch (error) {
            notify('error', error.message || t('structuredExportPlanFailed', 'Could not build layout'));
        }
    };

    runBtn.onclick = async () => {
        const form = readForm(dialog);
        if (typeof window.showDirectoryPicker !== 'function') {
            notify('info', t('folderPickerUnsupported', 'Folder export is not supported in this browser.'));
            return;
        }
        let plan = dialog._lastPlan;
        try {
            const req = buildPlanRequest(form, { analystFilters: dialog._analystFilters });
            if ((form.scope === 'selection' || form.scope === 'loaded') && (!req.file_ids || !req.file_ids.length)) {
                notify('warning', t('pleaseSelectFilesToExport', 'Please select files to export'));
                return;
            }
            plan = await fetchPlan(req);
        } catch (error) {
            notify('error', error.message || t('structuredExportPlanFailed', 'Could not build layout'));
            return;
        }
        if (!plan.entries?.length) {
            notify('warning', t('structuredExportEmpty', 'No files match this export scope.'));
            return;
        }

        let rootDir;
        try {
            rootDir = await window.showDirectoryPicker({ mode: 'readwrite' });
        } catch (error) {
            if (error?.name !== 'AbortError') {
                notify('error', t('folderPickerFailed', 'Could not write to the selected folder.'));
            }
            return;
        }

        if (form.rootName) {
            const cleaned = form.rootName.replace(/[\u0000-\u001f<>:"/\\|?*]/g, '_').slice(0, 80);
            try {
                rootDir = await rootDir.getDirectoryHandle(cleaned, { create: true });
            } catch (error) {
                notify('error', t('folderPickerFailed', 'Could not write to the selected folder.') + ` ${error.message}`);
                return;
            }
        }

        const progressWrap = dialog.querySelector('#structuredExportProgress');
        const progressBar = dialog.querySelector('#structuredExportProgressBar');
        const progressLabel = dialog.querySelector('#structuredExportProgressLabel');
        progressWrap.hidden = false;
        runBtn.disabled = true;
        previewBtn.disabled = true;

        const kinds = form.payload === 'both' ? ['originals', 'text'] : [form.payload];
        const usedByFolder = new Map();
        let done = 0;
        const totalSteps = plan.entries.length * kinds.length;
        const failures = [];

        function uniqueName(folderKey, name) {
            if (!usedByFolder.has(folderKey)) usedByFolder.set(folderKey, new Set());
            const used = usedByFolder.get(folderKey);
            const dot = name.lastIndexOf('.');
            const stem = dot > 0 ? name.slice(0, dot) : name;
            const ext = dot > 0 ? name.slice(dot) : '';
            let candidate = name;
            let suffix = 2;
            while (used.has(candidate.toLocaleLowerCase())) {
                candidate = `${stem}_${suffix++}${ext}`;
            }
            used.add(candidate.toLocaleLowerCase());
            return candidate;
        }

        for (const entry of plan.entries) {
            const rel = entry.relative_path || entry.filename;
            const parts = rel.split('/');
            const filename = parts.pop();
            const folderPath = parts.join('/');

            for (const kind of kinds) {
                try {
                    const { blob, filename: downloadedName } = await downloadEntry(entry.file_id, kind);
                    let outName = kind === 'text' && form.payload === 'both'
                        ? downloadedName
                        : filename || downloadedName;
                    const folderKey = folderPath || '.';
                    outName = uniqueName(folderKey, safeClientFilename(outName, `file_${entry.file_id}`));

                    const dir = folderPath
                        ? await ensureDir(rootDir, folderPath)
                        : rootDir;
                    await writeBlobToDir(dir, outName, blob);

                    if (form.sidecar) {
                        const meta = { ...entry.metadata, file_id: entry.file_id, export_name: outName, payload: kind };
                        const metaBlob = new Blob([JSON.stringify(meta, null, 2)], { type: 'application/json' });
                        const stem = outName.replace(/\.[^.]+$/, '') || outName;
                        await writeBlobToDir(dir, `${stem}.meta.json`, metaBlob);
                    }
                } catch (error) {
                    failures.push({ file_id: entry.file_id, message: error.message });
                }
                done += 1;
                const pct = Math.round((done / totalSteps) * 100);
                progressBar.style.width = `${pct}%`;
                progressLabel.textContent = `${done} / ${totalSteps}`;
            }
        }

        if (form.manifest) {
            const manifest = {
                generated_at: plan.generated_at,
                layout: plan.layout,
                total_files: plan.total,
                total_matched: plan.total_matched,
                truncated: plan.truncated,
                payload: form.payload,
                entries: plan.entries,
                failures,
            };
            const manifestBlob = new Blob([JSON.stringify(manifest, null, 2)], { type: 'application/json' });
            await writeBlobToDir(rootDir, 'structured-export-manifest.json', manifestBlob);
        }

        runBtn.disabled = false;
        previewBtn.disabled = false;
        if (failures.length) {
            notify('warning', `${t('folderExportPartial', 'Some documents could not be saved.')} (${failures.length})`);
        } else {
            notify('success', t('structuredExportDone', 'Structured export completed.'));
            dialog.hidden = true;
        }
    };
}

export default openStructuredFolderExport;
