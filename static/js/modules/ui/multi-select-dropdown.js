/**
 * Compact multi-select dropdown.
 *
 * Turns a native ``<select multiple>`` into a single-line trigger that opens a
 * panel of checkboxes with optional icons/markers — without the tall list that
 * breaks toolbar layouts. The underlying select stays in the DOM (form names,
 * existing JS that reads selectedOptions keep working).
 */

const ENHANCED = new WeakMap();

const FILE_TYPE_ICONS = {
    pdf: 'bi-filetype-pdf',
    doc: 'bi-filetype-doc',
    docx: 'bi-filetype-docx',
    xls: 'bi-filetype-xls',
    xlsx: 'bi-filetype-xlsx',
    ppt: 'bi-filetype-ppt',
    pptx: 'bi-filetype-pptx',
    txt: 'bi-filetype-txt',
    csv: 'bi-filetype-csv',
    json: 'bi-filetype-json',
    xml: 'bi-filetype-xml',
    html: 'bi-filetype-html',
    htm: 'bi-filetype-html',
    md: 'bi-markdown',
    zip: 'bi-file-earmark-zip',
    eml: 'bi-envelope',
    jpg: 'bi-filetype-jpg',
    jpeg: 'bi-filetype-jpg',
    png: 'bi-filetype-png',
    gif: 'bi-filetype-gif',
    svg: 'bi-filetype-svg',
    unknown: 'bi-file-earmark',
};

function escapeHtml(value) {
    return String(value == null ? '' : value)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function t(key, fallback) {
    return (window.translations && window.translations[key]) || fallback;
}

/** Bootstrap icon class for a file-type option value. */
export function iconForFileType(value) {
    const key = String(value || '').trim().replace(/^\./, '').toLowerCase();
    return FILE_TYPE_ICONS[key] || 'bi-file-earmark';
}

function defaultSummary(select, placeholder) {
    const selected = Array.from(select.selectedOptions).filter((o) => o.value);
    if (!selected.length) return placeholder;
    if (selected.length === 1) return selected[0].textContent.trim() || selected[0].value;
    if (selected.length === 2) {
        return selected.map((o) => o.textContent.trim() || o.value).join(', ');
    }
    const first = selected[0].textContent.trim() || selected[0].value;
    return `${first} +${selected.length - 1}`;
}

function closeAll(except) {
    document.querySelectorAll('.msdd.is-open').forEach((wrap) => {
        if (wrap !== except) {
            wrap.classList.remove('is-open');
            const btn = wrap.querySelector('.msdd-trigger');
            if (btn) btn.setAttribute('aria-expanded', 'false');
        }
    });
}

function syncFromSelect(state) {
    const { select, list, trigger, placeholder, summaryFn } = state;
    list.querySelectorAll('.msdd-option').forEach((row) => {
        const value = row.getAttribute('data-value');
        const opt = Array.from(select.options).find((o) => o.value === value);
        const box = row.querySelector('input[type="checkbox"]');
        if (box) box.checked = !!(opt && opt.selected);
    });
    const count = Array.from(select.selectedOptions).filter((o) => o.value).length;
    const label = trigger.querySelector('.msdd-trigger-label');
    if (label) label.textContent = summaryFn(select, placeholder);
    trigger.classList.toggle('has-selection', count > 0);
    const badge = trigger.querySelector('.msdd-count');
    if (badge) {
        badge.textContent = String(count);
        badge.hidden = count === 0;
    }
}

function rebuildList(state) {
    const { select, list, iconForOption, markerForOption } = state;
    list.replaceChildren();
    Array.from(select.options).forEach((opt) => {
        if (!opt.value && !state.includeEmpty) return;
        const row = document.createElement('label');
        row.className = 'msdd-option';
        row.setAttribute('data-value', opt.value);
        row.setAttribute('data-label', (opt.textContent || opt.value).trim().toLowerCase());

        const check = document.createElement('input');
        check.type = 'checkbox';
        check.checked = opt.selected;
        check.addEventListener('change', () => {
            opt.selected = check.checked;
            select.dispatchEvent(new Event('change', { bubbles: true }));
            syncFromSelect(state);
            if (typeof state.onChange === 'function') state.onChange(select);
        });

        const marker = markerForOption ? markerForOption(opt.value, opt.textContent) : null;
        let markerEl = null;
        if (marker) {
            markerEl = document.createElement('span');
            markerEl.className = 'msdd-marker';
            markerEl.style.background = marker;
            markerEl.setAttribute('aria-hidden', 'true');
        }

        const iconClass = iconForOption ? iconForOption(opt.value, opt.textContent) : null;
        let iconEl = null;
        if (iconClass) {
            iconEl = document.createElement('i');
            iconEl.className = `bi ${iconClass} msdd-icon`;
            iconEl.setAttribute('aria-hidden', 'true');
        }

        const text = document.createElement('span');
        text.className = 'msdd-option-text';
        text.textContent = (opt.textContent || opt.value).trim();

        row.append(check);
        if (markerEl) row.append(markerEl);
        if (iconEl) row.append(iconEl);
        row.append(text);
        list.appendChild(row);
    });
    syncFromSelect(state);
}

/**
 * Enhance a ``<select multiple>`` into a compact dropdown.
 * Safe to call repeatedly; rebuilds the option list when already enhanced.
 */
export function enhanceMultiSelect(select, options = {}) {
    if (!select || select.tagName !== 'SELECT') return null;
    select.multiple = true;
    select.removeAttribute('size');

    let state = ENHANCED.get(select);
    if (state) {
        rebuildList(state);
        return state;
    }

    const placeholder = options.placeholder
        || select.getAttribute('data-placeholder')
        || t('allSelectedNone', 'All');
    const searchable = options.searchable !== false;
    const iconForOption = options.iconForOption || null;
    const markerForOption = options.markerForOption || null;
    const summaryFn = options.summary || defaultSummary;

    const wrap = document.createElement('div');
    wrap.className = 'msdd';
    if (options.compact) wrap.classList.add('msdd-compact');

    select.classList.add('msdd-native');
    select.setAttribute('aria-hidden', 'true');
    select.tabIndex = -1;

    const parent = select.parentNode;
    parent.insertBefore(wrap, select);
    wrap.appendChild(select);

    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'msdd-trigger form-select form-select-sm';
    trigger.setAttribute('aria-haspopup', 'listbox');
    trigger.setAttribute('aria-expanded', 'false');
    trigger.setAttribute('aria-label', select.getAttribute('aria-label') || placeholder);
    trigger.innerHTML = [
        '<span class="msdd-trigger-label"></span>',
        '<span class="msdd-count" hidden>0</span>',
        '<i class="bi bi-chevron-down msdd-caret" aria-hidden="true"></i>',
    ].join('');

    const panel = document.createElement('div');
    panel.className = 'msdd-panel';
    panel.setAttribute('role', 'listbox');
    panel.setAttribute('aria-multiselectable', 'true');
    panel.hidden = true;

    if (searchable) {
        const search = document.createElement('input');
        search.type = 'search';
        search.className = 'form-control form-control-sm msdd-search';
        search.placeholder = options.searchPlaceholder
            || t('filterOptions', 'Filter…');
        search.setAttribute('aria-label', search.placeholder);
        search.addEventListener('input', () => {
            const q = search.value.trim().toLowerCase();
            panel.querySelectorAll('.msdd-option').forEach((row) => {
                const label = row.getAttribute('data-label') || '';
                const value = (row.getAttribute('data-value') || '').toLowerCase();
                row.hidden = !!(q && !label.includes(q) && !value.includes(q));
            });
        });
        panel.appendChild(search);
    }

    const actions = document.createElement('div');
    actions.className = 'msdd-actions';
    actions.innerHTML = [
        `<button type="button" class="msdd-action" data-msdd-all>${escapeHtml(t('selectAll', 'Select all'))}</button>`,
        `<button type="button" class="msdd-action" data-msdd-clear>${escapeHtml(t('clear', 'Clear'))}</button>`,
    ].join('');
    panel.appendChild(actions);

    const list = document.createElement('div');
    list.className = 'msdd-list';
    panel.appendChild(list);

    wrap.append(trigger, panel);

    state = {
        select,
        wrap,
        trigger,
        panel,
        list,
        placeholder,
        iconForOption,
        markerForOption,
        summaryFn,
        includeEmpty: !!options.includeEmpty,
        onChange: options.onChange || null,
    };
    ENHANCED.set(select, state);

    trigger.addEventListener('click', (event) => {
        event.preventDefault();
        const open = !wrap.classList.contains('is-open');
        closeAll(wrap);
        wrap.classList.toggle('is-open', open);
        panel.hidden = !open;
        trigger.setAttribute('aria-expanded', open ? 'true' : 'false');
        if (open) {
            const search = panel.querySelector('.msdd-search');
            if (search) search.focus();
        }
    });

    actions.addEventListener('click', (event) => {
        const all = event.target.closest('[data-msdd-all]');
        const clear = event.target.closest('[data-msdd-clear]');
        if (!all && !clear) return;
        const turnOn = !!all;
        Array.from(select.options).forEach((opt) => {
            if (!opt.value && !state.includeEmpty) return;
            const row = list.querySelector(`.msdd-option[data-value="${CSS.escape(opt.value)}"]`);
            if (row && row.hidden) return;
            opt.selected = turnOn;
        });
        select.dispatchEvent(new Event('change', { bubbles: true }));
        syncFromSelect(state);
        if (typeof state.onChange === 'function') state.onChange(select);
    });

    rebuildList(state);
    return state;
}

/** Rebuild option rows after the underlying ``<select>`` options change. */
export function refreshMultiSelect(select) {
    const state = ENHANCED.get(select);
    if (!state) return enhanceMultiSelect(select);
    rebuildList(state);
    return state;
}

/** Enhance every matching select in a root (default: document). */
export function enhanceMultiSelects(root = document, optionsById = {}) {
    const nodes = root.querySelectorAll('select[multiple][data-compact-multi], select.msdd-enhance');
    nodes.forEach((select) => {
        const kind = select.getAttribute('data-compact-multi') || 'generic';
        const extra = optionsById[select.id] || optionsById[kind] || {};
        if (kind === 'type' || kind === 'file-type') {
            enhanceMultiSelect(select, {
                placeholder: t('allTypes', 'All types'),
                iconForOption: (value) => iconForFileType(value),
                ...extra,
            });
        } else if (kind === 'source') {
            enhanceMultiSelect(select, {
                placeholder: t('allSources', 'All sources'),
                markerForOption: () => '#0ea5e9',
                iconForOption: () => 'bi-building',
                ...extra,
            });
        } else if (kind === 'side') {
            enhanceMultiSelect(select, {
                placeholder: t('allSides', 'All sides'),
                markerForOption: () => '#8b5cf6',
                iconForOption: () => 'bi-diagram-3',
                ...extra,
            });
        } else if (kind === 'category') {
            enhanceMultiSelect(select, {
                placeholder: t('allCategories', 'All categories'),
                markerForOption: () => '#10b981',
                iconForOption: () => 'bi-tags',
                ...extra,
            });
        } else if (kind === 'analyst-category') {
            enhanceMultiSelect(select, {
                placeholder: t('allAnalystCategories', 'All analyst categories'),
                markerForOption: () => '#7c3aed',
                iconForOption: () => 'bi-person-check',
                ...extra,
            });
        } else {
            enhanceMultiSelect(select, {
                placeholder: t('allSelectedNone', 'All'),
                ...extra,
            });
        }
    });
}

if (typeof document !== 'undefined') {
    document.addEventListener('click', (event) => {
        if (!event.target.closest('.msdd')) closeAll(null);
    });
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') closeAll(null);
    });
}
