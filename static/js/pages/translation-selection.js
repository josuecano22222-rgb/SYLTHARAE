/**
 * Manual translation of a selection, on the file viewer and the search results.
 *
 * A reader selects text inside a region a page marks with `data-translatable`
 * (the file viewer's content, a search result's excerpt). A "Translate
 * selection" button appears next to the selection; pressing it posts the text
 * to `/api/translation/text` and renders the answer in the panel below:
 * original and translation side by side, one row per sentence, labelled
 * "Machine translation, not verified".
 *
 * Every call is a POST with the CSRF token. Nothing is translated on load, on
 * navigation or on hover: a request goes out only when a person pressed the
 * button. Accept keeps the translation as it is; Correct opens an input for the
 * analyst's wording and posts it to `/api/translation/memory/correct`, which
 * makes that wording win over the engine for the same sentence forever.
 *
 * The reader's language declaration is optional: leaving "Detect language"
 * selected lets the server decide, and the server reports which language it saw
 * so a mismatch is visible.
 */

const PANEL_SELECTOR = '[data-translation-panel]';
const RUN_BUTTON_TEXT = 'Translate selection';
const DISCLAIMER = 'Machine translation, not verified';

const state = {
    selection: '',
    fileId: null,
    lastResult: null,
};

function panel() {
    return document.querySelector(PANEL_SELECTOR);
}

function csrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    if (meta && meta.content) return meta.content;
    const match = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : '';
}

async function postJson(path, body) {
    const response = await fetch(path, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken(),
        },
        credentials: 'same-origin',
        body: JSON.stringify(body),
    });
    let payload = null;
    try {
        payload = await response.json();
    } catch (error) {
        payload = null;
    }
    return { ok: response.ok, status: response.status, payload };
}

function showError(message) {
    const root = panel();
    if (!root) return;
    const box = root.querySelector('[data-translation-error]');
    if (!box) return;
    box.textContent = message;
    box.classList.remove('d-none');
}

function clearError() {
    const box = panel()?.querySelector('[data-translation-error]');
    if (!box) return;
    box.textContent = '';
    box.classList.add('d-none');
}

function engineSummary(result) {
    const engine = result.engine || {};
    const parts = [];
    // A memory answer names no model: nothing ran for this sentence, and the
    // correction (if there is one) is the analyst's wording, not the engine's.
    if (engine.served_from_memory || result.engine_calls === 0) {
        const corrected = (result.segments || []).some((s) => s.origin === 'user');
        parts.push(corrected ? 'from translation memory (corrected)'
                             : 'from translation memory');
    } else if (engine.id) {
        parts.push(`${engine.id}${engine.version ? ` ${engine.version}` : ''}`);
    }
    if (result.detected_language && result.source_language_override) {
        parts.push(`looks like ${result.detected_language}, you said ${result.source_lang}`);
    } else if (result.source_lang) {
        parts.push(`${result.source_lang} → ${result.target_lang}`);
    }
    return parts.join(' · ');
}

function renderRow(segment, index) {
    const row = document.createElement('tr');
    row.dataset.segmentIndex = String(index);

    const source = document.createElement('td');
    source.dir = 'auto';
    source.textContent = segment.source;

    const target = document.createElement('td');
    target.dir = 'auto';
    target.dataset.translationTarget = '';
    target.textContent = segment.target || '';

    if (segment.origin === 'user') {
        row.dataset.accepted = 'true';
        accept.disabled = true;             // it is already the analyst's wording
        accept.textContent = 'Accept translation';
        const badge = document.createElement('span');
        badge.className = 'badge bg-success ms-2';
        badge.textContent = 'Corrected translation saved';
        target.appendChild(badge);
    }

    const actions = document.createElement('td');
    const accept = document.createElement('button');
    accept.type = 'button';
    accept.className = 'btn btn-sm btn-outline-success me-1';
    accept.dataset.translationAccept = '';
    accept.textContent = 'Accept translation';
    const correct = document.createElement('button');
    correct.type = 'button';
    correct.className = 'btn btn-sm btn-outline-secondary';
    correct.dataset.translationCorrect = '';
    correct.textContent = 'Correct translation';
    actions.append(accept, correct);

    row.append(source, target, actions);
    return row;
}

function render(result) {
    const root = panel();
    if (!root) return;
    root.classList.remove('d-none');

    const body = root.querySelector('[data-translation-rows]');
    if (body) {
        body.replaceChildren();
        (result.segments || []).forEach((segment, index) => {
            body.appendChild(renderRow(segment, index));
        });
    }

    const summary = root.querySelector('[data-translation-engine]');
    if (summary) summary.textContent = engineSummary(result);

    const disclaimer = root.querySelector('[data-translation-disclaimer]');
    if (disclaimer) disclaimer.textContent = DISCLAIMER;

    state.lastResult = result;
}

async function runTranslation() {
    const root = panel();
    if (!root || !state.selection) return;
    clearError();
    const button = root.querySelector('[data-translation-run]');
    const original = button ? button.textContent : '';
    if (button) {
        button.disabled = true;
        button.textContent = 'Translating…';
    }
    try {
        const payload = {
            text: state.selection,
            target_lang: root.querySelector('[data-translation-target]')?.value || 'he',
        };
        const source = root.querySelector('[data-translation-source]')?.value;
        if (source) payload.source_lang = source;
        if (state.fileId) payload.file_id = state.fileId;

        const { ok, status, payload: body } = await postJson('/api/translation/text', payload);
        if (ok && body) {
            render(body);
        } else if (status === 503) {
            showError(body?.error || 'Translation engine is not installed');
        } else {
            showError(body?.error || 'Translation failed');
        }
    } catch (error) {
        showError('Translation failed');
    } finally {
        if (button) {
            button.disabled = false;
            button.textContent = original || RUN_BUTTON_TEXT;
        }
    }
}

async function saveCorrection(row) {
    const index = Number(row.dataset.segmentIndex);
    const segment = (state.lastResult?.segments || [])[index];
    if (!segment) return;
    const cell = row.querySelector('[data-translation-target]');
    // The project overrides window.prompt with an async dialog: it must be awaited.
    const corrected = await window.prompt('Correct translation', cell ? cell.textContent : '');
    if (corrected === null) return;
    const text = corrected.trim();
    if (!text) return;

    const result = state.lastResult;
    const { ok, payload } = await postJson('/api/translation/memory/correct', {
        source_text: segment.source,
        target_text: text,
        source_lang: result.source_lang,
        target_lang: result.target_lang,
    });
    if (ok) {
        if (cell) cell.textContent = text;
        clearError();
    } else {
        showError(payload?.error || 'Translation failed');
    }
}

async function acceptTranslation(row) {
    // Accepting is one click: the analyst says "this wording is right" and the
    // wording is stored as their correction, with their name on it. No prompt -
    // there is nothing to type; Correct is where typing happens.
    const index = Number(row.dataset.segmentIndex);
    const segment = (state.lastResult?.segments || [])[index];
    const cell = row.querySelector('[data-translation-target]');
    const text = (cell ? cell.textContent : '').trim();
    if (!segment || !text) return;
    if (row.dataset.accepted === 'true') return;   // already the analyst's wording

    const result = state.lastResult;
    const { ok, payload } = await postJson('/api/translation/memory/correct', {
        source_text: segment.source,
        target_text: text,
        source_lang: result.source_lang,
        target_lang: result.target_lang,
    });
    if (!ok) {
        showError(payload?.error || 'Translation failed');
        return;
    }
    clearError();
    row.dataset.accepted = 'true';
    const badge = document.createElement('span');
    badge.className = 'badge bg-success ms-2';
    badge.textContent = 'Corrected translation saved';
    if (cell) cell.appendChild(badge);
    if (segment) segment.origin = 'user';
}

function wirePanel() {
    const root = panel();
    if (!root || root.dataset.wired === 'true') return;
    root.dataset.wired = 'true';
    root.querySelector('[data-translation-run]')?.addEventListener('click', runTranslation);
    root.querySelector('[data-translation-close]')?.addEventListener('click', () => {
        root.classList.add('d-none');
    });
    root.addEventListener('click', (event) => {
        const accept = event.target.closest('[data-translation-accept]');
        if (accept) {
            acceptTranslation(accept.closest('tr'));
            return;
        }
        const correct = event.target.closest('[data-translation-correct]');
        if (correct) saveCorrection(correct.closest('tr'));
    });
}

function selectionInsideTranslatable() {
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed) return null;
    const text = selection.toString().trim();
    if (text.length < 2) return null;
    const anchor = selection.anchorNode;
    const element = anchor && (anchor.nodeType === 1 ? anchor : anchor.parentElement);
    const region = element ? element.closest('[data-translatable]') : null;
    if (!region) return null;
    return { text, region };
}

function showRunButton(near) {
    const root = panel();
    if (!root) return;
    root.classList.remove('d-none');
    // The panel is below the content on the viewer and below the results on
    // search: bring it into view, or the reader clicks a button that appears to
    // do nothing.
    // Instant, not smooth: a moving target is a button that misses the click.
    root.scrollIntoView({ block: 'nearest' });
    const button = root.querySelector('[data-translation-run]');
    if (button) button.focus({ preventScroll: true });
    const owner = near?.closest('[data-file-id]');
    state.fileId = owner ? Number(owner.dataset.fileId) || null : state.fileId;
}

document.addEventListener('mouseup', () => {
    const found = selectionInsideTranslatable();
    if (!found) return;
    state.selection = found.text;
    showRunButton(found.region);
});

document.addEventListener('DOMContentLoaded', () => {
    wirePanel();
    const root = panel();
    if (root) {
        const owner = document.querySelector('[data-translatable][data-file-id]');
        if (owner) state.fileId = Number(owner.dataset.fileId) || null;
    }
});

export { runTranslation, saveCorrection, acceptTranslation, selectionInsideTranslatable };
