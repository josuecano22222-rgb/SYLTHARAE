/**
 * User account menu (base.html, every authenticated page).
 *
 * AUTH-UI gaps fixed:
 *  - "Change Password" now has a working UI (POST /auth/change-password).
 *  - The must_change_password banner button opens the same modal.
 *  - Logout is a plain CSRF-protected form POST to /auth/logout (no JS needed).
 *  - The current password is required (RES-AUTH-02). A 429 means the account
 *    was locked and signed out, so the page goes to the sign-in screen.
 */
(function () {
    'use strict';

    document.addEventListener('DOMContentLoaded', function () {
        var modalEl = document.getElementById('changePasswordModal');
        if (!modalEl) return; // anonymous session — menu not rendered

        var form = document.getElementById('changePasswordForm');
        var currentInput = document.getElementById('currentPasswordInput');
        var newInput = document.getElementById('newPasswordInput');
        var confirmInput = document.getElementById('confirmPasswordInput');
        var errorBox = document.getElementById('changePasswordError');
        var submitBtn = document.getElementById('changePasswordSubmit');
        var mismatch = document.getElementById('passwordMismatchFeedback');
        var modal = null;

        function openModal() {
            if (!modal) modal = new bootstrap.Modal(modalEl);
            errorBox.classList.add('d-none');
            errorBox.textContent = '';
            mismatch.style.display = 'none';
            form.reset();
            modal.show();
            setTimeout(function () { currentInput.focus(); }, 300);
        }

        var item = document.getElementById('changePasswordItem');
        if (item) item.addEventListener('click', openModal);

        var bannerBtn = document.getElementById('mustChangePasswordBtn');
        if (bannerBtn) bannerBtn.addEventListener('click', openModal);

        form.addEventListener('submit', function (e) {
            e.preventDefault();
            errorBox.classList.add('d-none');
            mismatch.style.display = 'none';

            var currentPassword = currentInput.value;
            var newPassword = newInput.value;
            var confirmPassword = confirmInput.value;
            var minimumLength = Number(newInput.getAttribute('minlength')) || 12;

            if (!currentPassword) {
                errorBox.textContent = currentInput.getAttribute('data-required-message') ||
                    'Enter your current password.';
                errorBox.classList.remove('d-none');
                return;
            }

            if (!newPassword || newPassword.length < minimumLength) {
                errorBox.textContent = newInput.getAttribute('data-minimum-message') ||
                    ('New password must be at least ' + minimumLength + ' characters.');
                errorBox.classList.remove('d-none');
                return;
            }
            if (newPassword !== confirmPassword) {
                mismatch.style.display = 'block';
                return;
            }
            if (newPassword === currentPassword) {
                errorBox.textContent = currentInput.getAttribute('data-unchanged-message') ||
                    'The new password must be different from the current one.';
                errorBox.classList.remove('d-none');
                return;
            }

            var csrfToken = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';
            submitBtn.disabled = true;

            fetch('/auth/change-password', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfToken
                },
                body: JSON.stringify({ current_password: currentPassword, new_password: newPassword })
            })
                .then(function (r) {
                    return r.json().catch(function () { return {}; })
                        .then(function (j) { return { ok: r.ok, status: r.status, body: j }; });
                })
                .then(function (res) {
                    if (res.status === 429 && res.body.code === 'account_locked') {
                        // Locked and signed out: nothing more can be done here.
                        window.alert(res.body.error || 'Account locked.');
                        window.location.href = '/auth/login';
                        return;
                    }
                    if (res.ok && res.body.success) {
                        // Reload so the (now cleared) must_change_password banner
                        // disappears and the user continues with the new session state.
                        window.location.href = '/';
                    } else {
                        errorBox.textContent = res.body.error || 'Failed to change password.';
                        errorBox.classList.remove('d-none');
                    }
                })
                .catch(function (err) {
                    errorBox.textContent = 'Error: ' + err.message;
                    errorBox.classList.remove('d-none');
                })
                .finally(function () { submitBtn.disabled = false; });
        });
    });
})();
