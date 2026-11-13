"""The pagination macro's keyword arguments are a contract, checked at every caller.

A caller that passes a keyword the macro does not declare fails at render time
with ``TypeError: macro 'unified_pagination' takes no keyword argument ...``
(this broke /file/<id> with a HTTP 500). These tests check every call site
against the macro's signature, and render the file-detail call itself.
"""
import re
from pathlib import Path

import pytest
from jinja2 import DictLoader, Environment

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = PROJECT_ROOT / "templates"
MACRO_FILE = TEMPLATES / "components" / "unified_pagination.html"
FILE_DETAIL = TEMPLATES / "file" / "file_detail.html"


def _macro_parameters():
    source = MACRO_FILE.read_text(encoding="utf-8")
    signature = re.search(r"macro unified_pagination\((.*?)\)\s*-%\}", source, re.S).group(1)
    return {part.split("=")[0].strip() for part in signature.split(",") if part.strip()}


def _calls(text):
    """Yield the argument text of every unified_pagination(...) call in a template."""
    for match in re.finditer(r"unified_pagination\(", text):
        if "macro unified_pagination" in text[max(0, match.start() - 20):match.start()]:
            continue
        depth, index = 1, match.end()
        while index < len(text) and depth:
            depth += {"(": 1, ")": -1}.get(text[index], 0)
            index += 1
        yield text[: match.start()].count("\n") + 1, text[match.end(): index - 1]


def test_every_caller_passes_only_declared_keywords():
    parameters = _macro_parameters()
    problems = []
    for template in TEMPLATES.rglob("*.html"):
        text = template.read_text(encoding="utf-8", errors="ignore")
        for line, arguments in _calls(text):
            keywords = set(re.findall(r"(?:^|,|\s)([a-z_]+)\s*=(?!=)", arguments))
            unknown = sorted(k for k in keywords if k not in parameters and k != "url_for")
            if unknown:
                problems.append(f"{template.relative_to(PROJECT_ROOT)}:{line} {unknown}")
    assert not problems, "unified_pagination callers pass undeclared keywords: " + "; ".join(problems)


def test_file_detail_does_not_pass_the_stale_show_more_keyword():
    assert "show_more" not in FILE_DETAIL.read_text(encoding="utf-8")


def _render(call):
    loader = DictLoader({
        "components/unified_pagination.html": MACRO_FILE.read_text(encoding="utf-8"),
        "caller.html": (
            "{% from 'components/unified_pagination.html' import unified_pagination %}"
            + call
        ),
    })
    env = Environment(loader=loader, autoescape=True)
    env.globals["_"] = lambda text: text  # the app's translation helper; identity in tests
    return env.get_template("caller.html").render(url_for=lambda *a, **k: "/x")


FILE_DETAIL_CALL = (
    "{{ unified_pagination(current_page, total_pages, url_for, 'files.file_detail', "
    "{'per_page': 5000, 'search': none}, show_info=false, show_jump=false) }}"
)


@pytest.mark.parametrize("current,total", [(1, 2), (2, 3), (3, 3)])
def test_file_detail_call_renders_multi_page_pagination(current, total):
    html = _render(FILE_DETAIL_CALL.replace("current_page", str(current)).replace("total_pages", str(total)))
    assert "unified-pagination-container" in html
    assert 'data-endpoint="files.file_detail"' in html  # the pager keeps its endpoint
    assert 'data-current-page="%d"' % current in html
    assert 'data-total-pages="%d"' % total in html


def test_file_detail_call_renders_nothing_for_a_single_page_file():
    html = _render(FILE_DETAIL_CALL.replace("current_page", "1").replace("total_pages", "1"))
    assert html.strip() == ""


def test_file_detail_call_has_no_load_more_without_a_table():
    html = _render(FILE_DETAIL_CALL.replace("current_page", "1").replace("total_pages", "2"))
    assert "Load More" not in html and "load-more" not in html.lower()


def test_the_stale_keyword_is_rejected_by_the_macro():
    # Documents the failure the caller fix removes.
    stale = FILE_DETAIL_CALL.replace("show_jump=false)", "show_jump=false, show_more=false)")
    with pytest.raises(Exception, match="show_more"):
        _render(stale.replace("current_page", "1").replace("total_pages", "2"))
