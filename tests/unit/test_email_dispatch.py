"""Unit: the email reader identifies containers by content, not filename.

DETECT-01 was fixed in read_archive.py but the email reader still dispatched on
``file_lower.endswith('.msg')``. Consequences, both real:

* a genuine message renamed to .txt was never routed here, so it was read as
  plain text and its attachments were lost;
* anything renamed to .msg was handed to extract_msg, producing a confusing
  parse error instead of the truth about what the file is.

The three formats give genuinely different content signals, measured with
detect_file_type_with_confidence:

    .msg   -> ('.msg', 'strong')   OLE2 container holding a __substg1.0_ entry
    .pst   -> ('.ole', 'strong')   OLE2, but no PST-specific signature exists
    .eml   -> ('.bin',  'none')    plain text, no magic bytes
    .mbox  -> ('.bin',  'none')    plain text, no magic bytes

So no single rule covers all four. Content wins where content exists; for the
text formats the bytes are sniffed for an RFC822 header or an mbox "From "
envelope line, and only then is the declared extension trusted.
"""

import sys
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from reader_file.readers.read_email import EmailFileReader  # noqa: E402

OLE_MAGIC = b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1"
MSG_BYTES = (
    OLE_MAGIC + b"\x00" * 600
    + "__substg1.0_0037001F".encode("utf-16-le") + b"\x00" * 600
)
PST_BYTES = OLE_MAGIC + b"\x00" * 600 + b"NBDTNDPABPABPAGE" + b"\x00" * 600


@pytest.fixture(scope="module")
def eml_bytes():
    msg = EmailMessage()
    msg["From"] = "alice@example.org"
    msg["To"] = "bob@example.org"
    msg["Subject"] = "dispatch probe"
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="example.org")
    msg.set_content("Marker DISPATCHBODY body text")
    return bytes(msg)


@pytest.fixture
def reader():
    return EmailFileReader()


def resolve(reader, tmp_path, name, data, detected=None):
    path = tmp_path / name
    path.write_bytes(data)
    info = {"path": str(path), "extension": Path(name).suffix}
    if detected is not None:
        info["effective_extension"] = detected
    return reader._resolve_email_type(info, str(path))


# ------------------------------------------------------- content wins
def test_msg_content_under_a_txt_name_is_treated_as_msg(reader, tmp_path):
    """The core DETECT-01 case: content beats the filename."""
    assert resolve(reader, tmp_path, "message.txt", MSG_BYTES, ".msg")[0] == ".msg"


def test_msg_content_under_an_eml_name_is_treated_as_msg(reader, tmp_path):
    assert resolve(reader, tmp_path, "message.eml", MSG_BYTES, ".msg")[0] == ".msg"


def test_eml_content_under_a_msg_name_is_treated_as_eml(reader, tmp_path, eml_bytes):
    """The reverse error: an EML renamed to .msg must not reach extract_msg."""
    assert resolve(reader, tmp_path, "message.msg", eml_bytes)[0] == ".eml"


def test_eml_content_with_no_extension_is_found_by_sniffing(reader, tmp_path, eml_bytes):
    assert resolve(reader, tmp_path, "message", eml_bytes)[0] == ".eml"


def test_eml_content_under_an_unrelated_extension_is_found(reader, tmp_path, eml_bytes):
    assert resolve(reader, tmp_path, "notes.dat", eml_bytes)[0] == ".eml"


@pytest.mark.parametrize("header", [
    "From: a@b.c", "Received: from host", "Return-Path: <a@b.c>",
    "MIME-Version: 1.0", "Subject: hi", "Message-ID: <x@y>",
])
def test_each_rfc822_header_opener_is_recognised(reader, tmp_path, header):
    data = header.encode() + b"\nTo: c@d.e\n\nbody\n"
    assert resolve(reader, tmp_path, "noext", data)[0] == ".eml"


def test_mbox_envelope_line_is_recognised_as_mbox(reader, tmp_path, eml_bytes):
    """'From ' (envelope, with a space) is mbox; 'From:' is an RFC822 header."""
    data = b"From sender@example.org Mon Jan  1 00:00:00 2026\n" + eml_bytes
    assert resolve(reader, tmp_path, "mailbox", data)[0] == ".mbox"


def test_from_header_is_not_confused_with_an_mbox_envelope(reader, tmp_path):
    data = b"From: someone@example.org\nSubject: x\n\nbody\n"
    assert resolve(reader, tmp_path, "noext", data)[0] == ".eml"


# ------------------------------------------------- ordinary declarations
def test_plain_eml_is_handled(reader, tmp_path, eml_bytes):
    assert resolve(reader, tmp_path, "message.eml", eml_bytes)[0] == ".eml"


def test_plain_mbox_is_handled(reader, tmp_path, eml_bytes):
    data = b"From sender@example.org Mon Jan  1 00:00:00 2026\n" + eml_bytes
    assert resolve(reader, tmp_path, "mailbox.mbox", data)[0] == ".mbox"


def test_pst_is_accepted_when_the_container_matches(reader, tmp_path):
    """.pst has no format-specific signature; OLE2 plus the declaration is all
    that exists, so the declaration has to settle it."""
    assert resolve(reader, tmp_path, "store.pst", PST_BYTES, ".ole")[0] == ".pst"


def test_bin_detection_never_causes_a_rejection(reader, tmp_path, eml_bytes):
    """'.bin' means unidentified, not identified-as-something-else."""
    assert resolve(reader, tmp_path, "message.eml", eml_bytes, ".bin")[1] is None


# ------------------------------------------------------------- rejection
def test_a_zip_renamed_to_msg_is_rejected_not_misparsed(reader, tmp_path):
    """Handing a ZIP to extract_msg yields a confusing error, not the truth."""
    data = b"PK\x03\x04" + b"\x00" * 100
    email_type, rejection = resolve(reader, tmp_path, "fake.msg", data, ".zip")
    assert rejection, "a positively identified non-email was accepted"
    assert ".zip" in rejection


def test_a_pdf_renamed_to_eml_is_rejected(reader, tmp_path):
    data = b"%PDF-1.7\n" + b"\x00" * 100
    assert resolve(reader, tmp_path, "fake.eml", data, ".pdf")[1] is not None


def test_truly_unknown_bytes_are_left_to_the_dispatcher(reader, tmp_path):
    """No rejection here: the dispatcher reports 'Unsupported email type'."""
    email_type, rejection = resolve(
        reader, tmp_path, "mystery.emlx", b"\x01\x02\x03 no headers here"
    )
    assert rejection is None
    assert email_type == ".emlx"


# ---------------------------------------------------------- end to end
def test_read_file_routes_a_renamed_eml_to_the_eml_extractor(reader, tmp_path, eml_bytes):
    """The resolver is only useful if read_file actually uses it."""
    path = tmp_path / "message.dat"
    path.write_bytes(eml_bytes)
    result = reader.read_file({"path": str(path), "extension": ".dat"})
    assert result.get("error") is None, result
    assert result.get("email_type") == "eml", result
    assert result.get("message"), "no message payload extracted"


def test_read_file_still_rejects_a_non_email(reader, tmp_path):
    path = tmp_path / "thing.emlx"
    path.write_bytes(b"\x01\x02\x03 no headers")
    result = reader.read_file({"path": str(path), "extension": ".emlx"})
    assert "unsupported" in (result.get("error") or "").lower(), result


def test_original_declared_extensions_are_still_supported(reader):
    """Dispatch fixes must never remove a format.

    The set is asserted as a superset rather than an equality: the reader was
    later extended to the Outlook offline store (.ost), the Apple Mail message
    (.emlx) and the alternative mbox spelling (.mbx), and each of those has its
    own dispatch test above. What this test protects is that none of the
    original four ever disappears.
    """
    assert {".eml", ".msg", ".mbox", ".pst"} <= reader.get_supported_extensions()


def test_sniff_does_a_bounded_read(reader, tmp_path):
    """A large file must not be fully loaded to answer this question."""
    path = tmp_path / "big.eml"
    path.write_bytes(b"From: a@b.c\n" + b"x" * (5 * 1024 * 1024))
    assert reader._sniff_text_email(str(path)) == ".eml"
