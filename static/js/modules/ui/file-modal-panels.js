/**
 * File Details modal: collapsible / pinnable / expandable panels and icon toolbars.
 */

const STORAGE_PREFIX = 'syltharae.fileModal.panel.';
const EXPANDED_PANEL_KEY = 'syltharae.fileModal.expandedPanelId';

function panelEl(id) {
    return document.querySelector(`.file-modal-panel[data-panel-id="${id}"]`);
}

function savePanelState(id, state) {
    try {
        sessionStorage.setItem(`${STORAGE_PREFIX}${id}`, JSON.stringify(state));
    } catch (_e) { /* private mode */ }
}

function loadPanelState(id) {
    try {
        const raw = sessionStorage.getItem(`${STORAGE_PREFIX}${id}`);
        return raw ? JSON.parse(raw) : null;
    } catch (_e) {
        return null;
    }
}

function saveExpandedPanelId(id) {
    try {
        if (id) sessionStorage.setItem(EXPANDED_PANEL_KEY, id);
        else sessionStorage.removeItem(EXPANDED_PANEL_KEY);
    } catch (_e) { /* */ }
}

function loadExpandedPanelId() {
    try {
        return sessionStorage.getItem(EXPANDED_PANEL_KEY) || '';
    } catch (_e) {
        return '';
    }
}

function syncExpandButton(panel) {
    const expandBtn = panel.querySelector('[data-panel-expand]');
    if (!expandBtn) return;
    const expanded = panel.classList.contains('is-expanded');
    expandBtn.setAttribute('aria-pressed', expanded ? 'true' : 'false');
    expandBtn.classList.toggle('is-active', expanded);
    const icon = expandBtn.querySelector('.bi');
    if (icon) {
        icon.classList.toggle('bi-arrows-angle-expand', !expanded);
        icon.classList.toggle('bi-arrows-angle-contract', expanded);
    }
}

function clearExpandLayout(container) {
    if (!container) return;
    container.classList.remove(
        'has-expanded-panel',
        'is-focus-content',
        'is-focus-analysis',
        'is-focus-metadata',
    );
    container.removeAttribute('data-expanded-panel-id');
    container.querySelectorAll('.file-modal-panel.is-expanded').forEach((p) => {
        p.classList.remove('is-expanded');
        syncExpandButton(p);
    });
    container.querySelectorAll('.is-collapsed-by-expand').forEach((p) => {
        p.classList.remove('is-collapsed-by-expand');
    });
    saveExpandedPanelId('');
}

function applyExpandFocusClasses(container, panel) {
    container.classList.remove('is-focus-content', 'is-focus-analysis', 'is-focus-metadata');
    if (panel.closest('.file-details-content-section')) {
        container.classList.add('is-focus-content');
        return;
    }
    if (panel.closest('.file-details-analysis-section')) {
        container.classList.add('is-focus-analysis');
        return;
    }
    if (panel.classList.contains('file-details-metadata-section') || panel.closest('.file-details-metadata-section')) {
        container.classList.add('is-focus-metadata');
    }
}

function collapseSiblingsForExpand(panel) {
    const container = document.querySelector('#fileModal .file-details-container');
    if (!container) return;
    container.querySelectorAll('.is-collapsed-by-expand').forEach((p) => {
        p.classList.remove('is-collapsed-by-expand');
    });
    container.querySelectorAll('.file-modal-panel').forEach((p) => {
        if (p === panel || p.classList.contains('is-pinned')) return;
        p.classList.add('is-collapsed-by-expand');
    });
}

function setPanelExpanded(panel, expand) {
    const container = document.querySelector('#fileModal .file-details-container');
    if (!container || !panel) return;
    const id = panel.getAttribute('data-panel-id');
    if (!id) return;

    if (!expand) {
        clearExpandLayout(container);
        return;
    }

    clearExpandLayout(container);
    panel.classList.remove('is-collapsed', 'is-collapsed-by-expand');
    panel.classList.add('is-expanded');
    container.classList.add('has-expanded-panel');
    container.setAttribute('data-expanded-panel-id', id);
    collapseSiblingsForExpand(panel);
    applyExpandFocusClasses(container, panel);
    syncExpandButton(panel);
    saveExpandedPanelId(id);

    const collapseBtn = panel.querySelector('[data-panel-collapse]');
    if (collapseBtn) {
        collapseBtn.setAttribute('aria-expanded', 'true');
        const icon = collapseBtn.querySelector('.bi');
        if (icon) {
            icon.classList.add('bi-chevron-up');
            icon.classList.remove('bi-chevron-down');
        }
    }
}

function togglePanelExpanded(panel) {
    if (!panel) return;
    const willExpand = !panel.classList.contains('is-expanded');
    setPanelExpanded(panel, willExpand);
}

function applyExpandedPanelState(container) {
    const id = loadExpandedPanelId();
    if (!id) return;
    const panel = panelEl(id);
    if (!panel || !container.contains(panel)) return;
    setPanelExpanded(panel, true);
}

function applyPanelState(panel) {
    const id = panel.getAttribute('data-panel-id');
    if (!id) return;
    const saved = loadPanelState(id);
    const pinned = saved?.pinned === true;
    const collapsed = saved?.collapsed === true && !pinned;
    panel.classList.toggle('is-pinned', pinned);
    panel.classList.toggle('is-collapsed', collapsed);
    const pinBtn = panel.querySelector('[data-panel-pin]');
    const collapseBtn = panel.querySelector('[data-panel-collapse]');
    if (pinBtn) {
        pinBtn.setAttribute('aria-pressed', pinned ? 'true' : 'false');
        pinBtn.classList.toggle('is-active', pinned);
    }
    if (collapseBtn) {
        collapseBtn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
        const icon = collapseBtn.querySelector('.bi');
        if (icon) {
            icon.classList.toggle('bi-chevron-up', !collapsed);
            icon.classList.toggle('bi-chevron-down', collapsed);
        }
    }
    syncExpandButton(panel);
}

function persistPanel(panel) {
    const id = panel.getAttribute('data-panel-id');
    if (!id) return;
    savePanelState(id, {
        pinned: panel.classList.contains('is-pinned'),
        collapsed: panel.classList.contains('is-collapsed'),
    });
}

export function initFileModalTooltips(scope) {
    const root = scope || document.getElementById('fileModal');
    if (!root || !window.bootstrap?.Tooltip) return;
    root.querySelectorAll('[data-bs-toggle="tooltip"]').forEach((el) => {
        try {
            window.bootstrap.Tooltip.getInstance(el)?.dispose();
            window.bootstrap.Tooltip.getOrCreateInstance(el, {
                trigger: 'hover focus',
                container: 'body',
                boundary: 'viewport',
            });
        } catch (_e) { /* */ }
    });
}

function syncSearchToggle(btn) {
    const targetId = btn.getAttribute('data-toggle-target');
    const input = targetId ? document.getElementById(targetId) : null;
    if (!input || input.type !== 'checkbox') return;
    const on = !!input.checked;
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    btn.classList.toggle('is-active', on);
}

function wireSearchOptionToggles(root) {
    root.querySelectorAll('[data-toggle-target]').forEach((btn) => {
        if (btn.dataset.toggleWired) return;
        btn.dataset.toggleWired = '1';
        syncSearchToggle(btn);
        btn.addEventListener('click', () => {
            const targetId = btn.getAttribute('data-toggle-target');
            const input = targetId ? document.getElementById(targetId) : null;
            if (!input) return;
            input.checked = !input.checked;
            syncSearchToggle(btn);
        });
    });
    ['modalCaseSensitive', 'modalWholeWord'].forEach((id) => {
        const input = document.getElementById(id);
        if (!input || input.dataset.toggleSync) return;
        input.dataset.toggleSync = '1';
        input.addEventListener('change', () => {
            root.querySelectorAll(`[data-toggle-target="${id}"]`).forEach(syncSearchToggle);
        });
    });
}

let delegationBound = false;

function bindPanelDelegation() {
    if (delegationBound) return;
    delegationBound = true;
    document.addEventListener('click', (event) => {
        const expandBtn = event.target.closest('[data-panel-expand]');
        if (expandBtn) {
            const panel = expandBtn.closest('.file-modal-panel');
            if (!panel) return;
            event.preventDefault();
            togglePanelExpanded(panel);
            return;
        }
        const pinBtn = event.target.closest('[data-panel-pin]');
        if (pinBtn) {
            const panel = pinBtn.closest('.file-modal-panel');
            if (!panel) return;
            event.preventDefault();
            const container = document.querySelector('#fileModal .file-details-container');
            if (panel.classList.contains('is-expanded')) {
                clearExpandLayout(container);
            }
            const pinned = !panel.classList.contains('is-pinned');
            panel.classList.toggle('is-pinned', pinned);
            if (pinned) {
                panel.classList.remove('is-collapsed', 'is-collapsed-by-expand');
            }
            pinBtn.setAttribute('aria-pressed', pinned ? 'true' : 'false');
            pinBtn.classList.toggle('is-active', pinned);
            const collapseBtn = panel.querySelector('[data-panel-collapse]');
            if (collapseBtn) {
                collapseBtn.setAttribute('aria-expanded', 'true');
                const icon = collapseBtn.querySelector('.bi');
                if (icon) {
                    icon.classList.add('bi-chevron-up');
                    icon.classList.remove('bi-chevron-down');
                }
            }
            persistPanel(panel);
            return;
        }
        const collapseBtn = event.target.closest('[data-panel-collapse]');
        if (collapseBtn) {
            const panel = collapseBtn.closest('.file-modal-panel');
            if (!panel || panel.classList.contains('is-pinned')) return;
            event.preventDefault();
            if (panel.classList.contains('is-expanded')) {
                clearExpandLayout(document.querySelector('#fileModal .file-details-container'));
            }
            const collapsed = !panel.classList.contains('is-collapsed');
            panel.classList.toggle('is-collapsed', collapsed);
            collapseBtn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
            const icon = collapseBtn.querySelector('.bi');
            if (icon) {
                icon.classList.toggle('bi-chevron-up', !collapsed);
                icon.classList.toggle('bi-chevron-down', collapsed);
            }
            persistPanel(panel);
        }
    });
}

/**
 * Wire modal panels (idempotent). Call when the file modal opens or on load.
 */
export function initFileModalPanels() {
    const modal = document.getElementById('fileModal');
    if (!modal) return;
    bindPanelDelegation();
    modal.querySelectorAll('.file-modal-panel').forEach(applyPanelState);
    const container = modal.querySelector('.file-details-container');
    if (container) applyExpandedPanelState(container);
    wireSearchOptionToggles(modal);
    initFileModalTooltips(modal);
}

/** Reset search icon toggles after clearModalSearch. */
export function syncFileModalSearchToggles() {
    const modal = document.getElementById('fileModal');
    if (!modal) return;
    modal.querySelectorAll('[data-toggle-target]').forEach(syncSearchToggle);
}
