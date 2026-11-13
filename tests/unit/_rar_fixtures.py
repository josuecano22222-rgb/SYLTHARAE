"""Build RAR containers byte by byte, with no archiving tool installed.

The RAR defects this suite guards against could only be reproduced on a machine
that had *neither* a decoder *nor* an archiver, which is exactly the machine
that reported them - so the fixtures are constructed from the published format
description in the RARLAB technote and rarfile's own parser, not from a tool.

A hand-built container is not a substitute for a real WinRAR-produced file, and
these helpers make no claim to cover every option RAR can emit. They cover the
structures the reader actually parses: main header, file headers (stored,
"compressed", directory, encrypted, large-name), service header, extra area,
end-of-archive marker, RAR4 and RAR5, and a stub-prefixed self-extracting
archive.
"""

from __future__ import annotations

import binascii
import struct

RAR4_SIGNATURE = b"Rar!\x1a\x07\x00"
RAR5_SIGNATURE = b"Rar!\x1a\x07\x01\x00"


def vint(value: int) -> bytes:
    """RAR5 variable-length integer (little-endian 7-bit groups)."""
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


# ----------------------------------------------------------------- RAR5
def rar5_block(block_type: int, body: bytes = b"", data: bytes = b"",
               header_flags: int = 0, extra: bytes = b"") -> bytes:
    """One RAR5 block: CRC32 + size vint + type + flags + sizes + body + data."""
    payload = vint(block_type) + vint(header_flags)
    if header_flags & 0x01:          # extra area present
        payload += vint(len(extra))
    if header_flags & 0x02:          # data area present
        payload += vint(len(data))
    payload += body + extra
    size_field = vint(len(payload))
    crc = binascii.crc32(size_field + payload) & 0xFFFFFFFF
    return struct.pack("<I", crc) + size_field + payload + data


def rar5_file_body(name: str, size: int, *, method: int = 0, is_dir: bool = False,
                   mtime: int = 0x5EA0B123, encrypt: bool = False,
                   host_os: int = 1) -> tuple:
    """Return ``(body, header_flags, extra_area)`` for one RAR5 file header."""
    file_flags = 0x0002 | 0x0004      # has mtime, has data CRC32
    if is_dir:
        file_flags |= 0x0001
    extra = b""
    header_flags = 0x02
    if encrypt:
        record = vint(0x01) + b"\x0f" + bytes(16) + bytes(16) + bytes(12)
        extra = vint(len(record)) + record
        header_flags |= 0x01
    body = (
        vint(file_flags)
        + vint(size)
        + vint(0x20)                                   # attributes
        + struct.pack("<I", mtime)
        + struct.pack("<I", binascii.crc32(b"") & 0xFFFFFFFF)  # data CRC (fixed below)
        + vint(method << 7)                            # compression info
        + vint(host_os)
        + vint(len(name))
        + name.encode("utf-8")
    )
    return body, header_flags, extra


def rar5_file(name: str, data: bytes, *, method: int = 0, is_dir: bool = False,
              encrypt: bool = False) -> bytes:
    """One complete RAR5 file block (header + data area)."""
    file_flags = 0x0002 | 0x0004
    if is_dir:
        file_flags |= 0x0001
    extra = b""
    header_flags = 0x02
    if encrypt:
        record = vint(0x01) + b"\x0f" + bytes(16) + bytes(16) + bytes(12)
        extra = vint(len(record)) + record
        header_flags |= 0x01
    if is_dir and not data:
        header_flags = 0x01 if extra else 0   # directories carry no data area
    body = (
        vint(file_flags)
        + vint(len(data))
        + vint(0x20)
        + struct.pack("<I", 0x5EA0B123)
        + struct.pack("<I", binascii.crc32(data) & 0xFFFFFFFF)
        + vint((method & 0x07) << 7)
        + vint(1)
        + vint(len(name))
        + name.encode("utf-8")
    )
    return rar5_block(2, body=body, data=data if not is_dir else b"",
                      header_flags=header_flags, extra=extra)


def rar5_service(name: str, data: bytes) -> bytes:
    """A stored RAR5 service header (e.g. ``CMT`` for the archive comment)."""
    body = (
        vint(0x0002 | 0x0004 | 0x0008)
        + vint(len(data))
        + vint(0x20)
        + struct.pack("<I", 0)
        + struct.pack("<I", binascii.crc32(data) & 0xFFFFFFFF)
        + vint(0)
        + vint(1)
        + vint(len(name))
        + name.encode("ascii")
    )
    return rar5_block(3, body=body, data=data, header_flags=0x02)


def rar5_archive(entries, *, comment: bytes = b"", sfx_stub: bytes = b"",
                 archive_flags: int = 0) -> bytes:
    """A complete RAR5 archive from ``entries`` (see :func:`rar5_file`)."""
    out = bytearray(sfx_stub + RAR5_SIGNATURE)
    main_body = vint(archive_flags) + vint(0)
    out += rar5_block(1, body=main_body)
    for entry in entries:
        out += rar5_file(**entry)
    if comment:
        out += rar5_service("CMT", comment)
    out += rar5_block(5, body=vint(0))
    return bytes(out)


# ----------------------------------------------------------------- RAR4
def _rar4_block(block_type: int, flags: int, body: bytes = b"", data: bytes = b"",
                add: bytes = b"") -> bytes:
    """One RAR4 block.

    The ADD_SIZE field is part of the header (not a separate trailer): when
    the 0x8000 flag is set it follows HEAD_SIZE and is counted in HEAD_SIZE.
    """
    if add:
        flags |= 0x8000
        body = struct.pack("<I", len(add)) + body
    head_size = 7 + len(body)
    prefix = struct.pack("<HBHH", 0, block_type, flags, head_size)
    crc = binascii.crc32(prefix[2:] + body) & 0xFFFF
    return struct.pack("<H", crc) + prefix[2:] + body + add + data


def rar4_archive(entries, *, comment: bytes = b"") -> bytes:
    """A complete RAR4 archive from ``entries``.

    ``entries`` items: ``name``, ``data``, optional ``method`` (0 = stored),
    ``is_dir``, ``encrypt``.
    """
    out = bytearray(RAR4_SIGNATURE)
    out += _rar4_block(0x73, 0x0000, struct.pack("<HI", 0, 0))
    for entry in entries:
        name = entry["name"].encode("utf-8")
        data = entry.get("data", b"")
        method = entry.get("method", 0)
        flags = 0x8000
        if entry.get("is_dir"):
            flags |= 0x00E0
        if entry.get("encrypt"):
            flags |= 0x0004
        body = struct.pack(
            "<IIBIIBBH",
            len(data), len(data), 0x02,
            binascii.crc32(data) & 0xFFFFFFFF,
            0x4BF0A1C0,
            20, 0x30 + method, len(name),
        )
        body += struct.pack("<I", 0x20) + name
        out += _rar4_block(0x74, flags, body, data if not entry.get("is_dir") else b"")
    if comment:
        out += _rar4_block(0x75, 0x0000, b"", b"", add=comment)
    out += _rar4_block(0x7B, 0x4000)
    return bytes(out)


def corrupt_member_data(archive: bytes, needle: bytes) -> bytes:
    """Flip bytes inside a stored member's data area (CRC must stop matching)."""
    index = archive.index(needle) + len(needle) // 2
    mutated = bytearray(archive)
    mutated[index:index + 2] = b"\xff\xff"
    return bytes(mutated)
