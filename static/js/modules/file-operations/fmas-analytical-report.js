/**
 * File Manager → analytical report UI.
 *
 * From a selection (or one file), opens a form dialog (report kind, language,
 * delivery) and runs a registered report against those path ids. The
 * readable HTML artifact — narratives and explanatory text — is opened or
 * printed; CSV / XLSX / JSON downloads use the same run. No JSON editing.
 */

import { getCSRFToken, getCSRFTokenAsync } from '../core/utils.js';
import { collectSelectedIds } from './bulk-analyst-actions.js';

const FINISHED = new Set(['COMPLETED', 'COMPLETED_WITH_WARNINGS', 'FAILED', 'CANCELLED']);
const JOB_POLL_MS = 1500;
const JOB_POLL_LIMIT = 400;

// Must match core/criteria/model.py MAX_IDS_PER_DIMENSION (tests enforce it).
const MAX_REPORT_FILES = 1000;

const REPORT_CHOICES = [
    {
        id: 'entry_content',
        label: 'Full entry report (printable)',
        hint: 'Dedicated template: identity, analyst classifications, and the full extracted text of each selected file — ready to print.',
        live: true,
    },
    {
        id: 'comprehensive',
        label: 'Comprehensive intelligence',
        hint: 'Overview, distinctive terms, and keyword/category reach — with full explanatory narratives.',
    },
    {
        id: 'search_results',
        label: 'File listing with match count',
        hint: 'Every selected file and the exact match count, with criteria explanation.',
    },
    {
        id: 'term_keyness',
        label: 'Distinctive terms',
        hint: 'Words the selection uses more (or less) than the rest of the collection.',
    },
];

const LANGUAGE_FALLBACK = ['en', 'ar', 'he', 'fa', 'hr'];
const LANGUAGE_LABELS = {
    en: 'English',
    ar: 'العربية',
    he: 'עברית',
    fa: 'فارسی',
    hr: 'Hrvatski',
};

const DELIVERY = [
    { id: 'open', label: 'Open readable report', format: 'html', print: false },
    { id: 'print', label: 'Print readable report', format: 'html', print: true },
    { id: 'xlsx', label: 'Download spreadsheet (XLSX)', format: 'xlsx', print: false },
    { id: 'csv', label: 'Download tables (CSV)', format: 'csv', print: false },
    { id: 'json', label: 'Download data (JSON)', format: 'json', print: false },
];

let languagesCache = null;
let dialogEl = null;
let pendingPathIds = null;

function t(key, fallback) {
    return (window.translations && window.translations[key]) || fallback;
}

function csrf() {
    return getCSRFToken() ||
        document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
}

async function api(method, url, payload) {
    const headers = { Accept: 'application/json' };
    const init = { method, credentials: 'same-origin', headers };
    if (method !== 'GET') {
        headers['Content-Type'] = 'application/json';
        headers['X-CSRFToken'] = (await getCSRFTokenAsync()) || csrf();
        init.body = JSON.stringify(payload ?? {});
    }
    const resp = await fetch(url, init);
    const body = await resp.json().catch(() => null);
    if (!resp.ok || (body && body.success === false)) {
        const err = body && body.error;
        const message = (err && (err.message || err.code))
            || (typeof err === 'string' ? err : '')
            || `Request failed (HTTP ${resp.status})`;
        throw new Error(message);
    }
    return body;
}

async function loadLanguages() {
    if (languagesCache) return languagesCache;
    try {
        const body = await api('GET', '/api/reports/dashboard');
        if (Array.isArray(body.languages) && body.languages.length) {
            languagesCache = body.languages;
            return languagesCache;
        }
    } catch (_) { /* fall through */ }
    languagesCache = LANGUAGE_FALLBACK.slice();
    return languagesCache;
}

async function followJob(job, statusNode) {
    let current = job;
    for (let i = 0; !FINISHED.has(current.status) && i < JOB_POLL_LIMIT; i += 1) {
        if (statusNode) {
            statusNode.textContent = t('reportRunning', 'Building report…') +
                ` (${current.status || '…'})`;
        }
        await new Promise((resolve) => setTimeout(resolve, JOB_POLL_MS));
        const body = await api('GET', `/api/jobs/${encodeURIComponent(current.job_id)}`);
        current = body.job || body;
    }
    return current;
}

function ensureDialog() {
    if (dialogEl) return dialogEl;
    const wrap = document.createElement('div');
    wrap.id = 'fmasReportDialog';
    wrap.className = 'fmas-report-dialog';
    wrap.setAttribute('role', 'dialog');
    wrap.setAttribute('aria-modal', 'true');
    wrap.setAttribute('aria-labelledby', 'fmasReportDialogTitle');
    wrap.hidden = true;
    wrap.innerHTML = `
        <div class="fmas-report-dialog-backdrop" data-fmas-report-dismiss></div>
        <div class="fmas-report-dialog-panel">
            <header class="fmas-report-dialog-header">
                <h2 id="fmasReportDialogTitle">${t('analyticalReport', 'Analytical report')}</h2>
                <p class="fmas-report-dialog-lead" id="fmasReportDialogLead"></p>
                <button type="button" class="fmas-report-dialog-close" data-fmas-report-dismiss
                        aria-label="${t('close', 'Close')}">&times;</button>
            </header>
            <form id="fmasReportForm" class="fmas-report-dialog-form" novalidate>
                <div class="fmas-report-field">
                    <label for="fmasReportKind">${t('reportKind', 'Report type')}</label>
                    <select id="fmasReportKind" class="form-select" required></select>
                    <p class="fmas-report-hint" id="fmasReportKindHint"></p>
                </div>
                <div class="fmas-report-field" id="fmasReportDirectionWrap" hidden>
                    <label for="fmasReportDirection">${t('termDirection', 'Term direction')}</label>
                    <select id="fmasReportDirection" class="form-select">
                        <option value="over">${t('termsOver', 'Used more often in the selection')}</option>
                        <option value="under">${t('termsUnder', 'Used less often in the selection')}</option>
                    </select>
                </div>
                <div class="fmas-report-field">
                    <label for="fmasReportLanguage">${t('reportLanguage', 'Report language')}</label>
                    <select id="fmasReportLanguage" class="form-select" required></select>
                </div>
                <div class="fmas-report-field">
                    <label for="fmasReportDelivery">${t('reportDelivery', 'Delivery')}</label>
                    <select id="fmasReportDelivery" class="form-select" required></select>
                    <p class="fmas-report-hint">${t('reportDeliveryHint',
                        'Readable HTML includes explanatory narratives for analysts. Spreadsheet and CSV exports carry the underlying tables.')}</p>
                </div>
                <p class="fmas-report-status" id="fmasReportStatus" role="status" aria-live="polite"></p>
                <div class="fmas-report-dialog-actions">
                    <button type="button" class="action-btn fmas-report-cancel" data-fmas-report-dismiss>
                        ${t('cancel', 'Cancel')}
                    </button>
                    <button type="submit" class="action-btn export-btn fmas-report-submit" id="fmasReportSubmit">
                        <i class="bi bi-file-earmark-richtext me-1" aria-hidden="true"></i>
                        ${t('generateReport', 'Generate report')}
                    </button>
                </div>
            </form>
            <iframe id="fmasReportPrintFrame" class="fmas-report-print-frame" title="Print preview" hidden></iframe>
        </div>
    `;
    document.body.appendChild(wrap);

    const kindSelect = wrap.querySelector('#fmasReportKind');
    REPORT_CHOICES.forEach((choice) => {
        const opt = document.createElement('option');
        opt.value = choice.id;
        opt.textContent = t(`reportKind_${choice.id}`, choice.label);
        kindSelect.appendChild(opt);
    });
    const deliverySelect = wrap.querySelector('#fmasReportDelivery');
    DELIVERY.forEach((choice) => {
        const opt = document.createElement('option');
        opt.value = choice.id;
        opt.textContent = t(`reportDelivery_${choice.id}`, choice.label);
        deliverySelect.appendChild(opt);
    });

    const syncHint = () => {
        const choice = REPORT_CHOICES.find((c) => c.id === kindSelect.value) || REPORT_CHOICES[0];
        wrap.querySelector('#fmasReportKindHint').textContent =
            t(`reportKindHint_${choice.id}`, choice.hint);
        wrap.querySelector('#fmasReportDirectionWrap').hidden =
            choice.id !== 'term_keyness' && choice.id !== 'comprehensive';
    };
    kindSelect.addEventListener('change', syncHint);
    syncHint();

    wrap.addEventListener('click', (event) => {
        if (event.target.closest('[data-fmas-report-dismiss]')) closeDialog();
    });
    wrap.querySelector('#fmasReportForm').addEventListener('submit', (event) => {
        event.preventDefault();
        submitReport().catch((error) => {
            const status = wrap.querySelector('#fmasReportStatus');
            status.textContent = error.message || String(error);
            status.classList.add('is-error');
            window.showError?.(error.message || String(error));
            wrap.querySelector('#fmasReportSubmit').disabled = false;
        });
    });
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && dialogEl && !dialogEl.hidden) closeDialog();
    });

    dialogEl = wrap;
    return dialogEl;
}

function closeDialog() {
    if (!dialogEl) return;
    dialogEl.hidden = true;
    pendingPathIds = null;
    const submit = dialogEl.querySelector('#fmasReportSubmit');
    if (submit) submit.disabled = false;
}

async function openDialog(pathIds) {
    const dialog = ensureDialog();
    pendingPathIds = pathIds.slice();
    const lead = dialog.querySelector('#fmasReportDialogLead');
    lead.textContent = t('reportOnSelection', 'Report on %(count)s selected file(s)')
        .replace('%(count)s', String(pathIds.length));

    const languages = await loadLanguages();
    const langSelect = dialog.querySelector('#fmasReportLanguage');
    const previous = langSelect.value || 'en';
    langSelect.replaceChildren();
    languages.forEach((code) => {
        const opt = document.createElement('option');
        opt.value = code;
        opt.textContent = LANGUAGE_LABELS[code] || code;
        langSelect.appendChild(opt);
    });
    langSelect.value = languages.includes(previous) ? previous : (languages.includes('en') ? 'en' : languages[0]);

    const status = dialog.querySelector('#fmasReportStatus');
    status.textContent = '';
    status.classList.remove('is-error');
    dialog.hidden = false;
    dialog.querySelector('#fmasReportKind')?.focus();
}

async function submitReport() {
    const dialog = ensureDialog();
    const pathIds = pendingPathIds || [];
    if (!pathIds.length) {
        throw new Error(t('pleaseSelectFilesToExport', 'Please select files first'));
    }
    // The server refuses more than MAX_REPORT_FILES ids in one criteria set
    // (core/criteria/model.py MAX_IDS_PER_DIMENSION). Say so before sending.
    if (pathIds.length > MAX_REPORT_FILES) {
        throw new Error(t('tooManyFilesForReport',
            `Select at most ${MAX_REPORT_FILES} files for one report.`));
    }

    const kind = dialog.querySelector('#fmasReportKind').value;
    const language = dialog.querySelector('#fmasReportLanguage').value || 'en';
    const deliveryId = dialog.querySelector('#fmasReportDelivery').value;
    const delivery = DELIVERY.find((d) => d.id === deliveryId) || DELIVERY[0];
    const direction = dialog.querySelector('#fmasReportDirection').value || 'over';

    const status = dialog.querySelector('#fmasReportStatus');
    status.classList.remove('is-error');
    status.textContent = t('reportRunning', 'Building report…');
    const submit = dialog.querySelector('#fmasReportSubmit');
    submit.disabled = true;

    const choice = REPORT_CHOICES.find((c) => c.id === kind);
    if (choice && choice.live && (delivery.format === 'html')) {
        const params = new URLSearchParams();
        params.set('ids', pathIds.join(','));
        if (language) params.set('lang', language);
        if (delivery.print) params.set('print', '1');
        const url = `/reports/entries/print?${params.toString()}`;
        if (delivery.print) {
            const frame = dialog.querySelector('#fmasReportPrintFrame');
            frame.hidden = false;
            frame.onload = () => {
                try {
                    frame.contentWindow.focus();
                    frame.contentWindow.print();
                } catch (_) { /* the page also auto-prints when ?print=1 */ }
            };
            frame.src = url;
        } else {
            window.open(url, '_blank', 'noopener');
        }
        status.textContent = t('reportReady', 'Report ready.');
        window.showSuccess?.(t('reportReady', 'Report ready.'));
        submit.disabled = false;
        setTimeout(() => closeDialog(), 600);
        return;
    }

    const parameters = {
        criteria: {
            path_ids: pathIds,
            analyst_scope: 'all',
            unit: kind === 'comprehensive' || kind === 'term_keyness' ? 'hash' : 'path',
        },
    };
    if (kind === 'comprehensive' || kind === 'term_keyness') {
        parameters.direction = direction;
    }

    const created = await api('POST', '/api/reports/runs', {
        report_id: kind,
        parameters,
    });
    let job = created.job;
    if (job && !FINISHED.has(job.status)) {
        job = await followJob(job, status);
    }
    if (job && job.status === 'FAILED') {
        throw new Error(t('reportFailed', 'Report generation failed'));
    }

    const run = created.run || (await api('GET',
        `/api/reports/runs/${encodeURIComponent(created.run?.id)}`)).run;
    const runId = run?.id || created.run?.id;
    if (!runId) throw new Error(t('reportFailed', 'Report generation failed'));

    // Wait until the run itself is completed (dataset materialisation).
    let runState = run;
    for (let i = 0; i < JOB_POLL_LIMIT; i += 1) {
        if (['completed', 'failed', 'refused', 'cancelled'].includes(runState.status)) break;
        status.textContent = t('reportRunning', 'Building report…') +
            ` (${runState.status || '…'})`;
        await new Promise((resolve) => setTimeout(resolve, JOB_POLL_MS));
        const body = await api('GET', `/api/reports/runs/${encodeURIComponent(runId)}`);
        runState = body.run;
    }
    if (runState.status !== 'completed') {
        throw new Error(
            (runState.error_message || t('reportFailed', 'Report generation failed'))
            + (runState.status ? ` (${runState.status})` : '')
        );
    }

    status.textContent = t('reportRendering', 'Preparing readable output…');
    const artifactPayload = {
        format: delivery.format,
        language,
    };
    if (delivery.format === 'csv') {
        const datasets = Array.isArray(runState.datasets) ? runState.datasets : [];
        const preferred = datasets.find((d) => /matches|overview|ranked/i.test(d.dataset_key || ''))
            || datasets[0];
        if (!preferred || !preferred.dataset_key) {
            throw new Error(t('reportArtifactMissing', 'No dataset is available for CSV export.'));
        }
        artifactPayload.dataset_key = preferred.dataset_key;
    }
    const artifactBody = await api('POST', `/api/reports/runs/${encodeURIComponent(runId)}/artifacts`,
        artifactPayload);
    if (artifactBody.job && !FINISHED.has(artifactBody.job.status)) {
        await followJob(artifactBody.job, status);
    }

    const listed = await api('GET', `/api/reports/runs/${encodeURIComponent(runId)}/artifacts`);
    const match = (listed.items || []).find((f) => {
        if (f.format !== delivery.format) return false;
        if ((f.language || 'en') !== language) return false;
        if (delivery.format === 'csv') {
            return f.dataset_key === artifactPayload.dataset_key;
        }
        if (delivery.format === 'html') return !f.dataset_key;
        return !f.dataset_key || f.dataset_key == null;
    });
    if (!match) {
        throw new Error(t('reportArtifactMissing', 'Readable output is not available for this run.'));
    }

    const url = `/api/reports/artifacts/${encodeURIComponent(match.id)}/download`;
    if (delivery.print) {
        const frame = dialog.querySelector('#fmasReportPrintFrame');
        frame.hidden = false;
        frame.onload = () => {
            try {
                frame.contentWindow.focus();
                frame.contentWindow.print();
            } catch (_) { /* noop */ }
        };
        frame.src = url;
    } else if (delivery.format === 'html') {
        window.open(url, '_blank', 'noopener');
    } else {
        window.location.href = url;
    }

    status.textContent = t('reportReady', 'Report ready.');
    window.showSuccess?.(t('reportReady', 'Report ready.'));
    submit.disabled = false;
    // Keep the dialog open briefly so status is readable, then close.
    setTimeout(() => closeDialog(), 600);
}

/**
 * Open the analytical-report dialog for the current File Manager selection.
 */
export async function openAnalyticalReportForSelection() {
    const ids = collectSelectedIds();
    if (!ids.length) {
        window.showWarning?.(
            t('pleaseSelectFilesToExport', 'Please select files to include in the report')
        );
        return;
    }
    await openDialog(ids);
}

/**
 * Open the analytical-report dialog for a single file (card action).
 */
export async function openAnalyticalReportForFile(fileId) {
    const id = parseInt(fileId, 10);
    if (!Number.isInteger(id) || id <= 0) {
        window.showWarning?.(t('invalidFile', 'Invalid file'));
        return;
    }
    await openDialog([id]);
}
