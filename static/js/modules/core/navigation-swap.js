/**
 * Static sidebar navigation (owner requirement: "the sidebar must stay
 * fixed without refreshing").
 *
 * The application is server-rendered: every navigation used to redraw the
 * whole page, sidebar included. This module intercepts in-application link
 * clicks and back/forward navigation, fetches the target page, and swaps
 * ONLY the main content area - the sidebar element is never replaced, so
 * it keeps its scroll position, its collapsed/expanded state and its focus
 * between navigations. The sidebar's open/closed state is also persisted
 * per session and restored on a real load (first visit, full reload,
 * fallback), so even then it does not visibly reset.
 *
 * Discipline:
 * - Only same-origin GET navigations of plain links are swapped. Forms,
 *   downloads, new tabs/targets, modified clicks (ctrl/shift/...), external
 *   URLs and opted-out links (data-swap="off") take the normal path.
 * - Page scripts: the page's classic scripts are re-created so their
 *   functions exist for the new content's declarative handlers; the module
 *   scripts the app ships re-run their per-page init through the universal
 *   initializer (ES modules are cached, so re-appending them is a no-op -
 *   initializePage() is invoked explicitly instead).
 * - Failure handling: navigation errors are rendered inside the main content
 *   with a retry action. Network failures and malformed page responses never
 *   force a full-page refresh; only an authentication redirect leaves the shell.
 */

(function () {
    'use strict';

    const MAIN_ID = 'mainContent';
    const STATE_KEY = 'syltherae.sidebar-state';
    const PREFETCH_TTL_MS = 45000;
    const PREFETCH_MAX = 24;
    // Styles/scripts injected for the current soft-nav destination. Cleared
    // on the next swap so page-specific CSS (ingestion, select2, unified-table)
    // does not leak across routes — and missing assets are loaded when the
    // first full load never included them (e.g. Dashboard → Keywords).
    const PAGE_ASSET_ATTR = 'data-soft-nav-asset';

    // html[data-nav-swap] drives a thin top progress bar and a light dim of
    // the current main pane. The previous page stays visible until the next
    // main content is ready — no full whiteout and no "Loading..." banner.
    const prefetchCache = new Map(); // href -> { html, at } | Promise
    let inFlight = null;
    let pendingPath = null;

    /** Stable deploy stamp from <meta name="asset-version"> plus per-swap nonce. */
    function assetCacheBust() {
        var meta = document.querySelector('meta[name="asset-version"]');
        var ver = (meta && meta.getAttribute('content')) || '1';
        return encodeURIComponent(ver) + '-' + String(Date.now());
    }

    // ---------------------------------------------------------------- state

    function persistSidebarState() {
        const sidebar = document.getElementById('sidebar');
        if (!sidebar) return;
        try {
            sessionStorage.setItem(STATE_KEY, JSON.stringify({
                scroll: sidebar.scrollTop,
                open: sidebar.classList.contains('show'),
                at: Date.now()
            }));
        } catch (e) { /* storage unavailable: nothing to restore later */ }
    }

    function restoreSidebarState() {
        const sidebar = document.getElementById('sidebar');
        if (!sidebar) return;
        let state = null;
        try {
            state = JSON.parse(sessionStorage.getItem(STATE_KEY) || 'null');
        } catch (e) { state = null; }
        if (!state) return;
        if (typeof state.scroll === 'number') sidebar.scrollTop = state.scroll;
        const backdrop = document.getElementById('sidebarBackdrop');
        if (state.open && window.innerWidth < 992 && backdrop) {
            sidebar.classList.add('show');
            backdrop.classList.add('show');
        }
    }

    // ------------------------------------------------------------- helpers

    function sameOriginLink(anchor) {
        if (!anchor || anchor.target && anchor.target !== '_self') return null;
        if (anchor.hasAttribute('download')) return null;
        if (anchor.closest('[data-swap="off"]')) return null;
        const href = anchor.getAttribute('href');
        if (!href || href.startsWith('#')) return null;
        let url;
        try {
            url = new URL(anchor.href, window.location.href);
        } catch (e) { return null; }
        if (url.origin !== window.location.origin) return null;
        if (url.protocol !== 'http:' && url.protocol !== 'https:') return null;
        // Auth and setup are standalone shells — never soft-swap into them.
        if (url.pathname.startsWith('/auth/') || url.pathname.startsWith('/setup')) {
            return null;
        }
        return url;
    }

    function normalizeHref(href) {
        try {
            const url = new URL(href, window.location.href);
            return url.pathname + url.search + url.hash;
        } catch (_error) {
            return href;
        }
    }

    function updateSidebarActiveState(pathname) {
        const links = document.querySelectorAll('.sidebar-nav-link:not(.language-switcher .sidebar-nav-link)');
        const path = pathname || window.location.pathname;
        const currentEndpoint = document.body.dataset.currentEndpoint || window.currentEndpoint || '';
        let matched = null;
        let bestLen = -1;

        if (currentEndpoint) {
            for (const link of links) {
                const endpoint = link.getAttribute('data-endpoint');
                if (endpoint && endpoint === currentEndpoint) {
                    matched = link;
                    bestLen = Number.MAX_SAFE_INTEGER;
                    break;
                }
            }
        }

        if (!matched) {
            for (const link of links) {
                const href = link.getAttribute('href');
                if (!href) continue;
                try {
                    const hrefPath = new URL(href, window.location.origin).pathname;
                    const isExact = path === hrefPath;
                    const isNested = hrefPath !== '/' && path.startsWith(hrefPath + '/');
                    if ((isExact || isNested) && hrefPath.length > bestLen) {
                        matched = link;
                        bestLen = hrefPath.length;
                    }
                } catch (_error) {
                    if (path === href && href.length > bestLen) {
                        matched = link;
                        bestLen = href.length;
                    }
                }
            }
        }

        for (const link of links) {
            const on = link === matched;
            link.classList.toggle('active', on);
            if (on) link.setAttribute('aria-current', 'page');
            else link.removeAttribute('aria-current');
        }
    }

    function ensureProgressBar() {
        let bar = document.getElementById('navSwapProgress');
        if (bar) return bar;
        bar = document.createElement('div');
        bar.id = 'navSwapProgress';
        bar.setAttribute('aria-hidden', 'true');
        bar.innerHTML = '<div class="nav-swap-progress-bar"></div>';
        document.body.appendChild(bar);
        return bar;
    }

    function setNavigating(active) {
        document.documentElement.toggleAttribute('data-nav-swap', active);
        const main = document.getElementById(MAIN_ID);
        if (main) {
            if (active) main.setAttribute('aria-busy', 'true');
            else main.removeAttribute('aria-busy');
        }
        ensureProgressBar();
    }

    function clearInlineNavStatus(main) {
        if (!main) return;
        const status = main.querySelector('[data-navigation-swap-status]');
        if (status) status.remove();
    }

    function localizedMessage(key, fallback) {
        try {
            const source = (window.translations && window.translations[key]) || fallback;
            if (typeof window.t !== 'function') return source;
            const translated = window.t(source);
            return translated && translated !== source ? translated : source;
        } catch (_error) {
            return fallback;
        }
    }

    function showNavigationError(main, targetUrl, push) {
        if (!main) return;
        let status = main.querySelector('[data-navigation-swap-status]');
        if (!status) {
            status = document.createElement('div');
            status.setAttribute('data-navigation-swap-status', '');
            main.insertBefore(status, main.firstChild);
        }
        status.className = 'alert alert-danger d-flex flex-wrap align-items-center justify-content-between gap-2';
        status.setAttribute('role', 'alert');
        status.textContent = '';
        const message = document.createElement('span');
        message.textContent = localizedMessage('navigationLoadFailed', 'Error loading data');
        status.appendChild(message);
        const retry = document.createElement('button');
        retry.type = 'button';
        retry.className = 'btn btn-sm btn-outline-danger';
        retry.textContent = localizedMessage('retry', 'Retry');
        retry.addEventListener('click', function () {
            navigateTo(targetUrl, Boolean(push), false);
        }, { once: true });
        status.appendChild(retry);
    }

    function rememberPrefetch(href, html) {
        const key = normalizeHref(href);
        if (prefetchCache.size >= PREFETCH_MAX) {
            const oldest = prefetchCache.keys().next().value;
            prefetchCache.delete(oldest);
        }
        prefetchCache.set(key, { html: html, at: Date.now() });
    }

    function takePrefetch(href) {
        const key = normalizeHref(href);
        const entry = prefetchCache.get(key);
        if (!entry || typeof entry.then === 'function') return null;
        if (Date.now() - entry.at > PREFETCH_TTL_MS) {
            prefetchCache.delete(key);
            return null;
        }
        prefetchCache.delete(key);
        return entry.html;
    }

    function prefetch(href) {
        let url;
        try {
            url = new URL(href, window.location.href);
        } catch (_error) {
            return;
        }
        if (url.origin !== window.location.origin) return;
        if (url.pathname.startsWith('/auth/') || url.pathname.startsWith('/setup')) return;
        const key = normalizeHref(url.href);
        if (key === normalizeHref(window.location.href)) return;
        if (prefetchCache.has(key)) return;
        const request = fetch(url.href, {
            headers: { 'X-Navigate': 'swap', 'Accept': 'text/html' },
            credentials: 'same-origin',
            redirect: 'follow'
        }).then(function (response) {
            if (!response.ok) throw new Error('HTTP ' + response.status);
            return response.text();
        }).then(function (html) {
            rememberPrefetch(url.href, html);
            return html;
        }).catch(function () {
            prefetchCache.delete(key);
        });
        prefetchCache.set(key, request);
    }

    function onPointerPrefetch(event) {
        const target = event.target && event.target.nodeType === 1
            ? event.target : (event.target && event.target.parentElement);
        const anchor = target && target.closest ? target.closest('a') : null;
        const url = sameOriginLink(anchor);
        if (!url) return;
        prefetch(url.href);
    }

    function syncSidebarFromDocument(newDoc) {
        const currentSidebar = document.getElementById('sidebar');
        const nextSidebar = newDoc.getElementById('sidebar');
        if (!currentSidebar || !nextSidebar) return false;

        // Keep the shell, scroll position, mobile-open state and any listeners
        // on #sidebar itself. Only synchronize its content after an explicit
        // settings change (ordinary page swaps never touch the sidebar).
        ['.sidebar-logo', 'nav'].forEach((selector) => {
            const current = currentSidebar.querySelector(selector);
            const next = nextSidebar.querySelector(selector);
            if (current && next) {
                const imported = document.importNode(next, true);
                current.replaceWith(imported);
            }
        });
        updateSidebarActiveState();
        return true;
    }

    function translatedLabel(value) {
        if (typeof value !== 'string') return '';
        try {
            return typeof window.t === 'function' ? (window.t(value) || value) : value;
        } catch (_error) {
            return value;
        }
    }

    function syncSidebarFromEntries(entries) {
        const sidebar = document.getElementById('sidebar');
        const nav = sidebar && sidebar.querySelector('nav');
        if (!nav || !Array.isArray(entries)) return false;

        const list = document.createElement('ul');
        list.className = 'sidebar-nav';
        let currentDomain = null;
        for (const entry of entries) {
            if (!entry || entry.hidden) continue;
            if (entry.domain !== currentDomain) {
                currentDomain = entry.domain;
                const section = document.createElement('li');
                section.className = 'sidebar-nav-item';
                const label = document.createElement('span');
                label.className = 'sidebar-section-label';
                label.textContent = translatedLabel(entry.domain_label || entry.domain || '');
                section.appendChild(label);
                list.appendChild(section);
            }

            let destination;
            try {
                destination = new URL(entry.url, window.location.origin);
            } catch (_error) {
                continue;
            }
            if (destination.origin !== window.location.origin
                || !['http:', 'https:'].includes(destination.protocol)) continue;

            const item = document.createElement('li');
            item.className = 'sidebar-nav-item';
            const link = document.createElement('a');
            link.setAttribute('href', destination.pathname + destination.search + destination.hash);
            link.className = 'sidebar-nav-link';
            link.setAttribute('data-interface', entry.interface_id || '');
            if (entry.route) link.setAttribute('data-endpoint', entry.route);
            if (entry.description) {
                link.title = translatedLabel(entry.description)
                    + (entry.note ? ' — ' + translatedLabel(entry.note) : '');
            }
            if (entry.shortcut) link.setAttribute('data-shortcut', entry.shortcut);
            const icon = document.createElement('i');
            icon.className = 'bi ' + (entry.icon || 'bi-circle');
            icon.setAttribute('aria-hidden', 'true');
            link.appendChild(icon);
            const text = document.createElement('span');
            text.textContent = translatedLabel(entry.label || entry.interface_id || '');
            link.appendChild(text);
            if (entry.badge) {
                const badge = document.createElement('span');
                badge.className = 'sidebar-nav-badge';
                badge.textContent = translatedLabel(entry.badge);
                link.appendChild(badge);
            }
            item.appendChild(link);
            list.appendChild(item);
        }
        nav.replaceChildren(list);
        updateSidebarActiveState();
        return true;
    }

    function normalizeAssetPath(href) {
        if (!href) return '';
        try {
            return new URL(href, window.location.href).pathname;
        } catch (_error) {
            return String(href).split('?')[0];
        }
    }

    function isShellScriptSrc(src) {
        const path = normalizeAssetPath(src);
        if (!path) return false;
        // Scripts that belong to base.html — never re-inject on soft nav.
        return /\/static\/js\/(?:jquery|bootstrap|chart\.umd|jspdf|user-menu|responsive-fixes)/.test(path)
            || /\/static\/js\/modules\/core\//.test(path)
            || /\/static\/js\/modules\/(?:charts|messages|rendering)\//.test(path)
            || /\/static\/js\/modules\/ui\/(?:file-actions|documents-panel|table-export)\.js/.test(path)
            || /\/static\/js\/pages\/(?:base-page|universal-initializer)/.test(path)
            || /\/static\/js\/(?:theme-|translations-)/.test(path)
            || /\/static\/js\/i18n\//.test(path);
    }

    function isPageOwnedScript(script, newDoc) {
        // Soft navigation must not re-run the shell (jQuery, Bootstrap,
        // language persistence, this module, …). Only the destination page's
        // own payloads and modules belong here: anything inside #mainContent,
        // JSON page-data islands, or scripts under /static/js/pages|settings/,
        // plus classic helpers declared in {% block extra_js %} (unified-table,
        // select2, …).
        const main = newDoc.getElementById(MAIN_ID);
        if (main && main.contains(script)) return true;
        const type = (script.getAttribute('type') || '').toLowerCase();
        const id = script.id || '';
        if (type === 'application/json' && id) return true;
        const src = script.getAttribute('src') || '';
        if (!src) {
            // Inline page scripts in the body (outside head) are page-owned.
            return !!(newDoc.body && newDoc.body.contains(script) && !newDoc.head.contains(script));
        }
        if (isShellScriptSrc(src)) return false;
        if (/\/static\/js\/(?:pages|settings)\//.test(src)) return true;
        // extra_js classic helpers: present in the fetched body, not the shell.
        if (newDoc.body && newDoc.body.contains(script) && !newDoc.head.contains(script)) {
            return /\/static\/(?:js|dist)\//.test(src);
        }
        return false;
    }

    function syncPageStyles(newDoc) {
        document.querySelectorAll('link[' + PAGE_ASSET_ATTR + '], style[' + PAGE_ASSET_ATTR + ']')
            .forEach((el) => el.remove());

        const have = new Set(
            Array.from(document.querySelectorAll('link[rel="stylesheet"]'))
                .map((link) => normalizeAssetPath(link.href))
                .filter(Boolean)
        );

        Array.from(newDoc.querySelectorAll('link[rel="stylesheet"]')).forEach((link) => {
            const href = link.getAttribute('href');
            if (!href) return;
            const path = normalizeAssetPath(href);
            if (!path || have.has(path)) return;
            const el = document.createElement('link');
            el.rel = 'stylesheet';
            el.href = href;
            if (link.media) el.media = link.media;
            el.setAttribute(PAGE_ASSET_ATTR, '1');
            document.head.appendChild(el);
            have.add(path);
        });

        // Only copy inline <style> tags that live in head as page extras —
        // base.html does not put critical CSS there as naked style blocks
        // beyond what is already present on first paint.
        Array.from(newDoc.head ? newDoc.head.querySelectorAll('style') : []).forEach((style) => {
            const text = (style.textContent || '').trim();
            if (!text) return;
            const el = document.createElement('style');
            el.textContent = style.textContent;
            el.setAttribute(PAGE_ASSET_ATTR, '1');
            document.head.appendChild(el);
        });
    }

    function ensurePageClassicScripts(newDoc) {
        // Load shared classic helpers once. Re-executing them on every swap
        // would rebuild globals like UnifiedTable and drop handlers. Page and
        // settings scripts are the exception: they only wire their own DOM, so
        // they must re-run (cache-busted) whenever that page is swapped in, and
        // must therefore not declare top-level let/const/class.
        const REINIT_CLASSIC = /\/static\/js\/(?:(?:pages|settings)\/[^/]+|modules\/core\/operations-widget)\.js$/;
        const have = new Set(
            Array.from(document.querySelectorAll('script[src]'))
                .map((script) => normalizeAssetPath(script.src))
                .filter(Boolean)
        );
        const scripts = Array.from(newDoc.querySelectorAll('script[src]'))
            .filter((old) => !newDoc.head.contains(old))
            .filter((old) => {
                const type = (old.getAttribute('type') || '').toLowerCase();
                return type !== 'module' && type !== 'application/json';
            })
            .filter((old) => isPageOwnedScript(old, newDoc));

        const bust = assetCacheBust();
        return Promise.all(scripts.map(function (old) {
            const src = old.getAttribute('src');
            const path = normalizeAssetPath(src);
            if (!path) return Promise.resolve();
            const needsReinit = REINIT_CLASSIC.test(path);
            if (have.has(path) && !needsReinit) return Promise.resolve();
            if (!needsReinit) have.add(path);
            if (needsReinit) {
                document.querySelectorAll('script[' + PAGE_ASSET_ATTR + ']').forEach(function (el) {
                    if (normalizeAssetPath(el.src) === path) el.remove();
                });
            }
            return new Promise(function (resolve) {
                const script = document.createElement('script');
                let url = src;
                try {
                    const parsed = new URL(src, window.location.href);
                    parsed.searchParams.set('soft', bust);
                    url = parsed.pathname + parsed.search;
                } catch (_error) {
                    url = src + (src.indexOf('?') >= 0 ? '&' : '?') + 'soft=' + bust;
                }
                script.src = url;
                script.setAttribute(PAGE_ASSET_ATTR, '1');
                for (const attr of old.attributes) {
                    if (attr.name === 'src' || attr.name === 'defer' || attr.name === 'async') continue;
                    script.setAttribute(attr.name, attr.value);
                }
                script.onload = function () { resolve(); };
                script.onerror = function () { resolve(); };
                document.body.appendChild(script);
            });
        }));
    }

    function runInlineClassicPageScripts(newDoc) {
        const scripts = Array.from(newDoc.querySelectorAll('script'))
            .filter((old) => !newDoc.head.contains(old))
            .filter((old) => isPageOwnedScript(old, newDoc))
            .filter((old) => {
                const type = (old.getAttribute('type') || '').toLowerCase();
                const src = old.getAttribute('src') || '';
                return !src && type !== 'module' && type !== 'application/json';
            });
        for (const old of scripts) {
            const script = document.createElement('script');
            for (const attr of old.attributes) {
                script.setAttribute(attr.name, attr.value);
            }
            script.text = old.textContent;
            document.body.appendChild(script);
        }
    }

    function reinitPageWidgets() {
        const main = document.getElementById(MAIN_ID);
        try {
            if (window.UnifiedTable && typeof window.UnifiedTable.init === 'function') {
                window.UnifiedTable.init(main || document);
            }
        } catch (error) {
            console.warn('Soft navigation: UnifiedTable init failed', error);
        }
        try {
            if (typeof window.initUnifiedPaginationControls === 'function') {
                window.initUnifiedPaginationControls(main || document);
            }
        } catch (error) {
            console.warn('Soft navigation: pagination controls init failed', error);
        }
        try {
            if (window.bootstrap && main) {
                main.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(function (el) {
                    window.bootstrap.Tooltip.getOrCreateInstance(el);
                });
                main.querySelectorAll('[data-bs-toggle="popover"]').forEach(function (el) {
                    window.bootstrap.Popover.getOrCreateInstance(el);
                });
            }
        } catch (error) {
            console.warn('Soft navigation: Bootstrap widget init failed', error);
        }
        try {
            document.dispatchEvent(new CustomEvent('syltharae:soft-nav', {
                detail: {
                    path: window.location.pathname,
                    endpoint: document.body.dataset.currentEndpoint || ''
                }
            }));
        } catch (_error) { /* CustomEvent unavailable */ }
    }

    function syncPageDataScripts(newDoc) {
        // Page-data islands often live in {% block extra_js %}, outside
        // #mainContent, so the content swap alone does not bring them in.
        // Replace any prior island with the same id before modules re-read it.
        // Also drop soft-nav copies left by earlier routes — a generic id like
        // `page-data` otherwise shadows the freshly swapped island forever.
        document.querySelectorAll('script[type="application/json"][' + PAGE_ASSET_ATTR + ']')
            .forEach(function (el) { el.remove(); });

        const islands = Array.from(newDoc.querySelectorAll('script[type="application/json"][id]'))
            .filter((old) => !newDoc.head.contains(old))
            .filter((old) => isPageOwnedScript(old, newDoc));
        const main = document.getElementById(MAIN_ID);
        for (const old of islands) {
            const id = old.id;
            if (!id) continue;
            // Prefer the copy that arrived inside the swapped main pane.
            const inMain = main ? main.querySelector('#' + CSS.escape(id)) : null;
            document.querySelectorAll('script[type="application/json"]#' + CSS.escape(id))
                .forEach(function (el) {
                    if (inMain && el === inMain) return;
                    if (main && main.contains(el)) return;
                    el.remove();
                });
            if (inMain) continue;
            const script = document.createElement('script');
            script.type = 'application/json';
            script.id = id;
            script.textContent = old.textContent || '';
            script.setAttribute(PAGE_ASSET_ATTR, '1');
            document.body.appendChild(script);
        }
    }

    function importUnregisteredPageModules(newDoc, options) {
        // Modules registered in universal-initializer are loaded there with a
        // cache-buster. Everything else (users, monitoring, reports, settings,
        // …) only appears as a <script type="module"> in the fetched page —
        // re-appending those tags is a no-op because of the module cache, so
        // import() them with ?swap= so their top-level init runs against the
        // freshly swapped DOM.
        //
        // Registered destinations (archives, dashboard, …) still need companion
        // modules (e.g. file-management-system.js). skipPath names the module
        // the initializer already imported, so its handler is not run twice;
        // template-owned pages (keyword/word/source/side detail) have none and
        // their own module is imported here.
        const skipPath = options && options.skipPath;
        const modules = Array.from(newDoc.querySelectorAll('script[type="module"][src]'))
            .filter((old) => !newDoc.head.contains(old))
            .filter((old) => isPageOwnedScript(old, newDoc))
            .map((old) => old.getAttribute('src'))
            .filter(Boolean)
            .filter(function (src) {
                if (!skipPath) return true;
                try {
                    return new URL(src, window.location.href).pathname !== skipPath;
                } catch (_error) {
                    return true;
                }
            });
        const bust = assetCacheBust();
        return Promise.all(modules.map(function (src) {
            let url;
            try {
                url = new URL(src, window.location.href);
            } catch (_error) {
                return null;
            }
            if (url.origin !== window.location.origin) return null;
            url.searchParams.set('swap', bust);
            return import(url.pathname + url.search)
                .then(function (mod) {
                    if (!mod) return;
                    const def = mod.default;
                    if (typeof def === 'function') {
                        // ES class constructors are typeof 'function' but must
                        // not be called without `new` (CursorPaginator,
                        // LanguagePersistence, RTLManager, …).
                        var isClass = false;
                        try {
                            isClass = /^class\b/.test(Function.prototype.toString.call(def));
                        } catch (_e) {
                            isClass = false;
                        }
                        if (!isClass) return def();
                    }
                    if (def && typeof def.init === 'function') {
                        return def.init();
                    }
                    // Self-running modules (top-level init()) need no call.
                })
                .catch(function (error) {
                    console.error('Soft navigation failed to load page module', src, error);
                });
        }));
    }

    function runPageScripts(newDoc) {
        syncPageStyles(newDoc);
        syncPageDataScripts(newDoc);
        return ensurePageClassicScripts(newDoc)
            .then(function () {
                runInlineClassicPageScripts(newDoc);
                return import('/static/js/pages/universal-initializer.js');
            })
            .then(function (mod) {
                if (!mod || typeof mod.initializePage !== 'function') {
                    return importUnregisteredPageModules(newDoc);
                }
                // Tell the initializer this visit arrived by swap: a
                // self-running page module must re-import fresh, not
                // reuse its already-run cached instance. Includes the
                // deploy asset-version so soft-nav always picks up new
                // DocumentActions / analysis modules after a release.
                window.__swapReinit = assetCacheBust();
                return mod.initializePage()
                    .then(function () {
                        const detect = typeof mod.detectPageType === 'function'
                            ? mod.detectPageType
                            : (mod.default && typeof mod.default.detectPageType === 'function'
                                ? mod.default.detectPageType
                                : null);
                        const pageType = detect ? detect() : 'default';
                        // Unregistered destinations need their /js/pages handler.
                        // Registered destinations still need companion modules
                        // outside /js/pages (Archives → file-management-system.js).
                        const ownedPath = typeof mod.handlerModulePath === 'function'
                            ? mod.handlerModulePath(pageType)
                            : null;
                        return importUnregisteredPageModules(newDoc, { skipPath: ownedPath });
                    })
                    .finally(function () {
                        delete window.__swapReinit;
                    });
            })
            .catch(function () {
                return importUnregisteredPageModules(newDoc);
            })
            .then(function () {
                reinitPageWidgets();
            });
    }

    function swapFromResponse(url, html, push, refreshSidebar) {
        const parsed = new DOMParser().parseFromString(html, 'text/html');
        const nextMain = parsed.getElementById(MAIN_ID);
        const currentMain = document.getElementById(MAIN_ID);
        if (!nextMain || !currentMain) return false;

        // Ordinary route changes leave the sidebar subtree alone. Settings
        // changes may opt in to synchronizing its content, while retaining
        // the outer sidebar node and its scroll/open state.
        if (refreshSidebar) syncSidebarFromDocument(parsed);
        clearInlineNavStatus(currentMain);
        const imported = document.importNode(nextMain, true);
        const applySwap = function () {
            currentMain.replaceChildren(...imported.childNodes);
            finishSwapFromResponse(url, parsed, push, refreshSidebar, currentMain);
        };
        import('/static/js/modules/core/page-lifecycle.js')
            .then(function (lifecycle) {
                return lifecycle.deactivateActivePage ? lifecycle.deactivateActivePage() : null;
            })
            .catch(function () { /* first visit / offline module */ })
            .then(function () {
                try {
                    document.dispatchEvent(new CustomEvent('syltharae:soft-nav:before', {
                        detail: { path: window.location.pathname },
                    }));
                } catch (_e) { /* */ }
                applySwap();
            });
        return true;
    }

    function finishSwapFromResponse(url, parsed, push, refreshSidebar, currentMain) {

        // The <body> shell is never replaced on soft nav. Copy the destination
        // endpoint / page markers onto it so base-page handlers do not keep
        // highlighting Dashboard from the previous full load.
        syncShellDocumentState(parsed);

        document.title = parsed.title || document.title;
        let destinationPath = window.location.pathname;
        try {
            destinationPath = new URL(url, window.location.href).pathname;
        } catch (_error) { /* keep current */ }
        updateSidebarActiveState(destinationPath);
        if (push) {
            try { window.history.pushState({ swap: true }, '', url); } catch (e) { /* */ }
        }
        persistSidebarState();
        pendingPath = null;
        setNavigating(false);

        runPageScripts(parsed);
        // Keep the viewport on the page chrome; only reset the main pane.
        try {
            currentMain.scrollTop = 0;
            window.scrollTo({ top: 0, left: 0, behavior: 'instant' in window ? 'instant' : 'auto' });
        } catch (_error) {
            window.scrollTo(0, 0);
        }
        return true;
    }

    function syncShellDocumentState(newDoc) {
        const nextBody = newDoc.body;
        if (nextBody) {
            ['data-current-endpoint', 'data-endpoint', 'data-page-type'].forEach((attr) => {
                const value = nextBody.getAttribute(attr);
                if (value == null || value === '') document.body.removeAttribute(attr);
                else document.body.setAttribute(attr, value);
            });
            if (nextBody.dataset.currentEndpoint != null) {
                window.currentEndpoint = nextBody.dataset.currentEndpoint;
            }
        }
        const nextBaseData = newDoc.getElementById('base-page-data');
        if (nextBaseData) {
            const current = document.getElementById('base-page-data');
            if (current) current.textContent = nextBaseData.textContent || '';
            else {
                const script = document.createElement('script');
                script.type = 'application/json';
                script.id = 'base-page-data';
                script.textContent = nextBaseData.textContent || '';
                document.body.appendChild(script);
            }
            try {
                const parsed = JSON.parse(nextBaseData.textContent || '{}');
                if (parsed && parsed.current_endpoint != null) {
                    window.currentEndpoint = parsed.current_endpoint;
                    document.body.setAttribute('data-current-endpoint', parsed.current_endpoint);
                }
            } catch (_error) { /* ignore malformed island */ }
        }
        // lang/dir may change with language switches mid-session.
        if (newDoc.documentElement) {
            const lang = newDoc.documentElement.getAttribute('lang');
            const dir = newDoc.documentElement.getAttribute('dir');
            if (lang) document.documentElement.setAttribute('lang', lang);
            if (dir) document.documentElement.setAttribute('dir', dir);
        }
    }

    async function fetchPageHtml(url, signal) {
        const cached = takePrefetch(url);
        if (cached) return cached;
        const response = await fetch(url, {
            headers: { 'X-Navigate': 'swap', 'Accept': 'text/html' },
            credentials: 'same-origin',
            redirect: 'follow',
            signal: signal
        });
        if (!response.ok) throw new Error('HTTP ' + response.status);
        const html = await response.text();
        // Stash a copy for rapid back/forward / revisit within the TTL.
        rememberPrefetch(response.url || url, html);
        return { html: html, finalUrl: response.url || url };
    }

    async function navigateTo(url, push, refreshSidebar) {
        if (inFlight) inFlight.abort();
        const controller = new AbortController();
        inFlight = controller;
        const main = document.getElementById(MAIN_ID);
        const previousHref = window.location.href;
        let destination;
        try {
            destination = new URL(url, window.location.href);
        } catch (_error) {
            destination = null;
        }

        // Optimistic chrome: highlight the destination and show the thin
        // progress bar while the previous page stays on screen.
        pendingPath = destination ? destination.pathname : null;
        if (pendingPath) updateSidebarActiveState(pendingPath);
        if (push && destination) {
            try { window.history.pushState({ swap: true, pending: true }, '', destination.href); }
            catch (_error) { /* history unavailable */ }
        }
        setNavigating(true);
        clearInlineNavStatus(main);

        try {
            const payload = await fetchPageHtml(destination ? destination.href : url, controller.signal);
            if (controller.signal.aborted) return false;
            const html = typeof payload === 'string' ? payload : payload.html;
            const finalUrl = typeof payload === 'string'
                ? (destination ? destination.href : url)
                : (payload.finalUrl || url);
            if (push && destination && normalizeHref(finalUrl) !== normalizeHref(destination.href)) {
                try { window.history.replaceState({ swap: true }, '', finalUrl); }
                catch (_error) { /* */ }
            }
            // URL already updated optimistically when push=true.
            if (!swapFromResponse(finalUrl, html, false, refreshSidebar)) {
                let authRedirect = false;
                try {
                    authRedirect = new URL(finalUrl, window.location.href).pathname.startsWith('/auth/');
                } catch (_error) { authRedirect = false; }
                if (authRedirect) {
                    window.location.href = finalUrl;
                    return false;
                }
                throw new Error('The destination did not contain the application shell');
            }
            return true;
        } catch (error) {
            if (controller.signal.aborted || error.name === 'AbortError') return false;
            pendingPath = null;
            setNavigating(false);
            updateSidebarActiveState();
            if (push) {
                try { window.history.replaceState({ swap: true }, '', previousHref); }
                catch (_error) { /* */ }
            }
            if (main) showNavigationError(main, url, push);
            return false;
        } finally {
            if (inFlight === controller) inFlight = null;
        }
    }

    async function refreshSidebarNavigation() {
        try {
            const response = await fetch('/api/preferences/navigation', {
                headers: { 'Accept': 'application/json' },
                credentials: 'same-origin'
            });
            if (!response.ok) return false;
            const data = await response.json();
            if (!data || data.success !== true) return false;
            return syncSidebarFromEntries(data.entries);
        } catch (_error) {
            return false;
        }
    }

    async function refreshSidebarBranding() {
        try {
            const response = await fetch(window.location.href, {
                headers: { 'X-Navigate': 'swap' },
                credentials: 'same-origin',
                redirect: 'follow'
            });
            if (!response.ok) return false;
            const parsed = new DOMParser().parseFromString(await response.text(), 'text/html');
            const current = document.querySelector('#sidebar .sidebar-logo');
            const next = parsed.querySelector('#sidebar .sidebar-logo');
            if (!current || !next) return false;
            current.replaceWith(document.importNode(next, true));
            return true;
        } catch (_error) {
            return false;
        }
    }

    function onClick(event) {
        if (event.defaultPrevented || event.button !== 0) return;
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        const target = event.target && event.target.nodeType === 1
            ? event.target : (event.target && event.target.parentElement);
        const anchor = target && target.closest ? target.closest('a') : null;
        if (!anchor) return;
        const url = sameOriginLink(anchor);
        if (!url) return;
        // Same-page hash-only changes stay native.
        if (url.pathname === window.location.pathname
            && url.search === window.location.search
            && url.hash && url.hash !== window.location.hash) {
            return;
        }
        if (normalizeHref(url.href) === normalizeHref(window.location.href)) {
            event.preventDefault();
            return;
        }
        event.preventDefault();
        persistSidebarState();
        navigateTo(url.href, true, false);
    }

    function onPopState() {
        persistSidebarState();
        navigateTo(window.location.href, false, false);
    }

    // Page modules re-run, as fresh instances, on every swap, while the
    // document and window outlive them. Two consequences are handled here:
    //  - DOMContentLoaded fires once per document, so a listener added after
    //    it has fired runs on the next tick, as it would have on a full load;
    //  - a document/window listener a page module adds would pile up with one
    //    copy per visit, so when a newer instance of the same page script adds
    //    the same listener, the stale instance's copy is removed first.
    const PAGE_SCRIPT = /\/static\/js\/(?:pages|settings)\/[^?#:\s)]+(?:\?[^:\s)#]*)?/;

    function pageScriptOwner() {
        const limit = Error.stackTraceLimit;
        Error.stackTraceLimit = 40;
        const stack = String(new Error().stack || '');
        Error.stackTraceLimit = limit;
        const match = PAGE_SCRIPT.exec(stack);
        if (!match) return null;
        const at = match[0].indexOf('?');
        return {
            file: at >= 0 ? match[0].slice(0, at) : match[0],
            instance: at >= 0 ? match[0].slice(at) : '',
        };
    }

    function domReadyFired() {
        if (document.readyState === 'complete') return true;
        const entry = performance.getEntriesByType
            ? performance.getEntriesByType('navigation')[0] : null;
        return !!(entry && entry.domContentLoadedEventStart > 0);
    }

    function runLateDomReady(listener) {
        setTimeout(function () {
            const event = new Event('DOMContentLoaded');
            try {
                if (typeof listener === 'function') listener.call(document, event);
                else if (typeof listener.handleEvent === 'function') listener.handleEvent(event);
            } catch (error) {
                console.error('DOMContentLoaded handler failed after soft navigation', error);
            }
        }, 0);
    }

    const pageListeners = [];

    function scopePageListeners(target) {
        const nativeAdd = target.addEventListener;
        const nativeRemove = target.removeEventListener;
        target.addEventListener = function (type, listener, options) {
            if (!listener) return nativeAdd.call(this, type, listener, options);
            if (target === document && type === 'DOMContentLoaded' && domReadyFired()) {
                runLateDomReady(listener);
                return undefined;
            }
            const owner = pageScriptOwner();
            if (owner) {
                const capture = typeof options === 'boolean' ? options : !!(options && options.capture);
                const key = owner.file + '|' + type + '|' + capture + '|' + String(listener);
                for (let i = pageListeners.length - 1; i >= 0; i -= 1) {
                    const prior = pageListeners[i];
                    if (prior.target === target && prior.key === key && prior.instance !== owner.instance) {
                        nativeRemove.call(target, prior.type, prior.listener, prior.capture);
                        pageListeners.splice(i, 1);
                    }
                }
                pageListeners.push({target, type, listener, capture, key, instance: owner.instance});
            }
            return nativeAdd.call(this, type, listener, options);
        };
    }

    if (!window.__navigationSwapInstalled) {
        window.__navigationSwapInstalled = true;
        scopePageListeners(document);
        scopePageListeners(window);
        document.addEventListener('click', onClick, true);
        document.addEventListener('pointerover', onPointerPrefetch, true);
        window.addEventListener('popstate', onPopState);
        document.addEventListener('DOMContentLoaded', restoreSidebarState);
        window.addEventListener('pagehide', persistSidebarState);
        // All ordinary client-side destinations, including table sort/filter
        // and keyboard navigation, share this one request/lifecycle path.
        window.swapNavigate = function (url) { return navigateTo(url, true, false); };
        window.refreshMainContent = function () {
            return navigateTo(window.location.href, false, false);
        };
        window.refreshSidebarNavigation = refreshSidebarNavigation;
        window.refreshSidebarBranding = refreshSidebarBranding;
        window.refreshApplicationView = function () {
            return navigateTo(window.location.href, false, true);
        };
    }
})();
