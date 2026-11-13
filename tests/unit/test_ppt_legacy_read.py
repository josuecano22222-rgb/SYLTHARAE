"""Legacy PowerPoint (.ppt/.pot/.pps) must reach the reader body, not raise.

The defect this pins: ``read_ppt_file`` opened with

    if logger is None:
        logger = logging.getLogger(__name__)

Assigning a name anywhere in a function makes it local for the *whole*
function, so that line raised

    UnboundLocalError: cannot access local variable 'logger' where it is not
    associated with a value

on the first statement, before any conversion was attempted. Every legacy
PowerPoint file the router sent here failed - with an error that says nothing
about PowerPoint, which is why it survived: the file was reported as a failed
read and the traceback looked like a missing-dependency problem.

The behavioural tests below call the reader the way the router does; the
structural test keeps the function from acquiring a local ``logger`` again.
"""

import ast
import inspect
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from reader_file.readers.read_office import OfficeFileReader  # noqa: E402

CFB_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _legacy_ppt(tmp_path: Path, name: str = "legacy.ppt") -> Path:
    """A file with the OLE/CFB header, i.e. what a 97-2003 presentation is."""
    path = tmp_path / name
    path.write_bytes(CFB_MAGIC + b"\x00" * 512)
    return path


@pytest.mark.parametrize("name", ["legacy.ppt", "template.pot", "show.pps"])
def test_read_ppt_file_returns_a_result_instead_of_raising(tmp_path, name):
    """All three legacy extensions share this entry point."""
    path = _legacy_ppt(tmp_path, name)

    result = OfficeFileReader().read_ppt_file(str(path))

    assert isinstance(result, dict), result
    # Either a conversion succeeded (LibreOffice present) or the reader reports
    # honestly that it could not read the file - never an exception.
    assert "error" in result or "slides" in result, result


def test_read_ppt_file_still_reports_a_missing_file(tmp_path):
    """The guard must not have been 'fixed' by returning early for everything."""
    missing = tmp_path / "not-there.ppt"
    reader = OfficeFileReader()

    result = reader.read_ppt_file(str(missing))

    assert result == {"error": "File not found", "filepath": str(missing)}


def test_router_hands_legacy_presentations_to_the_ppt_reader(tmp_path):
    """The router must not treat .ppt as unsupported while the reader is broken."""
    path = _legacy_ppt(tmp_path)
    reader = OfficeFileReader()

    result = reader.read_file({"path": str(path), "extension": ".ppt"})

    assert result is not None, "the office reader declined a .ppt file outright"
    assert isinstance(result, dict), result


def test_read_ppt_file_does_not_shadow_the_module_logger():
    """Structural guard for the exact shape of the regression."""
    source = inspect.getsource(OfficeFileReader.read_ppt_file)
    tree = ast.parse(source.lstrip())

    assigned = [
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    ]
    assert "logger" not in assigned, (
        "read_ppt_file must not bind `logger`: a local binding makes every"
        " reference to it local for the whole function, and the check that"
        " used to sit there raised UnboundLocalError before any work happened."
    )
