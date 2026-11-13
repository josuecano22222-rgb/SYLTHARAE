/**
 * Analyst classification from any content display interface.
 *
 * Shared by the File Detail page, the Full Content Reader and the file
 * preview pop-up (the "File Details" modal on the archives page). Lets an
 * analyst/admin assign or remove ANALYST (manual) categories for the file
 * they are currently looking at — without going back to Advanced Search or
 * the Analyst View.
 *
 * All writes go through the existing, audit-logged endpoints:
 *   GET    /api/analyst/categories      — list analyst categories
 *   GET    /api/analyst/assignments     — current assignments (per file_id)
 *   POST   /api/analyst/assign          — {path_ids, category_id | category_name, source_query}
 *   POST   /api/analyst/remove          — {path_ids}
 * (FR-1.4: analyst namespace only; the smart taxonomy is never touched.)
 *
 * Two ways in, one implementation:
 *
 *   * a page-level card (``#analystClassifyCard`` — File Detail, Reader) is
 *     bound by this module's own self-initialization;
 *   * a card inside a container that shows *different files over time* (the
 *     preview modal, which has its own previous/next) is bound by calling
 *     ``bindAnalystClassify(card, fileId)`` every time another file is
 *     displayed, which re-points the card and refreshes its badges.
 *
 * Every lookup is scoped to the card element, never to a global id, so a page
 * can hold a page-level card and a modal card at once without them fighting
 * over the same controls.
 *
 * Page-data translations come from the nearest ``#analyst-classify-page-data``
 * JSON block (same pattern as the other page scripts).
 */

let translations = null;
let canCategorize = false;
let dataLoaded = false;

function classifyT(key, fallback) {
    return (translations && translations[key]) ? translations[key] : fallback;
}

/** Read the page-data block once (translations + write permission). */
function loadPageData() {
    if (dataLoaded) return;
    dataLoaded = true;
    const dataEl = document.getElementById('analyst-classify-page-data');
    if (!dataEl) return;
    try {
        const data = JSON.parse(dataEl.textContent);
        translations = data.translations || {};
        canCategorize = !!data.canCategorize;
    } catch (e) {
        /* fall back to the English defaults below */
    }
}

function csrfToken() {
    return document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
}

async function apiPost(url, payload) {
    const res = await fetch(url, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken(),
        },
        body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.success === false) {
        throw new Error(data.error || `HTTP ${res.status}`);
    }
    return data;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = String(text == null ? '' : text);
    return div.innerHTML;
}

let toastTimer = null;
function showToast(message, isError = false) {
    // One notification stack for the whole app: use it when the shell has it.
    if (window.Toast) {
        window.Toast[isError ? 'error' : 'success'](message);
        return;
    }
    let toast = document.getElementById('analystClassifyToast');
    if (!toast) {
        toast = document.createElement('div');
        toast.id = 'analystClassifyToast';
        toast.className = 'analyst-action-toast';
        document.body.appendChild(toast);
    }
    toast.textContent = message;
    toast.classList.toggle('toast-error', isError);
    toast.classList.add('visible');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove('visible'), 3200);
}

/**
 * The parts of a card, looked up inside it.
 *
 * Data attributes are the contract; the element ids are kept for styling and
 * for anything that already addressed them. No getElementById here: two cards
 * on one page must not resolve to the same controls.
 */
function cardParts(card) {
    return {
        badges: card.querySelector('[data-analyst-badges]') || card.querySelector('.analyst-classify-badges'),
        select: card.querySelector('[data-analyst-select]') || card.querySelector('.analyst-classify-controls select'),
        newName: card.querySelector('[data-analyst-new-name]') || card.querySelector('.analyst-classify-controls input'),
        assignBtn: card.querySelector('[data-analyst-assign]'),
        removeAllBtn: card.querySelector('[data-analyst-remove-all]'),
    };
}

function currentFileId(card) {
    const id = Number(card.dataset.fileId);
    return Number.isInteger(id) && id > 0 ? id : null;
}

/** Re-render the badge list from a categories array [{id, name}]. */
function renderBadges(card, categories) {
    const { badges } = cardParts(card);
    if (!badges) return;
    const canWrite = canCategorize || card.dataset.canCategorize === 'true';
    if (!categories || !categories.length) {
        badges.innerHTML = `<span class="analyst-classify-empty" data-analyst-empty>${escapeHtml(classifyT('noCategoriesYet', 'No analyst categories yet.'))}</span>`;
        return;
    }
    badges.innerHTML = categories.map(cat => `
        <span class="badge analyst-category-badge" data-category-id="${cat.id}">
            <i class="bi bi-person-fill me-1" aria-hidden="true"></i>${escapeHtml(cat.name)}
            ${canWrite ? `
            <button type="button" class="badge-remove" data-remove-category-id="${cat.id}"
                    title="${escapeHtml(classifyT('confirmRemove', 'Remove analyst category from this file? It returns to uncategorized status for search scope.'))}"
                    aria-label="${escapeHtml(classifyT('removeLabel', 'Remove'))} ${escapeHtml(cat.name)}">&times;</button>` : ''}
        </span>`).join('');
}

/** Authoritative refresh: exact per-file assignments via the file_id filter
 *  (the same source the Analyst View uses). */
async function reloadCurrentCategories(card) {
    const fileId = currentFileId(card);
    if (!fileId) return;
    try {
        const res = await fetch(`/api/analyst/assignments?file_id=${fileId}&per_page=100`);
        if (!res.ok) return;
        const data = await res.json();
        const rows = data.assignments || [];
        const seen = new Map();
        rows.forEach(r => seen.set(r.category_id, r.category_name));
        renderBadges(card, [...seen.entries()].map(([id, name]) => ({ id, name })));
    } catch (e) {
        console.error('analyst-classify: refresh failed', e);
    }
}

async function loadCategoryOptions(card) {
    const { select } = cardParts(card);
    if (!select) return;
    try {
        const res = await fetch('/api/analyst/categories');
        if (!res.ok) return;
        const categories = await res.json();
        if (!Array.isArray(categories)) return;
        select.innerHTML = '<option value="">' +
            escapeHtml(classifyT('chooseCategory', 'Choose analyst category…')) + '</option>' +
            categories.map(c =>
                `<option value="${c.id}">${escapeHtml(c.name)} (${c.file_count ?? 0})</option>`
            ).join('');
    } catch (e) {
        console.error('analyst-classify: could not load categories', e);
    }
}

async function assign(card) {
    const fileId = currentFileId(card);
    if (!fileId) return;
    const { select, newName } = cardParts(card);
    const categoryId = select && select.value ? parseInt(select.value, 10) : null;
    const categoryName = newName && newName.value.trim() ? newName.value.trim() : null;

    if (!categoryId && !categoryName) {
        showToast(classifyT('chooseOrCreateCategory', 'Choose an analyst category or type a new one'), true);
        return;
    }

    const payload = {
        path_ids: [Number(fileId)],
        source_query: classifyT('sourceContext', 'content view'),
        create_category: !categoryId && !!categoryName,
    };
    if (categoryId) payload.category_id = categoryId;
    if (categoryName) payload.category_name = categoryName;

    try {
        const data = await apiPost('/api/analyst/assign', payload);
        if (newName) newName.value = '';
        if (select) select.value = '';
        showToast(
            classifyT('assignedToast', 'Assigned "{category}" to {count} file(s)')
                .replace('{category}', data.category_name || categoryName || '')
                .replace('{count}', String(data.assigned ?? 1))
        );
        await reloadCurrentCategories(card);
    } catch (e) {
        showToast(classifyT('assignFailed', 'Analyst categorization failed') + ': ' + e.message, true);
    }
}

async function removeAll(card) {
    const fileId = currentFileId(card);
    if (!fileId) return;
    if (!window.confirm(
        classifyT('removeAllConfirmFile', 'Remove all analyst categories from this file? It will return to "uncategorized" for analyst search scope. Smart categories are not affected.')
    )) return;
    try {
        const data = await apiPost('/api/analyst/remove', { path_ids: [Number(fileId)] });
        showToast(
            classifyT('removedToast', 'Removed analyst categories from {count} file(s)')
                .replace('{count}', String(data.removed_assignments ?? 1))
        );
        await reloadCurrentCategories(card);
    } catch (e) {
        showToast(classifyT('removeFailed', 'Remove failed') + ': ' + e.message, true);
    }
}

async function removeOne(card, categoryId) {
    const fileId = currentFileId(card);
    if (!fileId) return;
    if (!window.confirm(
        classifyT('confirmRemove', 'Remove analyst category from this file? It returns to uncategorized status for search scope.')
    )) return;
    // The remove endpoint removes ALL analyst categories for given paths
    // (bulk semantics), so single-category removal = remove all + reassign
    // the remaining ones. Simplest correct sequence:
    try {
        const { badges } = cardParts(card);
        const current = [...(badges ? badges.querySelectorAll('.badge[data-category-id]') : [])];
        const remaining = current
            .map(b => parseInt(b.dataset.categoryId, 10))
            .filter(id => id !== Number(categoryId));
        await apiPost('/api/analyst/remove', { path_ids: [Number(fileId)] });
        for (const catId of remaining) {
            await apiPost('/api/analyst/assign', {
                path_ids: [Number(fileId)],
                category_id: catId,
                source_query: classifyT('sourceContext', 'content view'),
            });
        }
        showToast(classifyT('categoryRemoved', 'Analyst category removed'));
        await reloadCurrentCategories(card);
    } catch (e) {
        showToast(classifyT('removeFailed', 'Remove failed') + ': ' + e.message, true);
    }
}

/** Bind a card's controls once. Safe to call repeatedly for the same card. */
export function bindAnalystClassifyCard(card) {
    if (!card || card.dataset.analystBound === 'true') return false;
    loadPageData();
    card.dataset.analystBound = 'true';

    const { assignBtn, removeAllBtn, newName, badges } = cardParts(card);
    if (assignBtn) assignBtn.addEventListener('click', () => assign(card));
    if (removeAllBtn) removeAllBtn.addEventListener('click', () => removeAll(card));
    if (newName) {
        newName.addEventListener('keydown', e => {
            if (e.key === 'Enter') { e.preventDefault(); assign(card); }
        });
    }
    if (badges) {
        // Per-badge removal (event delegation — badges are re-rendered)
        badges.addEventListener('click', e => {
            const btn = e.target.closest('[data-remove-category-id]');
            if (btn) removeOne(card, btn.dataset.removeCategoryId);
        });
    }
    loadCategoryOptions(card);
    return true;
}

/**
 * Point a card at a file and refresh what it shows.
 *
 * The preview modal displays one file after another (including through its
 * own previous/next buttons), so the card is re-targeted on every display
 * rather than created again: one card, one set of listeners, no stale state.
 *
 * @param {HTMLElement} card - the card element (rendered by the shared
 *     analyst_classify macro, so page and modal markup cannot drift apart)
 * @param {number|string} fileId - the file now on screen
 * @returns {Promise<boolean>} whether the card was bound to that file
 */
export async function bindAnalystClassify(card, fileId) {
    const id = Number(fileId);
    if (!card || !Number.isInteger(id) || id <= 0) return false;
    bindAnalystClassifyCard(card);
    card.dataset.fileId = String(id);
    // The empty-state text and the write controls depend on the page data.
    const empty = card.querySelector('[data-analyst-empty]');
    if (empty) empty.textContent = classifyT('noCategoriesYet', 'No analyst categories yet.');
    await reloadCurrentCategories(card);
    return true;
}

function init() {
    const card = document.getElementById('analystClassifyCard');
    if (!card) return;
    loadPageData();
    bindAnalystClassifyCard(card);
}

if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
}
