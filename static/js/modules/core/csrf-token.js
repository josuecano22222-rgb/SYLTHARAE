/**
 * CSRF Token Provider
 * ===================
 * Central helper that always returns a usable CSRF token, even when the
 * token rendered into the page (meta tag) has gone stale - e.g. after a
 * re-login rotated the session (establish_session() clears the session,
 * invalidating any token minted for the previous one), after a logout in
 * another tab, or on pages left open across session expiry.
 *
 * The refresh endpoint (/api/csrf-token) is public and CSRF-exempt by
 * design, so recovery never needs a valid token to bootstrap.
 *
 * Usage:
 *   const token = window.CSRF.getToken();          // cached token
 *   const token = await window.CSRF.refreshToken();// fetch a fresh one
 *
 * This module only changes how the front end obtains tokens. Server-side
 * CSRF enforcement is untouched.
 */
(function () {
    'use strict';

    var metaEl = document.querySelector('meta[name="csrf-token"]');
    var metaToken = metaEl ? (metaEl.getAttribute('content') || '') : '';
    var cached = metaToken;
    var refreshing = null;

    function readMeta() {
        var el = document.querySelector('meta[name="csrf-token"]');
        return el ? (el.getAttribute('content') || '') : '';
    }

    /** Cached token (meta token until a refresh succeeds). */
    function getToken() {
        if (!cached) { cached = readMeta(); }
        return cached;
    }

    /** Fetch a fresh token from the server and cache it. */
    function refreshToken() {
        if (refreshing) { return refreshing; }
        refreshing = fetch('/api/csrf-token', {
            method: 'GET',
            credentials: 'same-origin',
            cache: 'no-store',
            headers: {
                'X-Requested-With': 'XMLHttpRequest',
                'Accept': 'application/json'
            }
        })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (d && d.csrf_token) {
                    cached = d.csrf_token;
                    return cached;
                }
                return getToken();
            })
            .catch(function () { return getToken(); })
            .then(function (t) { refreshing = null; return t; });
        return refreshing;
    }

    window.CSRF = {
        getToken: getToken,
        refreshToken: refreshToken
    };
})();
