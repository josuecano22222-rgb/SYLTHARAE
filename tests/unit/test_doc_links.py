"""Every relative link and #anchor in the Markdown documentation resolves."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCS = sorted([*ROOT.glob("*.md"), *ROOT.glob("docs/*.md"), *ROOT.glob("docs/reference/*.md")])
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")
FENCE = re.compile(r"```.*?```", re.S)


def _slug(heading: str) -> str:
    """GitHub's anchor algorithm: lowercase, keep word chars (incl. '_') and '-',
    drop other punctuation, spaces -> '-'."""
    text = re.sub(r"<[^>]+>", "", heading.strip().lower())
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def _anchors(path: Path) -> set:
    body = FENCE.sub("", path.read_text(encoding="utf-8"))
    seen, out = {}, set()
    for heading in re.findall(r"^#{1,6}\s+(.+?)\s*#*\s*$", body, re.M):
        slug = _slug(heading)
        n = seen.get(slug, 0)
        out.add(slug if n == 0 else f"{slug}-{n}")
        seen[slug] = n + 1
    return out


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(ROOT)))
def test_relative_links_resolve(doc):
    body = FENCE.sub("", doc.read_text(encoding="utf-8"))
    broken = []
    for target in LINK.findall(body):
        if re.match(r"^[a-z]+:", target):  # http:, https:, mailto:
            continue
        file_part, _, anchor = target.partition("#")
        dest = (doc.parent / file_part).resolve() if file_part else doc
        if not dest.exists():
            broken.append(f"{target} (missing file)")
        elif anchor and dest.suffix == ".md" and anchor not in _anchors(dest):
            broken.append(f"{target} (missing anchor)")
    assert not broken, f"{doc.relative_to(ROOT)}: {broken}"
