/**
 * Login page behaviour.
 *
 * Moved out of an inline <script> so the Content-Security-Policy can forbid
 * inline script (RES-CSP-01). Behaviour is unchanged.
 */
(function () {
    var form = document.getElementById('login-form');
    var errorBox = document.getElementById('login-error');

    // Translate display strings created by this script (falls back to English
    // until the i18n runtime finishes loading).
    function tr(key) {
        return (typeof window.t === 'function') ? window.t(key) : key;
    }

    // Password visibility toggle (accessible and stateful)
    document.querySelectorAll('.pw-toggle').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var input = document.getElementById(btn.dataset.target);
            var show = input.type === 'password';
            input.type = show ? 'text' : 'password';
            var label = show ? tr('Hide password') : tr('Show password');
            var icon = document.createElement('i');
            icon.className = show ? 'bi bi-eye-slash' : 'bi bi-eye';
            icon.setAttribute('aria-hidden', 'true');
            btn.replaceChildren(icon);
            btn.setAttribute('aria-label', label);
            btn.setAttribute('title', label);
            btn.setAttribute('aria-pressed', show ? 'true' : 'false');
        });
    });

    function showError(msg) {
        document.getElementById('login-error-msg').textContent = msg;
        errorBox.classList.add('show');
    }

    function getCsrfToken() {
        return document.querySelector('meta[name=csrf-token]').content || '';
    }

    // Refresh the CSRF token from the public endpoint. Needed when the
    // anonymous session was rotated (idle timeout, previous login) and the
    // token rendered into the page is no longer valid.
    async function refreshCsrfToken() {
        try {
            const r = await fetch('/api/csrf-token', {
                credentials: 'same-origin',
                cache: 'no-store',
                headers: { 'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json' }
            });
            if (r.ok) {
                const d = await r.json();
                if (d && d.csrf_token) {
                    document.querySelector('meta[name=csrf-token]')
                        .setAttribute('content', d.csrf_token);
                    return d.csrf_token;
                }
            }
        } catch (e) { /* keep the current token */ }
        return getCsrfToken();
    }

    async function postLogin() {
        return fetch(form.action + window.location.search, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCsrfToken()
            },
            credentials: 'same-origin',
            body: JSON.stringify({
                username: document.getElementById('username').value,
                password: document.getElementById('password').value
            })
        }).then(function (r) {
            // A proxy or limiter page is not JSON; keep the status either way.
            return r.json().catch(function () { return {}; })
                .then(function (j) { return {ok: r.ok, status: r.status, body: j || {}}; });
        });
    }

    form.addEventListener('submit', async function (e) {
        e.preventDefault();
        errorBox.classList.remove('show');
        var btn = document.getElementById('login-btn');
        btn.disabled = true;
        btn.innerHTML = '<i class="bi bi-arrow-repeat icon-spin"></i> ' + tr('Signing in…');
        try {
            let res = await postLogin();
            // Stale CSRF token (rotated session): refresh once and retry.
            if (!res.ok && res.status === 400 && /csrf|token/i.test(String(res.body && res.body.error || ''))) {
                await refreshCsrfToken();
                res = await postLogin();
            }
            if (res.ok) { window.location.href = res.body.redirect || '/'; return; }
            showError(tr(res.body.error)
                || (res.status === 429 ? tr('Too many attempts. Wait a minute and try again.') : tr('Sign-in failed')));
            btn.disabled = false;
            btn.innerHTML = '<i class="bi bi-box-arrow-in-right"></i> ' + tr('Sign in');
        } catch (err) {
            showError(tr('An internal error occurred. Please try again.'));
            btn.disabled = false;
            btn.innerHTML = '<i class="bi bi-box-arrow-in-right"></i> ' + tr('Sign in');
        }
    });

    // ------------------------------------------------------------------
    // Language switcher: persists the choice in the user_language cookie
    // (honored server-side for anonymous visitors) and reloads so gettext,
    // dir and fonts are rendered in the selected language.
    // ------------------------------------------------------------------
    var sw = document.getElementById('langSwitch');
    var btn = document.getElementById('langBtn');
    btn.addEventListener('click', function (e) {
        e.stopPropagation();
        var open = sw.classList.toggle('open');
        btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    document.addEventListener('click', function (e) {
        if (sw.classList.contains('open') && !sw.contains(e.target)) {
            sw.classList.remove('open');
            btn.setAttribute('aria-expanded', 'false');
        }
    });
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && sw.classList.contains('open')) {
            sw.classList.remove('open');
            btn.setAttribute('aria-expanded', 'false');
            btn.focus();
        }
    });
    document.querySelectorAll('#langMenu button[data-lang]').forEach(function (opt) {
        opt.addEventListener('click', function () {
            var lang = opt.getAttribute('data-lang');
            if (!lang || lang === document.documentElement.getAttribute('lang')) {
                sw.classList.remove('open');
                return;
            }
            document.cookie = 'user_language=' + encodeURIComponent(lang) +
                '; path=/; max-age=31536000; SameSite=Lax';
            window.location.reload();
        });
    });
})();
