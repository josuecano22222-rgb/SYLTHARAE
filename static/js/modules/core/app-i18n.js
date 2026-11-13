/**
 * Application I18N Runtime
 * ========================
 * Central client-side localization layer.
 *
 * Responsibilities:
 *  - Merge every translation source into one dictionary per locale:
 *      1. The complete server catalog served by /api/translations (Flask-Babel).
 *      2. Static UI packs (static/js/i18n/locales/*.js) covering strings that
 *         only exist in front-end JavaScript (renderers, validators, etc.).
 *      3. Inline page translations embedded by templates (#page-translations).
 *  - Expose the global helpers the rest of the app already uses:
 *      window.t(), window.translate(), window.TranslationHelper,
 *      window.translations, window.appTranslations, window.currentLocale.
 *  - Translate the live DOM:
 *      [data-i18n], [data-i18n-placeholder], [data-i18n-title],
 *      [data-i18n-aria] attributes plus exact-match text nodes, so content
 *      rendered dynamically by any module is localized as soon as it appears
 *      (MutationObserver driven).
 *
 * Safety rules for the DOM rewriter:
 *  - Never touches script/style/textarea/code/pre blocks or the file content
 *    viewers (their text is user data, not interface chrome).
 *  - Only exact, whole-string matches against the dictionary, length-capped.
 *  - Any element can opt out with data-i18n-skip.
 */
(function () {
    'use strict';

    var RTL_LANGUAGES = ['ar', 'fa', 'he', 'ur'];
    var MAX_TEXT_LENGTH = 200;
    var SKIP_SELECTOR = [
        'script', 'style', 'noscript', 'textarea', 'code', 'pre', 'kbd', 'samp',
        '[contenteditable="true"]', '[data-i18n-skip]', '[data-no-i18n]',
        '.formatted-content', '.full-content-viewer', '#fileContentText',
        '#fullContentText', '.document-content', '.email-body-content',
        '.chart-tooltip', '.select2-container', '.toast-content pre'
    ].join(', ');

    var state = {
        locale: (document.documentElement.getAttribute('lang') || 'en').split('-')[0],
        dictionaries: {},   // locale -> { source: translation }
        packs: {},          // locale -> pack name (registered by locale files)
        appliedLang: null,
        observer: null,
        ready: false
    };

    /* ------------------------------------------------------------------ *
     * Dictionary helpers
     * ------------------------------------------------------------------ */

    function dictFor(locale) {
        if (!state.dictionaries[locale]) {
            state.dictionaries[locale] = {};
        }
        return state.dictionaries[locale];
    }

    /** Register translations for a locale (later calls win). */
    function addTranslations(locale, map) {
        if (!locale || !map) { return; }
        var dict = dictFor(locale);
        for (var key in map) {
            if (Object.prototype.hasOwnProperty.call(map, key) && map[key]) {
                dict[key] = map[key];
            }
        }
    }

    /** Look up a translation with graceful fallback to the source string. */
    function lookup(key, locale) {
        if (!key) { return key; }
        var loc = locale || state.locale;
        var dict = state.dictionaries[loc];
        if (dict && Object.prototype.hasOwnProperty.call(dict, key)) {
            return dict[key];
        }
        // Fall back to the default locale dictionary, then to the key itself.
        if (loc !== 'en' && state.dictionaries.en &&
            Object.prototype.hasOwnProperty.call(state.dictionaries.en, key)) {
            return state.dictionaries.en[key];
        }
        return key;
    }

    /** Replace {name} and %(name)s style placeholders. */
    function interpolate(text, params) {
        if (!params) { return text; }
        Object.keys(params).forEach(function (name) {
            var value = String(params[name]);
            text = text.split('{' + name + '}').join(value);
            text = text.split('%(' + name + ')s').join(value);
        });
        return text;
    }

    /**
     * Translate a string.
     * @param {string} key  English source string (msgid).
     * @param {object} [params] Placeholder values.
     */
    function t(key, params) {
        if (typeof key !== 'string' || !key) { return key || ''; }
        return interpolate(lookup(key), params);
    }

    /* ------------------------------------------------------------------ *
     * DOM translation
     * ------------------------------------------------------------------ */

    function isSkippable(node) {
        if (!node || node.nodeType !== Node.ELEMENT_NODE) { return false; }
        if (node.hasAttribute('data-i18n-applied')) { return true; }
        return typeof node.closest === 'function' && node.closest(SKIP_SELECTOR) !== null;
    }

    function applyElementTranslations(root) {
        // data-i18n -> text content
        root.querySelectorAll('[data-i18n]').forEach(function (el) {
            var key = el.getAttribute('data-i18n');
            if (key) { el.textContent = t(key); }
        });
        // data-i18n-placeholder / -title / -aria
        root.querySelectorAll('[data-i18n-placeholder]').forEach(function (el) {
            var key = el.getAttribute('data-i18n-placeholder');
            if (key) { el.setAttribute('placeholder', t(key)); }
        });
        root.querySelectorAll('[data-i18n-title]').forEach(function (el) {
            var key = el.getAttribute('data-i18n-title');
            if (key) { el.setAttribute('title', t(key)); }
        });
        root.querySelectorAll('[data-i18n-aria]').forEach(function (el) {
            var key = el.getAttribute('data-i18n-aria');
            if (key) { el.setAttribute('aria-label', t(key)); }
        });
    }

    function translateTextNode(node) {
        var raw = node.nodeValue;
        if (!raw) { return; }
        var text = raw.trim();
        if (!text || text.length > MAX_TEXT_LENGTH) { return; }
        if (!/[a-zA-Z\u00C0-\u024F]/.test(text)) { return; } // skip numbers/symbols
        var translated = lookup(text);
        if (translated === text) { return; }
        // Preserve surrounding whitespace exactly as the template had it.
        node.nodeValue = raw.replace(text, translated);
    }

    function applyDOM(root) {
        if (state.locale === 'en') { return; } // source language: nothing to do
        root = root || document;
        if (root.nodeType !== Node.ELEMENT_NODE) { return; }

        if (isSkippable(root)) { return; }

        applyElementTranslations(root);

        // Exact-match text nodes (whole string equals an English source string).
        var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
            acceptNode: function (node) {
                if (!node.nodeValue || !node.nodeValue.trim()) {
                    return NodeFilter.FILTER_REJECT;
                }
                var parent = node.parentElement;
                if (!parent) { return NodeFilter.FILTER_REJECT; }
                if (parent.closest && parent.closest(SKIP_SELECTOR)) {
                    return NodeFilter.FILTER_REJECT;
                }
                return NodeFilter.FILTER_ACCEPT;
            }
        });
        var nodes = [];
        while (walker.nextNode()) { nodes.push(walker.currentNode); }
        nodes.forEach(translateTextNode);

        if (root.hasAttribute && root.hasAttribute('data-i18n-applied')) { return; }
    }

    /* ------------------------------------------------------------------ *
     * MutationObserver - localize dynamically injected content
     * ------------------------------------------------------------------ */

    var pending = null;
    function scheduleApply(root) {
        if (state.locale === 'en') { return; }
        if (pending) { pending.push(root); return; }
        pending = [root];
        setTimeout(function () {
            var roots = pending;
            pending = null;
            roots.forEach(function (r) {
                try { applyDOM(r); } catch (e) { /* never break rendering */ }
            });
        }, 0);
    }

    function startObserver() {
        if (state.observer || !window.MutationObserver) { return; }
        state.observer = new MutationObserver(function (mutations) {
            for (var i = 0; i < mutations.length; i++) {
                var m = mutations[i];
                if (m.type === 'childList') {
                    for (var j = 0; j < m.addedNodes.length; j++) {
                        var node = m.addedNodes[j];
                        if (node.nodeType === Node.ELEMENT_NODE) {
                            scheduleApply(node);
                        } else if (node.nodeType === Node.TEXT_NODE) {
                            scheduleApply(node.parentElement || document.body);
                        }
                    }
                } else if (m.type === 'characterData' && m.target.parentElement) {
                    scheduleApply(m.target.parentElement);
                }
            }
        });
        state.observer.observe(document.documentElement, {
            childList: true, subtree: true, characterData: false
        });
    }

    /* ------------------------------------------------------------------ *
     * Server catalog loading
     * ------------------------------------------------------------------ */

    function fetchServerCatalog(locale) {
        // Public read-only catalog endpoint (works for every role).
        return fetch('/api/i18n/catalog?locale=' + encodeURIComponent(locale), {
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
            credentials: 'same-origin'
        })
            .then(function (r) { return r.ok ? r.json() : {}; })
            .then(function (data) {
                if (data && data.success && data.translations && Object.keys(data.translations).length) {
                    return data.translations;
                }
                // Fallback to the legacy admin endpoint for older deployments.
                return fetch('/api/translations?locale=' + encodeURIComponent(locale), {
                    headers: { 'X-Requested-With': 'XMLHttpRequest' },
                    credentials: 'same-origin'
                })
                    .then(function (r2) { return r2.ok ? r2.json() : {}; })
                    .then(function (d2) {
                        return (d2 && d2.success && d2.translations) ? d2.translations : {};
                    });
            })
            .catch(function () { return {}; });
    }

    function collectPageTranslations() {
        var el = document.getElementById('page-translations');
        if (!el) { return {}; }
        try { return JSON.parse(el.textContent || '{}'); } catch (e) { return {}; }
    }

    function loadLocale(locale) {
        var dict = dictFor(locale);
        // 1. Inline page translations from the template.
        addTranslations(locale, collectPageTranslations());
        // 2. Static UI pack (registered by /static/js/i18n/locales/<locale>.js).
        // 3. Full server catalog.
        return fetchServerCatalog(locale).then(function (server) {
            addTranslations(locale, server);
            // Publish merged dictionary on the globals other modules read.
            window.translations = window.translations || {};
            window.appTranslations = window.appTranslations || {};
            if (locale === state.locale) {
                Object.keys(dict).forEach(function (k) {
                    if (!(k in window.translations)) { window.translations[k] = dict[k]; }
                    window.appTranslations[k] = dict[k];
                });
            }
            return dict;
        });
    }

    /* ------------------------------------------------------------------ *
     * Public API
     * ------------------------------------------------------------------ */

    var I18N = {
        t: t,
        translate: t,
        addTranslations: addTranslations,
        getLocale: function () { return state.locale; },
        isRTL: function (locale) {
            return RTL_LANGUAGES.indexOf(locale || state.locale) !== -1;
        },
        /** Apply the DOM translation pass (also invoked automatically). */
        applyDOM: function (root) {
            try { applyDOM(root || document); } catch (e) { /* no-op */ }
        },
        /** Switch the active dictionary and re-translate the page live. */
        setLocale: function (locale) {
            if (!locale) { return Promise.resolve(); }
            state.locale = locale;
            window.currentLocale = locale;
            try { sessionStorage.setItem('userLanguage', locale); } catch (e) { /* ignore */ }
            return loadLocale(locale).then(function () {
                applyDOM(document);
                document.dispatchEvent(new CustomEvent('i18n:localeApplied', {
                    detail: { locale: locale }
                }));
            });
        }
    };

    window.I18N = I18N;

    // ------------------------------------------------------------------
    // Global compatibility contract (used by MessageSystem, page modules…)
    // ------------------------------------------------------------------
    window.currentLocale = state.locale;
    window.translations = window.translations || {};
    window.appTranslations = window.appTranslations || {};

    function TranslationHelper() {}
    TranslationHelper.prototype.translate = function (key, params) { return t(key, params); };
    TranslationHelper.prototype.getCategoryTranslation = function (category) {
        return t(category.charAt(0).toUpperCase() + category.slice(1));
    };
    TranslationHelper.prototype.updateTranslations = function (map) { addTranslations(state.locale, map); };
    TranslationHelper.prototype.getAllTranslations = function () { return state.dictionaries[state.locale] || {}; };
    window.TranslationHelper = new TranslationHelper();
    window.t = t;
    window.translate = t;

    // ------------------------------------------------------------------
    // Boot: register static packs, load the current locale, translate DOM
    // ------------------------------------------------------------------
    function boot() {
        var packs = window.I18N_UI_PACKS || {};
        Object.keys(packs).forEach(function (loc) {
            addTranslations(loc, packs[loc]);
        });
        loadLocale(state.locale).then(function () {
            state.ready = true;
            applyDOM(document);
            startObserver();
            window.dispatchEvent(new CustomEvent('translationsLoaded', {
                detail: { locale: state.locale, translations: window.translations }
            }));
        });
    }

    // Listen for live language changes coming from the switcher.
    document.addEventListener('languageChanged', function (e) {
        if (e.detail && e.detail.locale) {
            I18N.setLocale(e.detail.locale);
        }
    });

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
})();
