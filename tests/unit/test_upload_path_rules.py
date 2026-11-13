"""Unit: the ingestion page's path rules on Windows, macOS and Linux.

Windows is the primary production platform and its path rules differ from
POSIX in three ways that all matter to this page: paths are drive-qualified or
UNC, both ``\\`` and ``/`` separate components, and comparison is
case-insensitive. A rules module that ignores any of those tells the operator
the wrong thing about the path they typed — either refusing a path the server
would accept, or promising a path the server will refuse.

The real module is imported in node so the assertions cannot drift from what
ships in the browser. Skipped when node is not installed.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
MODULE = PROJECT_ROOT / "static" / "js" / "modules" / "upload" / "path-rules.js"

DRIVER = """
import * as p from '{module_url}';

const out = {{}};

// --- containment, Windows host -------------------------------------------
out.windows = [
  p.isInside('C:\\\\Data\\\\Evidence\\\\a.txt', 'C:\\\\Data\\\\Evidence', 'nt'),
  p.isInside('c:/data/evidence/2026/x.txt', 'C:\\\\Data\\\\Evidence', 'nt'),
  p.isInside('C:\\\\Data\\\\Evidence', 'C:\\\\Data\\\\Evidence', 'nt'),
  p.isInside('C:\\\\Data\\\\Evidence\\\\\\\\', 'C:\\\\Data\\\\Evidence', 'nt'),
  p.isInside('C:\\\\Data\\\\Evidence2\\\\a.txt', 'C:\\\\Data\\\\Evidence', 'nt'),
  p.isInside('C:\\\\Data\\\\Evil\\\\a.txt', 'C:\\\\Data\\\\Evidence', 'nt'),
  p.isInside('D:\\\\Evidence\\\\a.txt', 'C:\\\\Evidence', 'nt'),
  p.isInside('C:\\\\Data\\\\Evidence\\\\..\\\\Secrets\\\\x', 'C:\\\\Data\\\\Evidence', 'nt'),
  p.isInside('C:\\\\Data\\\\Evidence\\\\sub\\\\..\\\\ok.txt', 'C:\\\\Data\\\\Evidence', 'nt'),
  p.isInside('', 'C:\\\\Data', 'nt'),
  p.isInside('C:\\\\Data', '', 'nt'),
];

// --- containment, UNC root -----------------------------------------------
out.unc = [
  p.isInside('\\\\\\\\SERVER\\\\Share\\\\cases\\\\a.txt', '\\\\\\\\server\\\\share', 'nt'),
  p.isInside('//server/share/a.txt', '\\\\\\\\server\\\\share', 'nt'),
  p.isInside('\\\\\\\\server\\\\other\\\\a.txt', '\\\\\\\\server\\\\share', 'nt'),
  p.isInside('\\\\\\\\other\\\\share\\\\a.txt', '\\\\\\\\server\\\\share', 'nt'),
];

// --- containment, POSIX host ---------------------------------------------
out.posix = [
  p.isInside('/data/evidence/a.txt', '/data/evidence', 'posix'),
  p.isInside('/data/Evidence/a.txt', '/data/evidence', 'posix'),
  p.isInside('/data/evidence2/a.txt', '/data/evidence', 'posix'),
  p.isInside('/data/evidence/sub/../ok.txt', '/data/evidence', 'posix'),
  // A backslash is an ordinary character on POSIX, so this is NOT inside.
  p.isInside('/data\\\\evidence', '/data', 'posix'),
];

// --- examples and separators ---------------------------------------------
out.examples = [
  p.examplePathFor('C:\\\\Data\\\\Evidence', 'nt'),
  p.examplePathFor('/data/evidence', 'posix'),
  p.examplePathFor('', 'nt'),
  p.examplePathFor('', 'posix'),
  p.examplePathFor('C:/Data/Evidence', 'posix'),
  p.isWindowsPath('C:\\\\Data'), p.isWindowsPath('C:/Data'), p.isWindowsPath('\\\\\\\\srv\\\\share'),
  p.isWindowsPath('/data'), p.isWindowsPath('data\\\\file.txt'),
];

// --- joining a dropped folder tree --------------------------------------
out.joins = [
  p.joinRelative('', 'file.txt'),
  p.joinRelative('Case 1', 'scan.jpg'),
  p.joinRelative('Case 1/sub/', '/scan.jpg'),
  p.joinRelative('Case 1', ''),
];

// --- file/entry naming ---------------------------------------------------
out.names = [
  p.displayNameFor({{ name: 'a.txt', webkitRelativePath: 'Case 1/a.txt' }}),
  p.displayNameFor({{ name: 'a.txt', webkitRelativePath: '' }}),
  p.displayNameFor({{ name: 'a.txt' }}),
  p.displayNameFor({{ file: {{ name: 'b.txt' }}, path: 'Case 1/sub/b.txt' }}),
  p.relativePathFor({{ name: 'a.txt', webkitRelativePath: 'dir/a.txt' }}),
];

// --- did the browser report the folder structure? ------------------------
out.folders = [
  p.folderStructureAvailable([]),
  p.folderStructureAvailable([{{ name: 'a.txt', webkitRelativePath: 'Case 1/a.txt' }}]),
  p.folderStructureAvailable([{{ file: {{ name: 'b.txt' }}, path: 'Case 1/b.txt' }}]),
  p.folderStructureAvailable([{{ name: 'a.txt' }}, {{ name: 'b.txt' }}]),
];

// --- shortening ----------------------------------------------------------
const deep = 'C:\\\\Evidence\\\\2026\\\\Case 000123\\\\Scans\\\\Batch 4\\\\' + 'x'.repeat(60) + '\\\\report.pdf';
out.shortened = [p.shortenPath('C:\\\\Data\\\\a.txt', 64), p.shortenPath(deep, 64).length, p.shortenPath(deep, 64).includes('report.pdf')];

console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def rules(tmp_path_factory):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    assert MODULE.exists(), MODULE

    driver = tmp_path_factory.mktemp("path-rules") / "driver.mjs"
    driver.write_text(DRIVER.format(module_url=MODULE.as_uri()), encoding="utf-8")
    proc = subprocess.run(
        [node, str(driver)], capture_output=True, text=True, timeout=120, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_windows_containment_is_case_and_separator_tolerant(rules):
    windows = rules["windows"]
    assert windows[0] is True     # C:\Data\Evidence\a.txt under C:\Data\Evidence
    assert windows[1] is True     # forward slashes, different case
    assert windows[2] is True     # the root itself
    assert windows[3] is True     # trailing separators
    assert windows[4] is False    # Evidence2 is a sibling, not a child
    assert windows[5] is False
    assert windows[6] is False    # a different drive is never inside
    assert windows[7] is False    # .. escapes the root
    assert windows[8] is True     # .. that stays inside is fine
    assert windows[9] is False    # empty path
    assert windows[10] is False   # empty root


def test_unc_roots_contain_their_share(rules):
    unc = rules["unc"]
    assert unc[0] is True
    assert unc[1] is True
    assert unc[2] is False
    assert unc[3] is False


def test_posix_containment_stays_case_sensitive(rules):
    posix = rules["posix"]
    assert posix[0] is True
    assert posix[1] is False   # /data/Evidence is a different directory
    assert posix[2] is False
    assert posix[3] is True
    assert posix[4] is False   # a backslash is not a separator on POSIX


def test_the_field_example_matches_the_host(rules):
    examples = rules["examples"]
    assert examples[0] == "C:\\Data\\Evidence\\subfolder"
    assert examples[1] == "/data/evidence/subfolder"
    assert examples[2] == "C:\\data\\inbox"
    assert examples[3] == "/data/inbox"
    # A Windows-shaped root on a POSIX host still reads as a Windows path, so
    # the example does not advertise a separator the server cannot use.
    assert examples[4] == "C:/Data/Evidence\\subfolder"
    assert examples[5:8] == [True, True, True]
    assert examples[8:10] == [False, False]


def test_dropped_folder_paths_are_joined(rules):
    assert rules["joins"] == ["file.txt", "Case 1/scan.jpg", "Case 1/sub/scan.jpg", "Case 1"]


def test_selected_files_keep_the_path_inside_their_folder(rules):
    names = rules["names"]
    assert names[0] == "Case 1/a.txt"          # folder picked through a picker
    assert names[1] == "a.txt"                 # single file
    assert names[2] == "a.txt"
    assert names[3] == "Case 1/sub/b.txt"      # file walked out of a dropped folder
    assert names[4] == "dir/a.txt"


def test_deep_paths_are_shortened_without_losing_the_file_name(rules):
    short, length, keeps_name = rules["shortened"]
    assert short == "C:\\Data\\a.txt"
    assert length <= 64
    assert keeps_name is True


def test_a_folder_selection_without_structure_is_recognisable(rules):
    folders = rules["folders"]
    assert folders[0] is True    # nothing selected is nothing to warn about
    assert folders[1] is True    # a picker that reports webkitRelativePath
    assert folders[2] is True    # a file walked out of a dropped folder
    assert folders[3] is False   # only bare names: the tree would be lost
