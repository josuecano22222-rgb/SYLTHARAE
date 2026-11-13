/**
 * Translations Loader (compatibility shim)
 * ----------------------------------------
 * The localization runtime now lives in static/js/modules/core/app-i18n.js,
 * which loads the full server catalog (/api/i18n/catalog), the static UI packs
 * and inline page translations, and keeps window.translations /
 * window.appTranslations up to date.
 *
 * This module keeps the historical API (loadTranslations / reloadTranslations
 * / getTranslation / currentLocale) working for any module that still imports
 * it, delegating everything to the shared I18N runtime instead of performing
 * its own network fetches.
 */

import { getPageData } from './pages/data-helper.js';

function runtime() {
    return window.I18N || null;
}

function ensureInlineTranslations() {
    // Templates can embed page-specific translations in #page-translations.
    const el = document.getElementById('page-translations');
    if (!el || !window.I18N) return;
    try {
        const pageTranslations = JSON.parse(el.textContent || '{}');
        if (pageTranslations && Object.keys(pageTranslations).length) {
            window.I18N.addTranslations(window.I18N.getLocale(), pageTranslations);
        }
    } catch (e) { /* ignore malformed JSON */ }
}

/**
 * Load (or ensure) translations for the active locale.
 * @param {boolean} [forceReload] kept for API compatibility; the runtime
 *                                always serves the active locale dictionary.
 */
export async function loadTranslations(forceReload = false) {
    const rt = runtime();
    if (rt) {
        ensureInlineTranslations();
        if (forceReload) {
            await rt.setLocale(rt.getLocale());
        }
    } else {
        // Runtime not loaded yet - publish whatever the page embedded so
        // legacy consumers keep working.
        window.translations = window.translations || {};
        window.appTranslations = window.appTranslations || {};
        const pageTranslations = getPageData('translations');
        if (pageTranslations) {
            Object.assign(window.translations, pageTranslations);
            Object.assign(window.appTranslations, pageTranslations);
        }
    }
    window.dispatchEvent(new CustomEvent('translationsLoaded', {
        detail: { locale: (window.I18N && window.I18N.getLocale()) || 'en', translations: window.translations }
    }));
}

/**
 * Reload translations for a specific locale.
 * @param {string} [locale] - target locale (defaults to the active locale)
 */
export async function reloadTranslations(locale) {
    const rt = runtime();
    if (rt) {
        await rt.setLocale(locale || rt.getLocale());
    }
}

/**
 * Get a translation for a key (falls back to the key itself).
 */
export function getTranslation(key, defaultValue = null) {
    if (window.translations && window.translations[key]) {
        return window.translations[key];
    }
    return defaultValue || key;
}

// Load on DOM ready (kept for backward compatibility with import side effects)
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => loadTranslations());
} else {
    loadTranslations();
}
