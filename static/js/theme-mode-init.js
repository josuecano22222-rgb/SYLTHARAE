/**
 * Paint the right theme before the first frame.
 *
 * Loaded synchronously in <head>, before any stylesheet, so `<html>` already
 * carries `data-theme-mode` when the browser styles the first element. Without
 * it a dark-mode reader sees a white flash on every navigation — the classic
 * "dark mode is broken" report that is really a race.
 *
 * Deliberately tiny and dependency-free: the full module
 * (`modules/ui/theme-mode.js`) owns the menu, the system listener and the
 * event, and runs later.
 *
 * Keep the key in step with `modules/ui/theme-mode.js` (STORAGE_KEY).
 */
(function () {
    'use strict';
    var KEY = 'syltharae-theme-mode';
    var mode = 'auto';
    try {
        var stored = window.localStorage.getItem(KEY);
        if (stored === 'light' || stored === 'dark' || stored === 'auto') mode = stored;
    } catch (error) {
        /* storage unavailable: auto */
    }
    var resolved = mode;
    if (mode === 'auto') {
        resolved = (typeof window.matchMedia === 'function' &&
                    window.matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light';
    }
    var root = document.documentElement;
    root.setAttribute('data-theme-mode', resolved);
    root.setAttribute('data-theme-preference', mode);
    root.style.colorScheme = resolved;
})();
