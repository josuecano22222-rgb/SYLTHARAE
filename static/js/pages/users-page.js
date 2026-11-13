/**
 * User Management page (/users, admin-only).
 * Wired to GET/POST /api/auth/users, PATCH /api/auth/users/<id>,
 * POST /api/auth/users/<id>/reset-password.
 */
import { getCSRFToken } from '../modules/core/utils.js';


let translations = {};
let currentUserId = null;

function escapeHtml(value) {
    return String(value == null ? '' : value)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

async function loadUsers() {
    const tbody = document.getElementById('usersTableBody');
    if (!tbody) return;
    try {
        const response = await fetch('/api/auth/users', {
            headers: { 'X-CSRFToken': getCSRFToken() }
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        const users = Array.isArray(data.users) ? data.users : [];

        const countEl = document.getElementById('usersCount');
        if (countEl) countEl.textContent = users.length;

        if (users.length === 0) {
            tbody.innerHTML = `<tr><td colspan="6" class="text-center py-4 text-muted">${escapeHtml(translations.noUsersFound || 'No users found.')}</td></tr>`;
            return;
        }

        tbody.innerHTML = users.map(u => renderUserRow(u)).join('');
    } catch (error) {
        tbody.innerHTML = `<tr><td colspan="6" class="text-center py-4 text-danger">
            ${escapeHtml(translations.error || 'Error')}: ${escapeHtml(error.message)}
        </td></tr>`;
    }
}

function setupUserTableInteractions() {
    const tbody = document.getElementById('usersTableBody');
    if (!tbody || tbody.dataset.actionsBound === 'true') return;
    tbody.dataset.actionsBound = 'true';

    tbody.addEventListener('change', (event) => {
        const control = event.target.closest('.user-role-select, .user-active-toggle');
        const row = control && control.closest('tr[data-user-id]');
        const userId = row ? Number(row.dataset.userId) : NaN;
        if (Number.isSafeInteger(userId) && userId > 0) updateUser(userId);
    });

    tbody.addEventListener('click', (event) => {
        const button = event.target.closest('button[data-user-action]');
        if (!button || !tbody.contains(button)) return;
        const userId = Number(button.dataset.userId);
        if (!Number.isSafeInteger(userId) || userId < 1) return;
        if (button.dataset.userAction === 'reset-password') {
            resetPassword(userId, button);
        } else if (button.dataset.userAction === 'delete-user') {
            showDeleteUserModal(userId, button.dataset.username || '', button);
        }
    });
}

function renderUserRow(u) {
    const userId = Number(u.id);
    if (!Number.isSafeInteger(userId) || userId < 1) return '';
    const username = String(u.username == null ? '' : u.username);
    const safeUsername = escapeHtml(username);
    const isSelf = userId === Number(currentUserId);
    const roleBadge = {
        admin: 'bg-dark',
        analyst: 'bg-primary',
        viewer: 'bg-secondary'
    }[u.role] || 'bg-secondary';
    const created = u.created_at ? String(u.created_at).slice(0, 10) : '—';
    const roleName = String(u.role || '');
    const deleteLabel = escapeHtml(translations.deleteUser || 'Delete User');
    const resetLabel = escapeHtml(translations.resetPassword || 'Reset password');
    return `
        <tr data-user-id="${userId}">
            <td>${userId}</td>
            <td>
                <strong>${safeUsername}</strong>
                ${isSelf ? `<span class="badge bg-info ms-1">${escapeHtml(translations.you || 'You')}</span>` : ''}
                ${u.must_change_password ? `<span class="badge bg-warning text-dark ms-1">${escapeHtml(translations.temporaryPassword || 'Temporary Password')}</span>` : ''}
            </td>
            <td>
                ${isSelf
                    ? `<span class="badge ${roleBadge}">${escapeHtml(translations['role_' + roleName] || roleName)}</span>`
                    : `<select class="form-select form-select-sm user-role-select" style="max-width: 130px;" aria-label="${escapeHtml(translations.role || 'Role')}">
                        ${['viewer', 'analyst', 'admin'].map(r =>
                            `<option value="${r}" ${r === u.role ? 'selected' : ''}>${escapeHtml(translations['role_' + r] || (r.charAt(0).toUpperCase() + r.slice(1)))}</option>`
                        ).join('')}
                    </select>`}
            </td>
            <td>
                <div class="form-check form-switch">
                    <input class="form-check-input user-active-toggle" type="checkbox" role="switch"
                           ${u.is_active ? 'checked' : ''} ${isSelf ? 'disabled' : ''}
                           aria-label="${escapeHtml(translations.status || 'Status')}">
                </div>
            </td>
            <td>${escapeHtml(created)}</td>
            <td class="text-end">
                <button type="button" class="btn btn-sm btn-outline-warning user-reset-password" data-user-action="reset-password" data-user-id="${userId}"
                        aria-label="${resetLabel}">
                    <i class="bi bi-key" aria-hidden="true"></i> ${resetLabel}
                </button>
                ${isSelf ? '' : `
                <button type="button" class="btn btn-sm btn-outline-danger user-delete-account" data-user-action="delete-user" data-user-id="${userId}"
                        data-username="${safeUsername}" aria-label="${deleteLabel}" title="${deleteLabel}">
                    <i class="bi bi-trash" aria-hidden="true"></i>
                </button>`}
            </td>
        </tr>`;
}

async function updateUser(userId) {
    const row = document.querySelector(`tr[data-user-id="${userId}"]`);
    if (!row) return;
    const role = row.querySelector('.user-role-select')?.value;
    const isActive = row.querySelector('.user-active-toggle')?.checked;

    // Guard: an admin cannot lock themselves out of the admin role.
    if (userId === Number(currentUserId)) {
        alert(translations.cannotChangeSelf || 'You cannot change your own role or status.');
        await loadUsers();
        return;
    }

    const body = {};
    if (role) body.role = role;
    if (typeof isActive === 'boolean') body.is_active = isActive;
    const controls = row.querySelectorAll('.user-role-select, .user-active-toggle');
    controls.forEach((control) => { control.disabled = true; });
    row.setAttribute('aria-busy', 'true');

    try {
        const response = await fetch(`/api/auth/users/${userId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
            body: JSON.stringify(body)
        });
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            alert((translations.error || 'Error') + ': ' + (err.error || translations.unknownError || 'Unknown error'));
        } else {
            window.showSuccess?.(translations.userUpdated || 'User updated successfully.');
        }
    } catch (error) {
        alert((translations.error || 'Error') + ': ' + (error.message || translations.unknownError || 'Unknown error'));
    } finally {
        await loadUsers();
        row.removeAttribute('aria-busy');
    }
}

async function resetPassword(userId, triggerButton = null) {
    if (!confirm(translations.confirmReset || 'Generate a new temporary password for this user?')) return;
    try {
        const response = await fetch(`/api/auth/users/${userId}/reset-password`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() }
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            alert((translations.error || 'Error') + ': ' + (data.error || translations.unknownError || 'Unknown error'));
            return;
        }

        const valueEl = document.getElementById('resetPasswordValue');
        const modalEl = document.getElementById('resetPasswordModal');
        if (!valueEl || !modalEl || !data.temporary_password) return;
        valueEl.value = data.temporary_password;
        modalEl.addEventListener('shown.bs.modal', () => {
            valueEl.focus();
            valueEl.select();
        }, { once: true });
        modalEl.addEventListener('hidden.bs.modal', async () => {
            valueEl.value = '';
            await loadUsers();
            const nextTrigger = document.querySelector(
                `.user-reset-password[data-user-id="${Number(userId)}"]`
            );
            (nextTrigger || document.getElementById('usersSectionTitle'))?.focus();
        }, { once: true });
        bootstrap.Modal.getOrCreateInstance(modalEl).show();
    } catch (error) {
        alert((translations.error || 'Error') + ': ' + (error.message || translations.unknownError || 'Unknown error'));
    }
}

function showDeleteUserModal(userId, username, triggerButton = null) {
    const modalEl = document.getElementById('deleteUserModal');
    if (!modalEl) return;
    document.getElementById('deleteUserModalName').textContent = username;
    const confirmBtn = document.getElementById('deleteUserConfirm');
    confirmBtn.dataset.userId = userId;
    modalEl.addEventListener('hidden.bs.modal', () => {
        const returnTarget = triggerButton && triggerButton.isConnected
            ? triggerButton
            : document.getElementById('usersSectionTitle');
        returnTarget?.focus();
    }, { once: true });
    const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
    modal.show();
    setTimeout(() => confirmBtn.focus(), 300);
}

async function deleteUser(userId) {
    const confirmBtn = document.getElementById('deleteUserConfirm');
    const errorBox = document.getElementById('deleteUserError');
    errorBox.classList.add('d-none');
    confirmBtn.disabled = true;
    confirmBtn.setAttribute('aria-busy', 'true');
    try {
        const response = await fetch(`/api/auth/users/${userId}`, {
            method: 'DELETE',
            headers: { 'X-CSRFToken': getCSRFToken() }
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            errorBox.textContent = data.error || translations.deleteFailed || 'Failed to delete user.';
            errorBox.classList.remove('d-none');
            return;
        }
        window.showSuccess?.(translations.userDeleted || 'User deleted successfully.');
        bootstrap.Modal.getInstance(document.getElementById('deleteUserModal'))?.hide();
        await loadUsers();
        document.getElementById('usersSectionTitle')?.focus();
    } catch (error) {
        errorBox.textContent = (translations.error || 'Error') + ': ' +
            (error.message || translations.pleaseTryAgain || 'Please try again.');
        errorBox.classList.remove('d-none');
    } finally {
        confirmBtn.disabled = false;
        confirmBtn.removeAttribute('aria-busy');
    }
}

function bindStaticUserControls() {
    const addUserButton = document.getElementById('addUserButton');
    if (addUserButton && addUserButton.dataset.bound !== 'true') {
        addUserButton.dataset.bound = 'true';
        addUserButton.addEventListener('click', showCreateUserModal);
    }
    const confirmBtn = document.getElementById('deleteUserConfirm');
    if (confirmBtn && confirmBtn.dataset.bound !== 'true') {
        confirmBtn.dataset.bound = 'true';
        confirmBtn.addEventListener('click', function () {
            deleteUser(parseInt(confirmBtn.dataset.userId, 10));
        });
    }
}

function showCreateUserModal() {
    const modalEl = document.getElementById('createUserModal');
    if (!modalEl) return;
    const triggerButton = document.activeElement;
    modalEl.addEventListener('hidden.bs.modal', () => {
        if (triggerButton && triggerButton.isConnected) triggerButton.focus();
    }, { once: true });
    document.getElementById('createUserError').classList.add('d-none');
    document.getElementById('createUserForm').reset();
    bootstrap.Modal.getOrCreateInstance(modalEl).show();
    setTimeout(() => document.getElementById('newUsername').focus(), 300);
}

function setupCreateUserForm() {
    const form = document.getElementById('createUserForm');
    if (!form || form.dataset.bound === 'true') return;
    form.dataset.bound = 'true';
    const usernameInput = document.getElementById('newUsername');
    const passwordInput = document.getElementById('newUserPassword');
    [usernameInput, passwordInput].forEach((input) => {
        input.addEventListener('input', () => input.removeAttribute('aria-invalid'));
    });

    form.addEventListener('submit', async function (e) {
        e.preventDefault();
        const errorBox = document.getElementById('createUserError');
        errorBox.classList.add('d-none');
        const username = usernameInput.value.trim();
        const password = passwordInput.value;
        const minimumLength = Number(passwordInput.getAttribute('minlength')) || 12;
        const role = document.getElementById('newUserRole').value;
        const mustChange = document.getElementById('newUserMustChange').checked;

        if (!username || !password) {
            errorBox.textContent = translations.credentialsRequired || 'Username and password are required.';
            errorBox.classList.remove('d-none');
            if (!username) {
                usernameInput.setAttribute('aria-invalid', 'true');
                usernameInput.focus();
            }
            if (!password) {
                passwordInput.setAttribute('aria-invalid', 'true');
                if (username) passwordInput.focus();
            }
            return;
        }
        if (password.length < minimumLength) {
            errorBox.textContent = passwordInput.getAttribute('data-minimum-message') ||
                ('Password must be at least ' + minimumLength + ' characters.');
            errorBox.classList.remove('d-none');
            passwordInput.setAttribute('aria-invalid', 'true');
            passwordInput.focus();
            return;
        }

        usernameInput.removeAttribute('aria-invalid');
        passwordInput.removeAttribute('aria-invalid');
        const submitBtn = document.getElementById('createUserSubmit');
        submitBtn.disabled = true;
        submitBtn.setAttribute('aria-busy', 'true');
        try {
            const response = await fetch('/api/auth/users', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
                body: JSON.stringify({ username, password, role, must_change_password: mustChange })
            });
            const data = await response.json().catch(() => ({}));
            if (response.ok && data.success) {
                bootstrap.Modal.getInstance(document.getElementById('createUserModal'))?.hide();
                window.showSuccess?.(translations.userCreated || 'User created successfully');
                await loadUsers();
            } else {
                errorBox.textContent = data.error || translations.createFailed || 'Failed to create user.';
                errorBox.classList.remove('d-none');
            }
        } catch (error) {
            errorBox.textContent = (translations.error || 'Error') + ': ' +
                (error.message || translations.pleaseTryAgain || 'Please try again.');
            errorBox.classList.remove('d-none');
        } finally {
            submitBtn.disabled = false;
            submitBtn.removeAttribute('aria-busy');
        }
    });
}

function initializeUsersPage() {
    if (!document.getElementById('usersTableBody')) return;
    const pageDataEl = document.getElementById('users-page-data');
    if (pageDataEl) {
        try {
            const data = JSON.parse(pageDataEl.textContent);
            translations = data.translations || {};
            currentUserId = data.currentUserId || null;
            window.translations = window.translations || {};
            Object.assign(window.translations, translations);
        } catch (e) {
            console.error('Error parsing users page data:', e);
        }
    }
    bindStaticUserControls();
    setupCreateUserForm();
    setupUserTableInteractions();
    return loadUsers();
}

export default function init() {
    if (document.readyState === 'loading') {
        return new Promise((resolve) => {
            document.addEventListener('DOMContentLoaded', () => {
                Promise.resolve(initializeUsersPage()).then(resolve);
            }, { once: true });
        });
    }
    return Promise.resolve(initializeUsersPage());
}

init();
