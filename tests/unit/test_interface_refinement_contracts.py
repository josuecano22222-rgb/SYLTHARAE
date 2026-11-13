"""Regression contracts for the first interface-refinement pass.

These assertions pin the cross-page details most likely to drift silently:
unique navigation IDs, a discoverable dashboard tab, accessible password
visibility controls, and a password-length hint that follows the configured
rule instead of duplicating a hard-coded number.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(relative):
    return (ROOT / relative).read_text(encoding="utf-8")


def test_archive_history_controls_keep_app_ids_and_shell_leaves_history_native():
    archive = read("templates/file/File_Management_Analysis_System.html")
    shell = read("templates/base.html")
    history = read("static/js/modules/navigation/history.js")
    base_script = read("static/js/pages/base-page.js")

    assert 'id="archiveNavBackBtn"' in archive
    assert 'id="archiveNavForwardBtn"' in archive
    assert 'id="navBackBtn"' not in archive
    assert 'id="navForwardBtn"' not in archive
    assert 'id="navBackBtn"' not in shell
    assert 'id="navForwardBtn"' not in shell
    assert 'id="navRefreshBtn"' not in shell
    assert "getElementById('archiveNavBackBtn')" in history
    assert "getElementById('archiveNavForwardBtn')" in history
    assert "history.back()" not in base_script
    assert "history.forward()" not in base_script


def test_shared_shell_has_one_language_control_and_accessible_mobile_navigation():
    shell = read("templates/base.html")
    base_script = read("static/js/pages/base-page.js")
    language_script = read("static/js/modules/core/language-switcher.js")

    assert 'id="sidebarLanguageSelect"' not in shell
    assert shell.count('id="topbarLangBtn"') == 1
    assert 'data-language-switch="{{ lang_code }}"' in shell
    assert 'aria-label="{{ _(\'Primary navigation\') }}"' in shell
    assert 'aria-controls="sidebar"' in shell
    assert 'data-close-label="{{ _(\'Close navigation menu\') }}"' in shell
    assert shell.index('class="page-navigation-bar"') < shell.index('id="menuToggle"')
    assert "sidebar.toggleAttribute('inert', !visible)" in base_script
    assert "sidebar.setAttribute('aria-hidden', visible ? 'false' : 'true')" in base_script
    assert "topbarButton.setAttribute('aria-busy', 'true')" in language_script
    assert "item.disabled = true" in language_script


def test_dashboard_has_a_control_for_the_rendered_paths_panel():
    template = read("templates/Analysis/dashboard.html")
    script = read("static/js/pages/dashboard-page.js")

    assert "switchTab('paths')" in template
    assert 'id="tab-paths"' in template
    assert "case 'paths':" in script


def page(template: str, script: str) -> str:
    """A page's template and the script it loads (RES-CSP-01 moved inline
    script to static files); the template must still load that script."""
    markup = read(template)
    assert f"filename='{script}'" in markup, f"{template} no longer loads {script}"
    return markup + "\n" + read(f"static/{script}")


def test_password_visibility_buttons_are_keyboard_reachable_and_stateful():
    for template, script in (
        ("templates/auth/login.html", "js/pages/login-page.js"),
        ("templates/auth/first_admin.html", "js/pages/first-admin-page.js"),
        ("templates/Setup/install_wizard.html", "js/pages/install-wizard-page.js"),
    ):
        source = page(template, script)
        assert 'aria-pressed="false"' in source
        assert 'tabindex="-1"' not in source
        assert "setAttribute('aria-pressed'" in source


def test_password_minimum_guidance_uses_the_enforced_configuration():
    base = read("templates/base.html")
    common = read("Api/routes/common.py")
    handler = read("static/js/user-menu.js")
    first_admin = read("templates/auth/first_admin.html")
    users = read("templates/auth/users.html")
    users_handler = read("static/js/pages/users-page.js")

    assert "'password_min_length': _password_min_length()" in common
    assert 'minlength="{{ password_min_length|default(12) }}"' in base
    assert "getAttribute('minlength')" in handler
    assert "password_min = password_min_length|default(12)" in first_admin
    assert 'minlength="{{ password_min_length|default(12) }}"' in users
    assert "getAttribute('minlength')" in users_handler


def test_the_setup_stepper_is_a_progress_list_not_a_nonfunctional_tablist():
    setup = page("templates/Setup/install_wizard.html", "js/pages/install-wizard-page.js")

    assert 'role="list" aria-label="{{ _(\'Installation steps\') }}"' in setup
    assert 'role="tablist" aria-label="Installation steps"' not in setup
    assert "goTo(TOTAL_STEPS + 1)" in setup


def test_user_row_actions_keep_untrusted_usernames_out_of_inline_javascript():
    users = read("static/js/pages/users-page.js")

    assert 'onclick=' not in users
    assert 'onchange=' not in users
    assert 'data-on-' not in users
    assert 'data-username="${safeUsername}"' in users
    assert "document.getElementById('deleteUserModalName').textContent = username" in users
    assert "showDeleteUserModal(userId, button.dataset.username || '', button)" in users
    users_template = read("templates/auth/users.html")
    assert '"deleteUser": _(\'Delete User\')' in users_template
    assert 'onclick=' not in users_template
    assert 'onchange=' not in users_template
    assert 'data-on-' not in users_template
    assert "admin: 'bg-dark'" in users


def test_setup_completion_renders_user_values_as_text_not_markup():
    setup = page("templates/Setup/install_wizard.html", "js/pages/install-wizard-page.js")

    assert "valueNode.textContent = String(value" in setup
    assert "body.admin_username + '</span>" not in setup
    assert "d.error || T('Check the step results above for details." in setup
    assert "renderInstallAlert(" in setup
