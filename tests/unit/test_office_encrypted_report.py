"""An encrypted Office document is encrypted, not corrupt.

Measured: `Spreadsheets/protect.xlsx` and three other password-protected OOXML
files came back from the round-17 run as ``File is not a zip file``, and
`Presentations/testPPT_protected_passtika.pptx` as
``Package not found at '...'``. Those are openpyxl's and python-pptx's words
for "this is not the ZIP I expected", which is true of the *ciphertext* and
misleading about the file: a user reading the error concludes the document is
damaged, and an operator counting corrupt files counts four healthy ones.

ODF already answers this properly (`_odf_encryption_report`): the reader states
that the package is encrypted and that a password is required. OOXML stores the
same information in the same place a legacy .doc does - an OLE container with
``EncryptionInfo`` and ``EncryptedPackage`` streams - so the answer is
available before any library is asked to parse it.
"""
import os
import zipfile

import pytest

from reader_file.readers.read_office import OfficeFileReader

CORPUS = os.path.join(os.path.dirname(__file__), "..", "..", "test-doc")

ENCRYPTED = [
    "Spreadsheets/protect.xlsx",
    "Spreadsheets/protectedFile.xlsx",
    "Spreadsheets/testEXCEL_protected_passtika.xlsx",
    "Spreadsheets/testEXCEL_protected_passtika_2.xlsx",
    "Presentations/testPPT_protected_passtika.pptx",
]


def reader():
    return OfficeFileReader()


def read(relative_path):
    path = os.path.join(CORPUS, relative_path)
    if not os.path.exists(path):
        pytest.skip(f"corpus file missing: {relative_path}")
    result = reader().read_file({"path": path,
                                 "extension": os.path.splitext(path)[1]})
    return result.get("content", result) if isinstance(result, dict) else {}


@pytest.mark.parametrize("relative_path", ENCRYPTED)
def test_an_encrypted_package_is_reported_as_encrypted(relative_path):
    content = read(relative_path)

    error = (content.get("error") or "").lower()
    assert error, "an encrypted package must report why it could not be read"
    assert "encrypt" in error or "password" in error, error
    # The library's own words describe the ciphertext, not the file.
    assert "not a zip file" not in error
    assert "package not found" not in error
    assert content.get("encrypted") is True


def test_the_report_says_which_parts_are_unreadable():
    content = read("Spreadsheets/protect.xlsx")
    assert content.get("encrypted") is True
    parts = [p.lower() for p in content.get("encrypted_parts") or []]
    assert any("encryptioninfo" in p for p in parts)
    assert any("encryptedpackage" in p for p in parts)


def test_a_plain_xlsx_is_not_reported_as_encrypted(tmp_path):
    path = tmp_path / "plain.xlsx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml",
                    '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats'
                    '.org/package/2006/content-types"/>')
    content = reader().read_file({"path": str(path), "extension": ".xlsx"})
    content = content.get("content", content) if isinstance(content, dict) else {}
    assert not content.get("encrypted")
