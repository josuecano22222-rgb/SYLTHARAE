"""The files page's same-source cleanup is wired to the confirmed endpoints."""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PAGE_JS = REPO_ROOT / "static/js/pages/files-list-page.js"
TEMPLATE = REPO_ROOT / "templates/file/files_list.html"


def test_handler_previews_then_confirms_then_removes():
    source = PAGE_JS.read_text(encoding="utf-8")
    assert "async function removeSameContextDuplicates()" in source
    assert "fetch('/api/files/same-context-duplicates'" in source
    assert "fetch('/api/files/same-context-duplicates/remove'" in source
    assert "window.ConfirmDialog.request({" in source
    # Nothing is removed before the confirmation: the POST comes after the request.
    assert source.index("ConfirmDialog.request") < source.index("same-context-duplicates/remove")
    assert "window.removeSameContextDuplicates = removeSameContextDuplicates;" in source


def test_page_exposes_the_button_and_its_dialog():
    html = TEMPLATE.read_text(encoding="utf-8")
    assert 'id="removeSameContextDuplicatesBtn"' in html
    assert "sameContextDuplicatesDialog" in html
