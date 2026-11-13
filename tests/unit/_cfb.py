"""A minimal Compound File Binary writer, for building test fixtures.

Legacy Office documents (``.doc``/``.xls``/``.ppt``/``.msg``) are OLE compound
files, and the identification and macro paths have to be tested against real
ones - a hand-waved byte blob would prove nothing about whether the FIB flag,
the BIFF document type or the MAPI stream scan actually works.

This writer produces files that `olefile` (and therefore `oletools`, and the
production detection path) reads back, including the 64-byte mini-stream
handling that real documents use for their small streams.

Test-only. Nothing in the package imports it.
"""

from __future__ import annotations

import struct

SECTOR_SIZE = 512
FREESECT = 0xFFFFFFFF
ENDOFCHAIN = 0xFFFFFFFE
FATSECT = 0xFFFFFFFD
NOSTREAM = 0xFFFFFFFF

_HEADER_SIZE = 512
_DIR_ENTRY_SIZE = 128
_MAX_DEPTH = 8


def _directory_entry(name: str, object_type: int, start_sector: int, size: int,
                     child: int = NOSTREAM, right: int = NOSTREAM,
                     left: int = NOSTREAM) -> bytes:
    encoded = name.encode("utf-16-le") + b"\x00\x00"
    if len(encoded) > 64:
        raise ValueError(f"CFB entry name too long: {name!r}")
    entry = bytearray(_DIR_ENTRY_SIZE)
    entry[0:len(encoded)] = encoded
    struct.pack_into("<H", entry, 0x40, len(encoded))
    entry[0x42] = object_type
    entry[0x43] = 1  # colour: black
    struct.pack_into("<I", entry, 0x44, left)      # left sibling
    struct.pack_into("<I", entry, 0x48, right)      # right sibling
    struct.pack_into("<I", entry, 0x4C, child)      # child
    struct.pack_into("<I", entry, 0x74, start_sector)
    struct.pack_into("<Q", entry, 0x78, size)
    return bytes(entry)


def build_compound_file(path: str, streams: dict, storages: dict | None = None) -> str:
    """Write a compound file containing ``streams`` (name -> bytes).

    Args:
        path: Destination path.
        streams: ``{'WordDocument': b'...', '_VBA_PROJECT_CUR/VBA/dir': b'...'}``.
            A name with ``/`` creates the storage chain it names, at any depth -
            which is how the legacy macro storage (``_VBA_PROJECT_CUR/VBA/dir``)
            and MAPI property streams are represented.
        storages: Additional storage paths to create even when empty.

    Returns:
        ``path``.
    """
    # --- build the directory tree -----------------------------------------
    tree: dict = {"children": {}, "kind": "storage"}

    def _node(path_parts):
        node = tree
        for part in path_parts[:_MAX_DEPTH]:
            node = node["children"].setdefault(
                part, {"children": {}, "kind": "storage"})
        return node

    for name in sorted(storages or {}):
        _node(name.split("/"))["kind"] = "storage"
    for name, payload in streams.items():
        parts = name.split("/")
        _node(parts[:-1])["children"][parts[-1]] = {"kind": "stream", "data": payload}

    # --- allocate stream storage ------------------------------------------
    # Streams smaller than the 4096-byte mini-stream cutoff live in the mini
    # stream (64-byte mini sectors chained through the mini FAT); larger ones
    # are chained through the regular FAT. Both are needed for a file that
    # `olefile` reads exactly as it reads a real document.
    fat: list[int] = []
    mini_fat: list[int] = []
    body = bytearray()
    mini_stream = bytearray()
    next_sector = 0
    allocation: dict[int, tuple[int, int, bool]] = {}   # id(node) -> (start, size, mini)

    def _allocate_large(data: bytes) -> tuple[int, int]:
        nonlocal next_sector
        start = next_sector
        padded = data + b"\x00" * ((SECTOR_SIZE - len(data) % SECTOR_SIZE) % SECTOR_SIZE)
        count = len(padded) // SECTOR_SIZE
        body.extend(padded)
        for index in range(count):
            fat.append(start + index + 1 if index < count - 1 else ENDOFCHAIN)
        next_sector += count
        return start, len(data)

    def _allocate_mini(data: bytes) -> tuple[int, int]:
        start = len(mini_fat)
        count = (len(data) + 63) // 64
        for index in range(count):
            mini_fat.append(start + index + 1 if index < count - 1 else ENDOFCHAIN)
        padded = data + b"\x00" * (count * 64 - len(data))
        mini_stream.extend(padded)
        return start, len(data)

    def _allocate_all(node: dict) -> None:
        for _name, child in node["children"].items():
            if child["kind"] == "stream":
                data = child["data"]
                if not data:
                    allocation[id(child)] = (ENDOFCHAIN, 0, False)
                elif len(data) < 4096:
                    start, size = _allocate_mini(data)
                    allocation[id(child)] = (start, size, True)
                else:
                    start, size = _allocate_large(data)
                    allocation[id(child)] = (start, size, False)
            else:
                _allocate_all(child)

    entries: list[bytes] = []

    def _sort_key(item):
        """CFB directory ordering: by name length, then uppercase code units."""
        name = item[0]
        return (len(name), name.upper())

    def _emit_children(node: dict) -> int:
        """Write a storage's child tree; returns the root child's index.

        The directory is a binary search tree ordered by the CFB name rules
        (:func:`_sort_key`): insertion here is balanced rather than sorted, so
        the tree is valid and name lookups resolve.
        """
        children = sorted(node["children"].items(), key=_sort_key)
        if not children:
            return NOSTREAM

        def _build(lo: int, hi: int):
            if lo > hi:
                return None
            mid = (lo + hi) // 2
            return {"item": children[mid], "left": _build(lo, mid - 1),
                    "right": _build(mid + 1, hi)}

        def _assign(tree) -> int:
            if tree is None:
                return NOSTREAM
            index = len(entries)
            entries.append(b"")
            left = _assign(tree["left"])
            right = _assign(tree["right"])
            name, child = tree["item"]
            if child["kind"] == "stream":
                start, size, _mini = allocation[id(child)]
                entries[index] = _directory_entry(name, 2, start, size,
                                                  right=right, left=left)
            else:
                grandchild = _emit_children(child)
                entries[index] = _directory_entry(name, 1, 0, 0,
                                                  child=grandchild, right=right,
                                                  left=left)
            return index

        return _assign(_build(0, len(children) - 1))

    _allocate_all(tree)
    # The mini stream itself is a regular stream, owned by the root entry.
    mini_start, mini_size = _allocate_large(bytes(mini_stream)) if mini_stream else (ENDOFCHAIN, 0)

    entries.append(b"")  # root placeholder at index 0
    root_child = _emit_children(tree)
    root = bytearray(_directory_entry("Root Entry", 5, mini_start, mini_size,
                                      child=root_child))
    entries[0] = bytes(root)

    directory_bytes = b"".join(entries)
    directory_bytes += b"\x00" * ((SECTOR_SIZE - len(directory_bytes) % SECTOR_SIZE) % SECTOR_SIZE)
    directory_sector = next_sector
    directory_count = len(directory_bytes) // SECTOR_SIZE
    for index in range(directory_count):
        fat.append(directory_sector + index + 1 if index < directory_count - 1 else ENDOFCHAIN)
    next_sector += directory_count

    # --- mini FAT ----------------------------------------------------------
    mini_fat_sector = ENDOFCHAIN
    mini_fat_sector_count = 0
    if mini_fat:
        entries_per_sector = SECTOR_SIZE // 4
        mini_fat_sector_count = (len(mini_fat) + entries_per_sector - 1) // entries_per_sector
        mini_fat_sector = next_sector
        for index in range(mini_fat_sector_count):
            fat.append(mini_fat_sector + index + 1 if index < mini_fat_sector_count - 1
                       else ENDOFCHAIN)
        next_sector += mini_fat_sector_count
        padded_entries = mini_fat + [FREESECT] * (mini_fat_sector_count * entries_per_sector
                                                  - len(mini_fat))
        mini_fat_bytes = b"".join(struct.pack("<I", entry) for entry in padded_entries)
    else:
        mini_fat_bytes = b""

    # --- FAT ---------------------------------------------------------------
    fat_sector = next_sector
    fat_sector_count = 1
    # FAT entries needed: every sector so far plus the FAT sector(s) themselves,
    # rounded up to a whole number of 512-byte FAT sectors (128 entries each).
    fat_entry_count = ((fat_sector + fat_sector_count + 127) // 128) * 128
    fat_entries = list(fat) + [FREESECT] * (fat_entry_count - len(fat))
    for index in range(fat_sector_count):
        fat_entries[fat_sector + index] = FATSECT
    fat_bytes = b"".join(struct.pack("<I", entry) for entry in fat_entries)

    header = bytearray(_HEADER_SIZE)
    header[0:8] = bytes.fromhex("d0cf11e0a1b11ae1")
    struct.pack_into("<H", header, 0x18, 0x003E)          # minor version
    struct.pack_into("<H", header, 0x1A, 0x0003)          # major version (512-byte sectors)
    struct.pack_into("<H", header, 0x1C, 0xFFFE)          # little-endian
    struct.pack_into("<H", header, 0x1E, 9)               # sector shift
    struct.pack_into("<H", header, 0x20, 6)               # mini sector shift
    struct.pack_into("<I", header, 0x28, 0)               # directory sector count (v3: 0)
    struct.pack_into("<I", header, 0x2C, fat_sector_count)  # FAT sector count
    struct.pack_into("<I", header, 0x30, directory_sector)  # first directory sector
    struct.pack_into("<I", header, 0x34, 0)               # transaction signature
    struct.pack_into("<I", header, 0x38, 4096)            # mini-stream cutoff
    struct.pack_into("<I", header, 0x3C, mini_fat_sector)  # first mini FAT sector
    struct.pack_into("<I", header, 0x40, mini_fat_sector_count)  # mini FAT sector count
    struct.pack_into("<I", header, 0x44, ENDOFCHAIN)      # first DIFAT sector
    struct.pack_into("<I", header, 0x48, 0)               # DIFAT sector count
    difat = [FREESECT] * 109
    for index in range(fat_sector_count):
        difat[index] = fat_sector + index
    for index, entry in enumerate(difat):
        struct.pack_into("<I", header, 0x4C + index * 4, entry)

    with open(path, "wb") as handle:
        handle.write(bytes(header))
        handle.write(bytes(body))
        handle.write(directory_bytes)
        handle.write(mini_fat_bytes)
        handle.write(fat_bytes)
    return path
