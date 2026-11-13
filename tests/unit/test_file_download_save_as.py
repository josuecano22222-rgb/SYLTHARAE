"""Regression: downloads and exports on the file record ask for a name and
save the file. They never go through the content swap, which cannot parse an
attachment and used to fail with a navigation error."""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FILE_ACTIONS = REPO_ROOT / "static/js/modules/ui/file-actions.js"
RECORD_ACTIONS = REPO_ROOT / "static/js/modules/core/record-actions.js"
DETAIL_PAGE = REPO_ROOT / "static/js/pages/file-detail-page.js"


def test_save_flow_is_exposed_and_asks_for_a_name():
    source = FILE_ACTIONS.read_text(encoding="utf-8")
    assert "async function saveFile(options)" in source
    assert "promptFileName(" in source
    assert "showSaveFilePicker" in source
    assert "        saveFile,\n        openPreview," in source


def test_preview_open_link_saves_through_the_dialog():
    source = FILE_ACTIONS.read_text(encoding="utf-8")
    assert 'data-file-save-url="' in source
    assert "closest('[data-file-save-url]')" in source


def test_cancelled_save_is_not_reported():
    source = RECORD_ACTIONS.read_text(encoding="utf-8")
    assert "result.cancelled" in source


def test_detail_downloads_do_not_use_the_swap_for_files():
    source = DETAIL_PAGE.read_text(encoding="utf-8")
    assert "actionId === 'files.download_original'" in source
    assert "actionId === 'files.export_content'" in source
    assert "actionId === 'files.view_original'" in source
    assert "window.open(" in source
    # The download and export branches return before the generic link path.
    download_at = source.index("actionId === 'files.download_original'")
    generic_at = source.index("Anything else the server prepared as a plain link")
    assert download_at < generic_at
    assert "function downloadContent" in source and "window.FileActions.saveFile" in source
