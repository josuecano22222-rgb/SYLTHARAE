/**
 * Email Words Page JavaScript
 * Handles initialization and interactions for the email words page
 */

// Global state for sorting
let currentSortBy = 'word';
let currentSortOrder = 'asc';
let emailFilesRequestController = null;

function initializeEmailWordsPage() {
    // Load filters from JSON embedded in the page (template-safe)
    const filtersDataEl = document.getElementById('emailWordsFiltersData');
    if (filtersDataEl && filtersDataEl.textContent) {
        try {
            const pageData = JSON.parse(filtersDataEl.textContent);
            window.emailWordsFilters = pageData.filters || {};
            window.emailWordsPageTranslations = pageData.translations || {};
        } catch (e) {
            window.emailWordsFilters = window.emailWordsFilters || {};
            window.emailWordsPageTranslations = window.emailWordsPageTranslations || {};
        }
    } else {
        window.emailWordsFilters = window.emailWordsFilters || {};
        window.emailWordsPageTranslations = window.emailWordsPageTranslations || {};
    }

    const emailTableBody = document.getElementById('emailTableBody');
    emailTableBody?.addEventListener('click', (event) => {
        const button = event.target.closest('[data-email-action]');
        if (!button || !emailTableBody.contains(button)) return;
        const email = button.dataset.email || '';
        switch (button.dataset.emailAction) {
            case 'show-files': showEmailFiles(email); break;
            case 'copy': copyEmail(email, button); break;
            case 'search': searchInFiles(email); break;
        }
    });

    document.getElementById('copyEmailWordsButton')?.addEventListener('click', (event) => {
        copyToClipboard(event.currentTarget);
    });
    const exportMenu = document.getElementById('emailWordsExportMenu');
    exportMenu?.addEventListener('click', (event) => {
        const columnsToggle = event.target.closest('[data-email-export-columns]');
        if (columnsToggle) {
            event.preventDefault();
            const checked = columnsToggle.getAttribute('data-email-export-columns') === 'all';
            exportMenu.querySelectorAll('.ut-export-column-check').forEach((box) => {
                box.checked = checked;
            });
            return;
        }
        const formatBtn = event.target.closest('[data-email-export-format]');
        if (!formatBtn) return;
        event.preventDefault();
        exportData(document.getElementById('exportEmailWordsButton'), formatBtn.getAttribute('data-email-export-format'));
    });
    document.getElementById('perPageSelect')?.addEventListener('change', (event) => {
        changePerPage(event.currentTarget.value);
    });
    document.getElementById('emailFilesModal')?.addEventListener('hidden.bs.modal', () => {
        emailFilesRequestController?.abort();
    });

    // Get current sort parameters from URL
    const urlParams = new URLSearchParams(window.location.search);
    const requestedSort = urlParams.get('sort_by');
    currentSortBy = ['word', 'usage_count'].includes(requestedSort) ? requestedSort : 'word';
    currentSortOrder = urlParams.get('sort_order') === 'desc' ? 'desc' : 'asc';
    updateSortIcons();

    // Wire domain dropdown -> domain input, then submit
    const domainSelect = document.getElementById('domainSelect');
    const domainInput = document.getElementById('domainInput');
    const filtersForm = document.getElementById('emailFiltersForm');

    if (domainSelect && domainInput && filtersForm) {
        domainSelect.addEventListener('change', function () {
            if (!this.value) return;
            domainInput.value = this.value;
            filtersForm.submit();
        });
    }

    // Click domain pill in table -> set domain and submit
    document.querySelectorAll('.domain-pill').forEach((el) => {
        el.addEventListener('click', function () {
            if (!domainInput || !filtersForm) return;
            const d = this.getAttribute('data-domain');
            if (!d) return;
            domainInput.value = d;
            filtersForm.submit();
        });
    });
    
    // Native buttons preserve keyboard and assistive-technology support for sorting.
    document.querySelectorAll('button.sortable[data-sort]').forEach((button) => {
        button.addEventListener('click', () => sortTable(button.dataset.sort));
    });

    // Update totals display immediately
    updateTotalsDisplay();
    updateBulkButtons();
}

function getEmailCheckboxes() {
    return document.querySelectorAll('.email-checkbox');
}

function getSelectedEmailCheckboxes() {
    return document.querySelectorAll('.email-checkbox:checked');
}

function emailSelectAll(master) {
    getEmailCheckboxes().forEach((cb) => {
        cb.checked = master.checked;
    });
    updateBulkButtons();
}

function selectAll() {
    getEmailCheckboxes().forEach((cb) => {
        cb.checked = true;
    });
    const header = document.getElementById('emailSelectAllCheckbox');
    if (header) header.checked = true;
    updateBulkButtons();
}

function selectNone() {
    getEmailCheckboxes().forEach((cb) => {
        cb.checked = false;
    });
    const header = document.getElementById('emailSelectAllCheckbox');
    if (header) header.checked = false;
    updateBulkButtons();
}

/** One control: select every visible row when empty, clear when any are selected. */
function toggleAllSelection() {
    if (getSelectedEmailCheckboxes().length > 0) {
        selectNone();
    } else {
        selectAll();
    }
}

function updateBulkButtons() {
    if (!window.ActionToolbar) return;
    const total = getEmailCheckboxes().length;
    const selected = getSelectedEmailCheckboxes().length;
    window.ActionToolbar.sync('emailWordsActionBar', { selected, total });
    const header = document.getElementById('emailSelectAllCheckbox');
    if (header) {
        header.checked = total > 0 && selected === total;
        header.indeterminate = selected > 0 && selected < total;
    }
}

function selectedEmailRows() {
    return Array.from(getSelectedEmailCheckboxes()).map((cb) => {
        const row = cb.closest('tr');
        return {
            email: cb.value || row?.dataset?.word || '',
            usage_count: Number(row?.dataset?.usageCount) || 0,
        };
    }).filter((row) => row.email);
}

async function exportSelectedEmails() {
    const rows = selectedEmailRows();
    const translations = window.emailWordsPageTranslations || {};
    if (!rows.length) {
        showToast(translations.selectEmailsFirst || 'Please select emails to export', 'warning');
        return;
    }
    const menu = document.getElementById('emailWordsExportMenu');
    const columns = Array.from(menu?.querySelectorAll('.ut-export-column-check:checked') || [])
        .map((box) => box.getAttribute('data-export-column'))
        .filter(Boolean);
    if (!columns.length) {
        showToast(translations.selectColumns || 'Please select at least one column to export.', 'warning');
        return;
    }
    const headers = [];
    if (columns.includes('email')) headers.push(translations.emailAddress || 'Email Address');
    if (columns.includes('usage_count')) headers.push(translations.usageCount || 'Usage Count');
    const csvRows = [
        headers.map(csvCell).join(','),
        ...rows.map((item) => {
            const cells = [];
            if (columns.includes('email')) cells.push(item.email);
            if (columns.includes('usage_count')) cells.push(item.usage_count);
            return cells.map(csvCell).join(',');
        }),
    ];
    const blob = new Blob([`\uFEFF${csvRows.join('\r\n')}`], { type: 'text/csv;charset=utf-8' });
    const objectUrl = window.URL.createObjectURL(blob);
    const downloadLink = document.createElement('a');
    downloadLink.href = objectUrl;
    downloadLink.download = `email_words_selected_${new Date().toISOString().slice(0, 10)}.csv`;
    downloadLink.hidden = true;
    document.body.appendChild(downloadLink);
    downloadLink.click();
    downloadLink.remove();
    window.setTimeout(() => window.URL.revokeObjectURL(objectUrl), 1000);
    showToast(translations.exportDone || 'Export completed successfully', 'success');
}

async function copySelectedEmails() {
    const rows = selectedEmailRows();
    const translations = window.emailWordsPageTranslations || {};
    if (!rows.length) {
        showToast(translations.selectEmailsFirst || 'Please select emails to copy', 'warning');
        return;
    }
    try {
        if (!navigator.clipboard?.writeText) {
            throw new Error('Clipboard access is unavailable');
        }
        await navigator.clipboard.writeText(rows.map((row) => row.email).join('\n'));
        showToast(translations.copied || 'Content copied to clipboard!', 'success');
    } catch (error) {
        console.error('Error copying selected emails:', error);
        showToast(translations.copyFailed || 'Failed to copy content to clipboard', 'error');
    }
}

// Update totals display
function updateTotalsDisplay() {
    const showingCount = document.getElementById('showingCount');
    const totalCount = document.getElementById('totalCount');
    const emailRows = document.querySelectorAll('.email-row');
    
    if (showingCount && emailRows.length > 0) {
        showingCount.textContent = emailRows.length;
    }
}

// Sort table by column
function sortTable(column) {
    if (!['word', 'usage_count'].includes(column)) return;
    if (currentSortBy === column) {
        // Toggle sort order
        currentSortOrder = currentSortOrder === 'asc' ? 'desc' : 'asc';
    } else {
        // New column, default to ascending
        currentSortBy = column;
        currentSortOrder = 'asc';
    }
    
    // Reload page with new sort parameters
    const url = new URL(window.location.href);
    url.searchParams.set('sort_by', currentSortBy);
    url.searchParams.set('sort_order', currentSortOrder);
    url.searchParams.set('page', '1'); // Reset to first page
    if (window.swapNavigate) { window.swapNavigate(url.toString()); } else { window.location.href = url.toString(); }
}

// Update sort icons
function updateSortIcons() {
    document.querySelectorAll('button.sortable[data-sort]').forEach((button) => {
        const column = button.dataset.sort;
        const icon = button.querySelector('.sort-icon');
        const header = button.closest('th');
        const isCurrentSort = currentSortBy === column;
        if (header) {
            header.setAttribute('aria-sort', isCurrentSort
                ? (currentSortOrder === 'asc' ? 'ascending' : 'descending')
                : 'none');
        }
        if (icon) {
            icon.className = isCurrentSort
                ? `bi bi-arrow-${currentSortOrder === 'asc' ? 'up' : 'down'} ms-1 sort-icon text-primary`
                : 'bi bi-arrow-down-up ms-1 sort-icon text-muted';
        }
    });
}

// Change per page
function changePerPage(value) {
    const allowedPageSizes = new Set(['25', '50', '100', '200']);
    if (!allowedPageSizes.has(String(value))) return;
    const url = new URL(window.location.href);
    url.searchParams.set('per_page', String(value));
    url.searchParams.set('page', '1'); // Reset to first page
    if (window.swapNavigate) { window.swapNavigate(url.toString()); } else { window.location.href = url.toString(); }
}

// Note:
// - The page now uses server-side filtering via GET params (q/domain/domain_mode).
// - We intentionally do NOT do client-side row hiding on input, so counts/pagination stay correct.

function clearSearch() {
    const searchInput = document.getElementById('searchInput');
    if (!searchInput) return;
    searchInput.value = '';
    searchInput.form?.requestSubmit();
}

async function copyEmail(email, btn = null) {
    try {
        await navigator.clipboard.writeText(email);
        if (!btn) return;
        const originalHTML = btn.innerHTML;
        btn.innerHTML = '<i class="bi bi-check" aria-hidden="true"></i>';
        btn.classList.remove('btn-outline-primary');
        btn.classList.add('btn-success');
        btn.setAttribute('aria-label', window.emailWordsPageTranslations?.copied || 'Content copied to clipboard!');
        setTimeout(() => {
            btn.innerHTML = originalHTML;
            btn.classList.remove('btn-success');
            btn.classList.add('btn-outline-primary');
            btn.setAttribute('aria-label', btn.title || 'Copy');
        }, 1000);
    } catch (error) {
        console.error('Could not copy email address:', error);
        const message = window.emailWordsPageTranslations?.copyFailed || 'Failed to copy content to clipboard';
        showToast(message, 'error');
    }
}

async function copyToClipboard(button = null) {
    const targetButton = button?.closest?.('button') || document.getElementById('copyEmailWordsButton');
    if (!targetButton || targetButton.disabled) return;
    const originalChildren = Array.from(targetButton.childNodes, node => node.cloneNode(true));
    const translations = window.emailWordsPageTranslations || {};
    targetButton.disabled = true;
    targetButton.setAttribute('aria-busy', 'true');
    setButtonState(targetButton, 'bi-hourglass-split', translations.loading || 'Loading...');

    try {
        const params = new URLSearchParams();
        const filters = window.emailWordsFilters || {};
        Object.entries(filters).forEach(([key, value]) => {
            if (value !== null && value !== undefined && String(value).trim() !== '') {
                params.set(key, value);
            }
        });
        const response = await fetch(`/api/email-words/all?${params.toString()}`);
        const data = await response.json();
        if (!response.ok || !data.success || !Array.isArray(data.emails)) {
            throw new Error(data.error || 'Email export request failed');
        }
        if (!navigator.clipboard?.writeText) {
            throw new Error('Clipboard access is unavailable');
        }

        const emails = data.emails
            .map(item => item && item.email != null ? String(item.email) : '')
            .filter(Boolean)
            .join('\n');
        await navigator.clipboard.writeText(emails);
        setButtonState(targetButton, 'bi-check-circle');
        showToast(translations.copied || 'Content copied to clipboard!', 'success');
    } catch (error) {
        console.error('Error copying email addresses:', error);
        setButtonState(targetButton, 'bi-exclamation-circle');
        showToast(translations.copyFailed || 'Failed to copy content to clipboard', 'error');
    } finally {
        window.setTimeout(() => {
            if (!targetButton.isConnected) return;
            targetButton.replaceChildren(...originalChildren);
            targetButton.disabled = false;
            targetButton.removeAttribute('aria-busy');
        }, 1200);
    }
}

function csvCell(value) {
    let text = String(value == null ? '' : value);
    // Prefix spreadsheet formulas with an apostrophe before RFC-style quoting.
    if (/^[\s\u0000-\u001F]*[=+\-@]/.test(text)) text = `'${text}`;
    return `"${text.replace(/"/g, '""')}"`;
}

async function exportData(button = null, format = 'csv') {
    const targetButton = button?.closest?.('button') || document.getElementById('exportEmailWordsButton');
    if (!targetButton || targetButton.disabled) return;
    const menu = document.getElementById('emailWordsExportMenu');
    const columns = Array.from(menu?.querySelectorAll('.ut-export-column-check:checked') || [])
        .map((box) => box.getAttribute('data-export-column'))
        .filter(Boolean);
    if (!columns.length) {
        showToast(
            window.emailWordsPageTranslations?.selectColumns
                || 'Please select at least one column to export.',
            'warning');
        return;
    }
    const scope = menu?.querySelector('.ut-export-scope-check:checked')?.value || 'filtered';
    const originalChildren = Array.from(targetButton.childNodes, node => node.cloneNode(true));
    const translations = window.emailWordsPageTranslations || {};
    targetButton.disabled = true;
    targetButton.setAttribute('aria-busy', 'true');
    setButtonState(targetButton, 'bi-hourglass-split', translations.loading || 'Loading...');

    try {
        // Filtered = current screen filters. All = every email word in the database.
        const params = new URLSearchParams();
        if (scope !== 'all') {
            const filters = window.emailWordsFilters || {};
            Object.entries(filters).forEach(([key, value]) => {
                if (value !== null && value !== undefined && String(value).trim() !== '') {
                    params.set(key, value);
                }
            });
        }
        const response = await fetch(`/api/email-words/all?${params.toString()}`);
        const data = await response.json();
        if (!response.ok || !data.success || !Array.isArray(data.emails)) {
            throw new Error(data.error || 'Email export request failed');
        }

        const headers = [];
        if (columns.includes('email')) headers.push(translations.emailAddress || 'Email Address');
        if (columns.includes('usage_count')) headers.push(translations.usageCount || 'Usage Count');

        const csvRows = [
            headers.map(csvCell).join(','),
            ...data.emails.map((item) => {
                const cells = [];
                if (columns.includes('email')) cells.push(item?.email);
                if (columns.includes('usage_count')) cells.push(Number(item?.usage_count) || 0);
                return cells.map(csvCell).join(',');
            })
        ];
        const blob = new Blob([`\uFEFF${csvRows.join('\r\n')}`], { type: 'text/csv;charset=utf-8' });
        const objectUrl = window.URL.createObjectURL(blob);
        const downloadLink = document.createElement('a');
        downloadLink.href = objectUrl;
        const scopeTag = scope === 'all' ? 'all' : 'filtered';
        downloadLink.download = `email_words_${scopeTag}_${new Date().toISOString().slice(0, 10)}.csv`;
        downloadLink.hidden = true;
        document.body.appendChild(downloadLink);
        downloadLink.click();
        downloadLink.remove();
        window.setTimeout(() => window.URL.revokeObjectURL(objectUrl), 1000);

        setButtonState(targetButton, 'bi-check-circle');
        showToast(translations.exportDone || 'Export completed successfully', 'success');
    } catch (error) {
        console.error('Error exporting email addresses:', error);
        setButtonState(targetButton, 'bi-exclamation-circle');
        showToast(translations.exportFailed || 'Error exporting data', 'error');
    } finally {
        window.setTimeout(() => {
            if (!targetButton.isConnected) return;
            targetButton.replaceChildren(...originalChildren);
            targetButton.disabled = false;
            targetButton.removeAttribute('aria-busy');
        }, 1200);
    }
}

function setButtonState(button, iconClass, label = '') {
    const icon = document.createElement('i');
    icon.className = `bi ${iconClass}`;
    icon.setAttribute('aria-hidden', 'true');
    if (label) {
        const text = document.createTextNode(` ${label}`);
        button.replaceChildren(icon, text);
    } else {
        button.replaceChildren(icon);
    }
}

function searchInFiles(email) {
    // Redirect to advanced search with email pre-filled
    if (window.swapNavigate) { window.swapNavigate(`/search/advanced?q=${encodeURIComponent(email)}`); } else { window.location.href = `/search/advanced?q=${encodeURIComponent(email)}`; }
}

// Show files containing an email address in a modal
async function showEmailFiles(email) {
    const modalElement = document.getElementById('emailFilesModal');
    const modalBody = document.getElementById('emailFilesModalBody');
    const modalTitle = document.getElementById('modalEmailAddress');
    const modalFileCount = document.getElementById('modalFileCount');
    if (!modalElement || !modalBody || !modalTitle || !modalFileCount) return;

    // Reuse the Bootstrap modal instance when available.
    let modal;
    if (typeof bootstrap !== 'undefined' && bootstrap.Modal) {
        modal = typeof bootstrap.Modal.getOrCreateInstance === 'function'
            ? bootstrap.Modal.getOrCreateInstance(modalElement)
            : new bootstrap.Modal(modalElement);
    } else if (typeof $ !== 'undefined' && $.fn.modal) {
        modal = $(modalElement);
    } else {
        modalElement.style.display = 'block';
        modalElement.classList.add('show');
        document.body.classList.add('modal-open');
    }

    // Email is untrusted content; use a text node, not HTML.
    modalTitle.textContent = email;
    modalFileCount.textContent = '0';

    emailFilesRequestController?.abort();
    const requestController = new AbortController();
    emailFilesRequestController = requestController;

    // Show loading state
    const translations = window.emailWordsPageTranslations || {};
    modalBody.innerHTML = `
        <div class="text-center py-4">
            <div class="spinner-border text-primary" role="status">
                <span class="visually-hidden">${escapeHtml(translations.loading || 'Loading...')}</span>
            </div>
            <p class="mt-2">${escapeHtml(translations.loadingFiles || 'Loading files...')}</p>
        </div>
    `;
    
    // Show modal
    if (modal && typeof modal.show === 'function') {
        modal.show();
    } else if (modal && typeof modal.modal === 'function') {
        modal.modal('show');
    } else {
        // Manual show
        modalElement.style.display = 'block';
        modalElement.classList.add('show');
        document.body.classList.add('modal-open');
    }
    
    try {
        // Fetch files containing this email
        const response = await fetch(`/api/email-words/files?email=${encodeURIComponent(email)}&limit=500`, {
            signal: requestController.signal
        });
        const data = await response.json();
        if (requestController.signal.aborted) return;

        if (!response.ok || !data.success) {
            throw new Error(data.error || translations.loadingError || 'Error loading files');
        }
        const files = Array.isArray(data.files) ? data.files.filter((file) => {
            const id = Number(file?.id);
            return Number.isSafeInteger(id) && id > 0;
        }) : [];

        // The API total can exceed its 500-item display limit.
        const total = Number(data.total);
        modalFileCount.textContent = Number.isSafeInteger(total) && total >= 0 ? total : files.length;

        if (files.length > 0) {
            renderEmailFiles(files, email);
        } else {
            modalBody.innerHTML = `
                <div class="text-center py-5">
                    <i class="bi bi-inbox display-4 text-muted d-block mb-3" aria-hidden="true"></i>
                    <h5>${escapeHtml(translations.noFilesFound || 'No Files Found')}</h5>
                </div>
            `;
        }
    } catch (error) {
        if (error.name === 'AbortError') return;
        console.error('Error loading email files:', error);
        modalBody.innerHTML = `
            <div class="alert alert-danger">
                <i class="bi bi-exclamation-triangle me-2" aria-hidden="true"></i>
                <strong>${escapeHtml(translations.loadingError || 'Error loading files')}:</strong>
                ${escapeHtml(error.message || translations.unknownError || 'Unknown error')}
            </div>
        `;
    } finally {
        if (emailFilesRequestController === requestController) {
            emailFilesRequestController = null;
        }
    }
}

// Render files list in modal. All untrusted file values are encoded for HTML,
// and the file ID is coerced to a positive integer before it becomes a URL.
function renderEmailFiles(files, email) {
    const modalBody = document.getElementById('emailFilesModalBody');
    if (!modalBody) return;
    const translations = window.emailWordsPageTranslations || {};
    const rows = [];

    (Array.isArray(files) ? files : []).forEach((file) => {
        const fileId = Number(file?.id);
        if (!Number.isSafeInteger(fileId) || fileId < 1) return;
        const unknownLabel = translations.unknown || 'Unknown';
        const notAvailableLabel = translations.notAvailable || 'N/A';
        const fileType = String(file.type || unknownLabel);
        const fileName = String(file.name || unknownLabel);
        const rawFileSize = Number(file.size);
        const fileSize = formatFileSize(Number.isFinite(rawFileSize) ? Math.max(0, rawFileSize) : 0);
        const fileDate = file.date ? new Date(file.date).toLocaleDateString() : notAvailableLabel;
        const rawWordCount = Number(file.word_count);
        const wordCount = Number.isSafeInteger(rawWordCount) && rawWordCount > 0 ? rawWordCount : 0;
        const fileStatus = String(file.status || '').toLowerCase();
        const statusBadge = fileStatus === 'read'
            ? `<span class="badge bg-success">${escapeHtml(translations.read || 'Read')}</span>`
            : fileStatus === 'unread'
                ? `<span class="badge bg-secondary">${escapeHtml(translations.unread || 'Unread')}</span>`
                : `<span class="badge bg-light text-muted">${escapeHtml(unknownLabel)}</span>`;

        rows.push(`
            <a class="list-group-item list-group-item-action"
               href="/file/${fileId}" target="_blank" rel="noopener noreferrer">
                <div class="d-flex w-100 justify-content-between align-items-start">
                    <div class="flex-grow-1">
                        <div class="d-flex align-items-center mb-2">
                            ${getFileIcon(fileType)}
                            <h6 class="mb-0 ms-2">${escapeHtml(fileName)}</h6>
                            ${statusBadge}
                        </div>
                        <div class="small text-muted mb-1">
                            <i class="bi bi-folder" aria-hidden="true"></i> ${escapeHtml(file.path || notAvailableLabel)}
                        </div>
                        <div class="small">
                            <span class="badge bg-info me-2">
                                <i class="bi bi-file-earmark" aria-hidden="true"></i> ${escapeHtml(fileType.toUpperCase())}
                            </span>
                            <span class="badge bg-secondary me-2">
                                <i class="bi bi-hdd" aria-hidden="true"></i> ${escapeHtml(fileSize)}
                            </span>
                            <span class="badge bg-secondary me-2">
                                <i class="bi bi-calendar" aria-hidden="true"></i> ${escapeHtml(fileDate)}
                            </span>
                            ${wordCount > 0 ? `<span class="badge bg-primary">
                                <i class="bi bi-envelope" aria-hidden="true"></i> ${escapeHtml(translations.usageCount || 'Usage Count')}: ${wordCount}
                            </span>` : ''}
                        </div>
                        <div class="small mt-2 d-flex flex-wrap gap-2">
                            <span class="badge text-bg-light border">
                                <i class="bi bi-building me-1" aria-hidden="true"></i>
                                ${escapeHtml(translations.sourceLabel || 'Source')}:
                                <strong class="ms-1">${escapeHtml(file.source || notAvailableLabel)}</strong>
                            </span>
                            <span class="badge text-bg-light border">
                                <i class="bi bi-diagram-3 me-1" aria-hidden="true"></i>
                                ${escapeHtml(translations.sideLabel || 'Side')}:
                                <strong class="ms-1">${escapeHtml(file.side || notAvailableLabel)}</strong>
                            </span>
                        </div>
                    </div>
                    <span class="ms-3 text-primary" aria-hidden="true">
                        <i class="bi bi-box-arrow-up-right"></i>
                    </span>
                </div>
            </a>
        `);
    });

    modalBody.innerHTML = `
        <div class="mb-3 p-3 bg-light rounded">
            <div class="d-flex justify-content-between align-items-center">
                <div>
                    <strong>${rows.length}</strong> ${escapeHtml(translations.filesFound || 'files found')}
                </div>
            </div>
        </div>
        <div class="list-group">${rows.join('')}</div>
    `;
}

// Helper functions
function getFileIcon(fileType) {
    const icons = {
        'pdf': 'bi-file-pdf text-danger',
        'doc': 'bi-file-word text-primary',
        'docx': 'bi-file-word text-primary',
        'xls': 'bi-file-excel text-success',
        'xlsx': 'bi-file-excel text-success',
        'txt': 'bi-file-text',
        'eml': 'bi-envelope text-info',
        'msg': 'bi-envelope text-info',
        'html': 'bi-file-code text-warning',
        'htm': 'bi-file-code text-warning'
    };
    const icon = icons[fileType?.toLowerCase()] || 'bi-file-earmark';
    return `<i class="bi ${icon} fs-4"></i>`;
}

function formatFileSize(bytes) {
    const size = Number(bytes);
    if (!Number.isFinite(size) || size <= 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
    const unitIndex = Math.min(Math.floor(Math.log(size) / Math.log(1024)), units.length - 1);
    const value = Math.round(size / Math.pow(1024, unitIndex) * 100) / 100;
    return `${value} ${units[unitIndex]}`;
}

function escapeHtml(text) {
    if (text === null || text === undefined) return '';
    const div = document.createElement('div');
    div.textContent = String(text);
    return div.innerHTML;
}

// Toast notification helper: build nodes explicitly so messages stay text-only.
function showToast(message, type = 'info') {
    const toastContainer = (document.querySelector('[data-toast-region]') || document.getElementById('toastContainer')) || createToastContainer();
    const isError = type === 'error';
    const variant = isError ? 'danger' : (type === 'success' ? 'success' : 'info');
    const toast = document.createElement('div');
    toast.className = `alert alert-${variant} alert-dismissible fade show shadow`;
    toast.setAttribute('role', isError ? 'alert' : 'status');
    toast.setAttribute('aria-live', isError ? 'assertive' : 'polite');

    const icon = document.createElement('i');
    icon.className = `bi ${isError ? 'bi-exclamation-triangle' : (type === 'success' ? 'bi-check-circle' : 'bi-info-circle')} me-2`;
    icon.setAttribute('aria-hidden', 'true');
    const text = document.createElement('span');
    text.textContent = String(message ?? '');
    const closeButton = document.createElement('button');
    closeButton.type = 'button';
    closeButton.className = 'btn-close';
    closeButton.setAttribute('aria-label', window.emailWordsPageTranslations?.close || 'Close');
    closeButton.addEventListener('click', () => toast.remove());
    toast.append(icon, text, closeButton);
    toastContainer.appendChild(toast);
    window.setTimeout(() => toast.remove(), 5000);
}

function createToastContainer() {
    const container = document.createElement('div');
    container.id = 'toastContainer';
    container.className = 'toast-container position-fixed top-0 end-0 p-3';
    container.style.zIndex = '1090';
    document.body.appendChild(container);
    return container;
}

// The clear control in the search-group component uses this page-local hook.
window.clearSearch = clearSearch;
window.selectAll = selectAll;
window.selectNone = selectNone;
window.toggleAllSelection = toggleAllSelection;
window.emailSelectAll = emailSelectAll;
window.updateBulkButtons = updateBulkButtons;
window.exportSelectedEmails = exportSelectedEmails;
window.copySelectedEmails = copySelectedEmails;

// Export default initialization function for universal-initializer
export default function init() {
    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initializeEmailWordsPage);
    } else {
        initializeEmailWordsPage();
    }
}