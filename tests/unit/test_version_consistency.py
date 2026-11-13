"""The release version has one value everywhere it is declared."""
import re
from pathlib import Path

import version

ROOT = Path(__file__).resolve().parents[2]


def test_pyproject_matches_version_py():
    toml = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    declared = re.search(r'^version\s*=\s*"([^"]+)"', toml, re.M).group(1)
    assert declared == version.__version__


def test_version_info_matches_version_string():
    assert ".".join(map(str, version.__version_info__)) == version.__version__


def test_changelog_has_an_entry_for_this_version():
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert re.search(rf"^## v{re.escape(version.__version__)}\b", changelog, re.M)
