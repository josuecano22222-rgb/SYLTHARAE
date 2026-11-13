/**
 * First administrator: password strength, confirmation and account creation.
 *
 * Moved out of an inline <script> so the Content-Security-Policy can forbid
 * inline script (RES-CSP-01). Behaviour is unchanged.
 */
// Server-rendered values from #first-admin-page-data.
var FIRST_ADMIN_PAGE_DATA = JSON.parse(document.getElementById('first-admin-page-data').textContent || '{}');

(function () {
    var form = document.getElementById('form');
    var errBox = document.getElementById('err');
    var submitButton = document.getElementById('btn');
    var strings = {
        minimum: FIRST_ADMIN_PAGE_DATA.minimumMinSCharacters,
        weak: FIRST_ADMIN_PAGE_DATA.weakPassword,
        fair: FIRST_ADMIN_PAGE_DATA.fairPassword,
        strong: FIRST_ADMIN_PAGE_DATA.strongPassword,
        creating: FIRST_ADMIN_PAGE_DATA.creating,
        create: FIRST_ADMIN_PAGE_DATA.createAdministrator,
        passwordMismatch: FIRST_ADMIN_PAGE_DATA.passwordsDoNotMatch,
        createFailed: FIRST_ADMIN_PAGE_DATA.failedToCreateAdministrator,
        genericError: FIRST_ADMIN_PAGE_DATA.anInternalErrorOccurred,
        showPassword: FIRST_ADMIN_PAGE_DATA.showPassword,
        hidePassword: FIRST_ADMIN_PAGE_DATA.hidePassword
    };

    function setSubmitState(loading) {
        var icon = document.createElement('i');
        icon.className = loading ? 'bi bi-arrow-repeat icon-spin' : 'bi bi-person-check-fill';
        icon.setAttribute('aria-hidden', 'true');
        submitButton.replaceChildren(icon, document.createTextNode(' ' + (loading ? strings.creating : strings.create)));
        submitButton.disabled = loading;
        if (loading) submitButton.setAttribute('aria-busy', 'true');
        else submitButton.removeAttribute('aria-busy');
    }

    function showError(msg) {
        document.getElementById('err-msg').textContent = msg;
        errBox.classList.add('show');
    }

    // Password visibility toggles (presentation only)
    document.querySelectorAll('.pw-toggle').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var input = document.getElementById(btn.dataset.target);
            var show = input.type === 'password';
            input.type = show ? 'text' : 'password';
            var label = show ? strings.hidePassword : strings.showPassword;
            var icon = document.createElement('i');
            icon.className = show ? 'bi bi-eye-slash' : 'bi bi-eye';
            icon.setAttribute('aria-hidden', 'true');
            btn.replaceChildren(icon);
            btn.setAttribute('aria-label', label);
            btn.setAttribute('title', label);
            btn.setAttribute('aria-pressed', show ? 'true' : 'false');
        });
    });

    // Password strength indicator (presentation only), using the same minimum
    // rendered into the password control and enforced by the auth service.
    document.getElementById('password').addEventListener('input', function () {
        var pw = this.value;
        var minLength = Number(this.minLength) || 12;
        var score = 0;
        if (pw.length >= minLength) score++;
        if (pw.length >= minLength + 4) score++;
        if (/[A-Z]/.test(pw)) score++;
        if (/[0-9]/.test(pw)) score++;
        if (/[^A-Za-z0-9]/.test(pw)) score++;
        var pct = Math.min(100, score * 20);
        var colors = ['#ef4444', '#f59e0b', '#f59e0b', '#10b981', '#10b981', '#10b981'];
        var bar = document.getElementById('pw-strength');
        bar.style.width = pct + '%';
        bar.style.background = colors[score] || '#e2e8f0';
        var hint = document.getElementById('pw-hint');
        var label = pw.length === 0 ? strings.minimum :
                    score < 2 ? strings.weak : score < 4 ? strings.fair : strings.strong;
        var iconName = pw.length === 0 ? 'bi-shield-lock' :
                       score < 2 ? 'bi-shield-exclamation' : score < 4 ? 'bi-shield-plus' : 'bi-shield-check';
        var icon = document.createElement('i');
        icon.className = 'bi ' + iconName;
        icon.setAttribute('aria-hidden', 'true');
        hint.replaceChildren(icon, document.createTextNode(' ' + label));
    });

    var passwordConfirmation = document.getElementById('password2');
    passwordConfirmation.addEventListener('input', function () {
        passwordConfirmation.setCustomValidity('');
    });

    form.addEventListener('submit', function (e) {
        e.preventDefault();
        errBox.classList.remove('show');
        setSubmitState(true);
        var p1 = document.getElementById('password').value;
        var p2 = passwordConfirmation.value;
        if (p1 !== p2) {
            passwordConfirmation.setCustomValidity(strings.passwordMismatch);
            passwordConfirmation.reportValidity();
            showError(strings.passwordMismatch);
            setSubmitState(false);
            return;
        }
        passwordConfirmation.setCustomValidity('');
        fetch(window.location.href, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': document.querySelector('meta[name=csrf-token]').content
            },
            body: JSON.stringify({
                username: document.getElementById('username').value,
                password: p1
            })
        }).then(function (r) { return r.json().then(function (j) { return {ok: r.ok, body: j}; }); })
          .then(function (res) {
            if (res.ok) { window.location.href = res.body.redirect || '/'; return; }
            showError(res.body.error || strings.createFailed);
            setSubmitState(false);
        }).catch(function () {
            showError(strings.genericError);
            setSubmitState(false);
        });
    });
})();
