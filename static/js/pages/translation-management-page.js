const PLACEHOLDER_PATTERN = /%\([A-Za-z_][A-Za-z0-9_]*\)[#0 +\-]?[0-9]*(?:\.[0-9]+)?[diouxXeEfFgGcrsa]|(?<!\{)\{[A-Za-z_][A-Za-z0-9_]*\}(?!\})/g;

function translate(text) {
    return typeof window.t === 'function' ? window.t(text) : text;
}

function placeholders(text) {
    return (String(text || '').match(PLACEHOLDER_PATTERN) || []).sort();
}

function placeholdersMatch(source, translation) {
    return JSON.stringify(placeholders(source)) === JSON.stringify(placeholders(translation));
}

function initTranslationManager() {
    const root = document.querySelector('[data-translation-manager]');
    if (!root) return;

    const elements = {
        locale: document.getElementById('tm-locale'),
        screen: document.getElementById('tm-screen'),
        query: document.getElementById('tm-query'),
        statusFilter: document.getElementById('tm-status-filter'),
        search: document.getElementById('tm-search-button'),
        refresh: document.getElementById('tm-refresh-button'),
        saveButtons: Array.from(document.querySelectorAll('[data-tm-save]')),
        dirtyCounts: Array.from(document.querySelectorAll('[data-tm-dirty-count]')),
        rows: document.getElementById('tm-rows'),
        empty: document.getElementById('tm-empty'),
        loading: document.getElementById('tm-loading'),
        feedback: document.getElementById('tm-feedback'),
        resultCount: document.getElementById('tm-result-count'),
        total: document.getElementById('tm-stat-total'),
        translated: document.getElementById('tm-stat-translated'),
        missing: document.getElementById('tm-stat-missing'),
        overridden: document.getElementById('tm-stat-overridden'),
        previous: document.getElementById('tm-previous'),
        next: document.getElementById('tm-next'),
        pageLabel: document.getElementById('tm-page-label')
    };

    const pageSize = Number.parseInt(root.dataset.pageSize || '50', 10) || 50;
    const state = {
        locale: elements.locale?.value || root.dataset.defaultLocale || 'ar',
        screen: '',
        query: '',
        status: elements.statusFilter?.value || 'all',
        page: 1,
        pages: 1,
        items: [],
        dirty: new Map(),
        loadingRequest: null,
        saving: false
    };

    function csrfToken() {
        return document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
    }

    function showFeedback(message, kind = 'info') {
        if (!elements.feedback) return;
        elements.feedback.textContent = message;
        elements.feedback.dataset.kind = kind;
        elements.feedback.hidden = !message;
    }

    function clearFeedback() {
        if (!elements.feedback) return;
        elements.feedback.textContent = '';
        elements.feedback.hidden = true;
        delete elements.feedback.dataset.kind;
    }

    function hasDirtyChanges() {
        return state.dirty.size > 0;
    }

    function mayDiscardChanges() {
        if (!hasDirtyChanges()) return true;
        return window.confirm(translate('Discard unsaved changes?'));
    }

    function updateSaveState() {
        const invalid = Array.from(state.dirty.values()).some(change => !change.reset && (
            !change.translation.trim() || !change.validPlaceholders
        ));
        elements.saveButtons.forEach(button => {
            button.disabled = state.saving || state.dirty.size === 0 || invalid;
            button.setAttribute('aria-busy', state.saving ? 'true' : 'false');
        });
        elements.dirtyCounts.forEach(counter => {
            counter.textContent = state.dirty.size ? String(state.dirty.size) : '';
        });
    }

    function setStats(stats = {}) {
        if (elements.total) elements.total.textContent = new Intl.NumberFormat().format(stats.total || 0);
        if (elements.translated) elements.translated.textContent = new Intl.NumberFormat().format(stats.translated || 0);
        if (elements.missing) elements.missing.textContent = new Intl.NumberFormat().format(stats.missing || 0);
        if (elements.overridden) elements.overridden.textContent = new Intl.NumberFormat().format(stats.overridden || 0);
    }

    function setScreenOptions(screens) {
        if (!elements.screen) return;
        const selected = state.screen;
        const allOption = elements.screen.options[0];
        elements.screen.replaceChildren(allOption);
        (screens || []).forEach(screen => {
            const option = document.createElement('option');
            option.value = screen.id;
            option.textContent = `${translate(screen.label)} (${new Intl.NumberFormat().format(screen.count)})`;
            elements.screen.appendChild(option);
        });
        if (selected && Array.from(elements.screen.options).some(option => option.value === selected)) {
            elements.screen.value = selected;
        } else {
            state.screen = '';
            elements.screen.value = '';
        }
    }

    function setCellLabel(cell, label) {
        cell.dataset.label = translate(label);
    }

    function makeStatus(entry, pending) {
        const status = document.createElement('span');
        status.className = 'tm-row-status';
        status.dataset.status = entry.status;
        status.dataset.overridden = entry.overridden ? 'true' : 'false';

        if (pending?.reset) {
            status.textContent = translate('Will reset');
            status.dataset.status = 'pending';
        } else if (pending) {
            status.textContent = translate('Unsaved changes');
            status.dataset.status = 'pending';
        } else if (entry.overridden) {
            status.textContent = translate('Custom override');
        } else if (entry.status === 'translated') {
            status.textContent = translate('Translated');
        } else {
            status.textContent = translate('Missing');
        }
        return status;
    }

    function renderRows() {
        if (!elements.rows) return;
        elements.rows.replaceChildren();
        const fragment = document.createDocumentFragment();

        state.items.forEach(entry => {
            const row = document.createElement('tr');
            row.dataset.translationId = entry.id;
            const pending = state.dirty.get(entry.id);

            const sourceCell = document.createElement('td');
            setCellLabel(sourceCell, 'Source text');
            const sourceText = document.createElement('div');
            sourceText.className = 'tm-source-text';
            sourceText.dir = 'ltr';
            sourceText.lang = 'en';
            sourceText.textContent = entry.source;
            sourceCell.appendChild(sourceText);
            if (entry.locations?.length) {
                const paths = document.createElement('div');
                paths.className = 'tm-source-paths';
                const visibleLocations = entry.locations.slice(0, 3);
                visibleLocations.forEach(location => {
                    const path = document.createElement('code');
                    path.textContent = location.line
                        ? `${location.path}:${location.line}`
                        : location.path;
                    path.title = location.screen_label || location.path;
                    paths.appendChild(path);
                });
                if (entry.locations.length > visibleLocations.length) {
                    const more = document.createElement('span');
                    more.className = 'tm-source-more';
                    more.textContent = `+${entry.locations.length - visibleLocations.length}`;
                    more.title = entry.locations.slice(visibleLocations.length)
                        .map(location => location.line ? `${location.path}:${location.line}` : location.path)
                        .join('\n');
                    paths.appendChild(more);
                }
                sourceCell.appendChild(paths);
            }

            const translationCell = document.createElement('td');
            setCellLabel(translationCell, 'Translation');
            const textarea = document.createElement('textarea');
            textarea.className = 'tm-translation-input';
            textarea.dataset.translationId = entry.id;
            textarea.dir = 'auto';
            textarea.lang = state.locale;
            textarea.maxLength = 10000;
            textarea.value = pending?.reset
                ? entry.base_translation
                : (pending?.translation ?? entry.translation);
            textarea.setAttribute('aria-label', `${translate('Translation for')}: ${entry.source.slice(0, 180)}`);
            textarea.setAttribute('aria-describedby', `tm-help-${entry.id}`);
            textarea.addEventListener('input', () => syncPendingState(entry, textarea, row));
            translationCell.appendChild(textarea);
            const help = document.createElement('span');
            help.id = `tm-help-${entry.id}`;
            help.className = 'visually-hidden';
            help.textContent = translate('Keep named placeholders unchanged.');
            translationCell.appendChild(help);

            const statusCell = document.createElement('td');
            setCellLabel(statusCell, 'Status');
            statusCell.appendChild(makeStatus(entry, pending));

            const actionsCell = document.createElement('td');
            setCellLabel(actionsCell, 'Row actions');
            const reset = document.createElement('button');
            reset.className = 'btn btn-sm btn-outline-secondary tm-reset-button';
            reset.type = 'button';
            reset.innerHTML = '<i class="bi bi-arrow-counterclockwise" aria-hidden="true"></i>';
            reset.title = translate('Reset to catalog translation');
            reset.setAttribute('aria-label', `${translate('Reset to catalog translation')}: ${entry.source.slice(0, 120)}`);
            reset.disabled = !entry.overridden && !pending;
            reset.addEventListener('click', () => {
                textarea.value = entry.base_translation;
                syncPendingState(entry, textarea, row, true);
            });
            actionsCell.appendChild(reset);

            row.append(sourceCell, translationCell, statusCell, actionsCell);
            fragment.appendChild(row);
        });

        elements.rows.appendChild(fragment);
        const noItems = state.items.length === 0;
        if (elements.empty) elements.empty.hidden = !noItems;
        if (elements.loading) elements.loading.hidden = true;
        if (elements.resultCount) {
            elements.resultCount.textContent = `${new Intl.NumberFormat().format(state.total || 0)} ${translate('matching strings')}`;
        }
        if (elements.pageLabel) {
            elements.pageLabel.textContent = `${translate('Page')} ${state.page} ${translate('of')} ${state.pages}`;
        }
        if (elements.previous) elements.previous.disabled = state.page <= 1 || state.saving;
        if (elements.next) elements.next.disabled = state.page >= state.pages || state.saving;
        updateSaveState();
    }

    function syncPendingState(entry, textarea, row, forceReset = false) {
        const value = textarea.value;
        const resetToBase = forceReset || (entry.overridden && value === entry.base_translation);
        let change = null;

        if (resetToBase) {
            change = { reset: true, translation: '', validPlaceholders: true };
        } else if (value !== entry.translation) {
            change = {
                reset: false,
                translation: value,
                validPlaceholders: placeholdersMatch(entry.source, value)
            };
        }

        if (change) state.dirty.set(entry.id, change);
        else state.dirty.delete(entry.id);

        const invalid = Boolean(change && !change.reset && (
            !value.trim() || !change.validPlaceholders
        ));
        textarea.setAttribute('aria-invalid', invalid ? 'true' : 'false');
        textarea.title = invalid
            ? translate(!value.trim()
                ? 'A translation cannot be blank.'
                : 'Keep every named placeholder from the source text.')
            : '';

        const statusCell = row.children[2];
        statusCell.replaceChildren(makeStatus(entry, change));
        const resetButton = row.querySelector('.tm-reset-button');
        if (resetButton) resetButton.disabled = !entry.overridden && !change;
        updateSaveState();
    }

    async function loadCatalog(options = {}) {
        const { allowDiscard = false, keepFeedback = false } = options;
        if (!allowDiscard && !mayDiscardChanges()) return false;
        if (!keepFeedback) clearFeedback();

        state.locale = elements.locale?.value || state.locale;
        state.screen = elements.screen?.value || '';
        state.query = elements.query?.value.trim() || '';
        state.status = elements.statusFilter?.value || 'all';
        state.dirty.clear();
        state.page = Math.max(1, state.page);
        updateSaveState();

        if (state.loadingRequest) state.loadingRequest.abort();
        state.loadingRequest = new AbortController();
        if (elements.loading) elements.loading.hidden = false;
        if (elements.empty) elements.empty.hidden = true;
        if (elements.rows) elements.rows.replaceChildren();
        if (elements.resultCount) elements.resultCount.textContent = translate('Loading translations…');

        const params = new URLSearchParams({
            locale: state.locale,
            screen: state.screen,
            q: state.query,
            status: state.status,
            page: String(state.page),
            per_page: String(pageSize)
        });

        try {
            const response = await fetch(`${root.dataset.catalogUrl}?${params.toString()}`, {
                method: 'GET',
                credentials: 'same-origin',
                headers: { 'X-Requested-With': 'XMLHttpRequest' },
                signal: state.loadingRequest.signal
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok || !data.success) {
                throw new Error(data.error || translate('Could not load translations.'));
            }

            state.locale = data.locale || state.locale;
            state.page = data.page || 1;
            state.pages = data.pages || 1;
            state.total = data.total || 0;
            state.items = Array.isArray(data.items) ? data.items : [];
            setStats(data.stats);
            setScreenOptions(data.screens);
            renderRows();
        } catch (error) {
            if (error.name === 'AbortError') return false;
            if (elements.loading) elements.loading.hidden = true;
            if (elements.empty) elements.empty.hidden = false;
            if (elements.resultCount) elements.resultCount.textContent = translate('Catalog unavailable');
            showFeedback(error.message || translate('Could not load translations.'), 'error');
            return false;
        }
        return true;
    }

    async function changeFilter() {
        if (!mayDiscardChanges()) {
            elements.locale.value = state.locale;
            elements.screen.value = state.screen;
            elements.statusFilter.value = state.status;
            return;
        }
        state.page = 1;
        await loadCatalog({ allowDiscard: true });
    }

    function startSearch() {
        if (!mayDiscardChanges()) return;
        state.page = 1;
        loadCatalog({ allowDiscard: true });
    }

    async function saveChanges() {
        if (state.saving || state.dirty.size === 0) return;
        const invalid = Array.from(state.dirty.values()).some(change => !change.reset && (
            !change.translation.trim() || !change.validPlaceholders
        ));
        if (invalid) {
            showFeedback(translate('Fix blank translations and placeholders before saving.'), 'error');
            return;
        }

        const changes = Array.from(state.dirty.entries()).map(([id, change]) => ({
            id,
            ...(change.reset ? { reset: true } : { translation: change.translation })
        }));
        const currentEntries = new Map(state.items.map(entry => [entry.id, entry]));
        state.saving = true;
        updateSaveState();

        try {
            const response = await fetch(root.dataset.saveUrl, {
                method: 'PUT',
                credentials: 'same-origin',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfToken(),
                    'X-Requested-With': 'XMLHttpRequest'
                },
                body: JSON.stringify({ locale: state.locale, translations: changes })
            });
            const result = await response.json().catch(() => ({}));
            if (!response.ok || !result.success) {
                throw new Error(result.error || translate('Could not save translations.'));
            }

            const localUpdates = {};
            changes.forEach(change => {
                const entry = currentEntries.get(change.id);
                if (!entry) return;
                const value = change.reset
                    ? (entry.base_translation || entry.source)
                    : change.translation;
                localUpdates[entry.source] = value;
                if (window.currentLocale === state.locale) {
                    if (window.translations) window.translations[entry.source] = value;
                    if (window.appTranslations) window.appTranslations[entry.source] = value;
                }
            });
            if (window.I18N?.addTranslations) window.I18N.addTranslations(state.locale, localUpdates);

            state.dirty.clear();
            const reloaded = await loadCatalog({ allowDiscard: true, keepFeedback: true });
            if (reloaded) showFeedback(translate('Translations saved successfully.'), 'success');
        } catch (error) {
            showFeedback(error.message || translate('Could not save translations.'), 'error');
        } finally {
            state.saving = false;
            updateSaveState();
        }
    }

    elements.search?.addEventListener('click', startSearch);
    elements.query?.addEventListener('keydown', event => {
        if (event.key === 'Enter') {
            event.preventDefault();
            startSearch();
        }
    });
    elements.locale?.addEventListener('change', changeFilter);
    elements.screen?.addEventListener('change', changeFilter);
    elements.statusFilter?.addEventListener('change', changeFilter);
    elements.refresh?.addEventListener('click', () => loadCatalog());
    elements.saveButtons.forEach(button => button.addEventListener('click', saveChanges));
    elements.previous?.addEventListener('click', () => {
        if (state.page > 1 && mayDiscardChanges()) {
            state.page -= 1;
            loadCatalog({ allowDiscard: true });
        }
    });
    elements.next?.addEventListener('click', () => {
        if (state.page < state.pages && mayDiscardChanges()) {
            state.page += 1;
            loadCatalog({ allowDiscard: true });
        }
    });

    window.addEventListener('beforeunload', event => {
        if (!hasDirtyChanges()) return;
        event.preventDefault();
        event.returnValue = '';
    });

    loadCatalog({ allowDiscard: true });
}

export default async function init() {
    initTranslationManager();
}
