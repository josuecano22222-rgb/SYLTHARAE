"""Static contracts for soft-navigation lifecycle (no refresh required)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_page_lifecycle_module_exists():
    text = read("static/js/modules/core/page-lifecycle.js")
    assert "deactivateActivePage" in text
    assert "onPageTeardown" in text


def test_universal_initializer_runs_teardown_and_shell_bridge():
    text = read("static/js/pages/universal-initializer.js")
    assert "deactivateActivePage" in text
    assert "preparePageShell" in text
    assert "files-list-page.js" in text and "(bust)" in text


def test_navigation_swap_teardown_before_dom_replace():
    text = read("static/js/modules/core/navigation-swap.js")
    assert "deactivateActivePage" in text
    assert "syltharae:soft-nav:before" in text
    assert "finishSwapFromResponse" in text


def test_function_manager_exposes_attach_window_globals():
    text = read("static/js/managers/function-manager.js")
    assert "export function attachWindowGlobals" in text


def test_file_detail_page_mounts_without_domcontentloaded_only():
    detail = read("static/js/pages/file-detail-page.js")
    template = read("templates/file/file_detail.html")
    assert "export default function initFileDetailPage" in detail
    assert "mountFileDetailPage" in detail
    assert "onPageTeardown('file-detail'" in detail
    assert "file-detail-page.js" not in template or "universal-initializer" in template


def test_signals_page_registered_for_soft_nav():
    init = read("static/js/pages/universal-initializer.js")
    page = read("static/js/pages/signals-page.js")
    template = read("templates/Signals/signals.html")
    assert "'signals':" in init and "signals-page.js" in init
    assert "export default function initSignalsPage" in page
    assert "onPageTeardown('signals'" in page
    assert "js/pages/signals-page.js" not in template


def test_file_detail_workstation_layout():
    detail = read("static/js/pages/file-detail-page.js")
    template = read("templates/file/file_detail.html")
    css = read("static/css/file-details-inspector.css")
    assert "initFileDetailWorkstation" in detail
    assert "syncFileAnalystWorkspaceHeight" in detail
    assert "file-analyst-workspace" in template
    assert "file-analyst-page" in template
    assert "metadataPane.style.display" not in detail
    assert ".file-analyst-workspace" in css
    assert "--file-analyst-scroll-max" in css
    assert "file-analyst-content-tools" in template
    assert "file-analyst-ops-deck" in template
    panels = read("static/js/modules/file-operations/file-details-panels.js")
    assert "metadata-spotlight" in panels
    assert "buildFileMetadataCompactHtml" in panels
    assert "metadata-compact-grid" in panels
    fmas = read("templates/file/File_Management_Analysis_System.html")
    assert 'id="fileMetadataSection"' in fmas
    assert "metadata-compact-layout" in fmas
    assert "file-details-inspector.css" not in fmas
    modal_details = read("static/js/modules/file-operations/file-details.js")
    assert "layout: 'compact'" in modal_details
    assert "initFileModalPanels" in modal_details
    fmas_modal = read("templates/file/File_Management_Analysis_System.html")
    assert "file-modal-toolbar" in fmas_modal
    assert "file-modal-panels.css" in fmas_modal
    assert 'data-panel-id="modal-content"' in fmas_modal
    assert "data-panel-expand" in fmas_modal
    assert "file-document-status" in fmas_modal
    assert 'data-panel-id="modal-status"' in fmas_modal
    assert "fileAnalysisSummary" in fmas_modal
    assert fmas_modal.index("modal-status") < fmas_modal.index("modal-analysis")
    assert "renderDocumentStatusSummary" in read("static/js/modules/documents/document-analysis.js")
    file_view = read("static/js/modules/views/file-view.js")
    assert "fmas-files-scroll-body" in file_view
    assert "fmas-files-panel fmas-files-section" in file_view
    assert file_view.index("fmas-files-scroll-body") > file_view.index("buildFilesToolbar")
    assert "file_nav_dock" in template
    assert "file-nav-dock.css" in template
    nav_component = read("templates/components/file_nav.html")
    assert "fileNavDock" in nav_component
    nav_css = read("static/css/file-nav-dock.css")
    assert "var(--z-file-nav-dock" in nav_css
    assert "--z-file-nav-dock" in read("static/css/design-system.css")
    assert "syncFileNavDockInset" in detail
    nav_py = read("core/interfaces/navigation.py")
    assert "translate_ui" in nav_py
    assert "_UI_MSGIDS" in read("core/i18n/ui_catalog.py")
    assert "grid-template-rows: minmax(0, 1fr)" in css
