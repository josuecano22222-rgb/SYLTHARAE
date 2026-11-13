import os
from pathlib import Path
from typing import Optional, Tuple, Union

# Confidence levels reported by :func:`detect_file_type_with_confidence`.
#
# ``strong`` - a magic-byte / container signature matched. Content evidence is
#              authoritative and MAY override a disagreeing file extension.
# ``weak``   - only a textual heuristic matched (``<?xml``, ``<html``, ``{``).
#              Used to fill a gap when there is no usable extension, never to
#              override one.
# ``none``   - nothing matched; the caller must fall back to the extension.
CONFIDENCE_STRONG = 'strong'
CONFIDENCE_WEAK = 'weak'
CONFIDENCE_NONE = 'none'

# Default header size used when sniffing a file from disk. Large enough to
# cover the deepest fixed-offset signature we test (ISO9660 at 0x8801) and
# the OOXML/OLE container probes below, bounded so a huge file never forces a
# full read into memory.
SNIFF_HEADER_SIZE = 0x9000


def read_file_header(path: str, size: int = SNIFF_HEADER_SIZE) -> bytes:
    """Read at most ``size`` leading bytes of ``path``.

    Binary-safe and streaming: a multi-gigabyte file costs one bounded read,
    never a full load. Any filesystem failure (missing file, directory,
    permission denied, I/O error, vanished mid-read) yields ``b''`` so callers
    degrade to extension-based identification instead of raising.

    Args:
        path: Path to the file.
        size: Maximum number of bytes to read.

    Returns:
        The leading bytes, or ``b''`` if nothing could be read.
    """
    try:
        with open(path, 'rb') as handle:
            return handle.read(size) or b''
    except Exception:
        return b''


#: Extensions whose *payload* can contain OOXML/ODF markers. When one of these
#: is an entry of the outer ZIP, markers found inside its data belong to the
#: nested document, not to the archive.
_NESTED_DOCUMENT_EXTENSIONS = (
    '.docx', '.xlsx', '.pptx', '.odt', '.ods', '.odp', '.epub',
    '.docm', '.xlsm', '.pptm', '.dotx', '.xltm',
)


def _walk_zip_local_headers(
    data: Union[bytes, bytearray, memoryview]
) -> Tuple[list, bool]:
    """Enumerate ZIP entry names from the local file headers in ``data``.

    Returns ``(entries, truncated)`` where ``entries`` is a list of
    ``(name, data_start, data_end)`` and ``truncated`` says whether the walk
    stopped because the buffer ran out (so the entry list may be incomplete).

    Local file header layout (PK\x03\x04):
        6..8    general purpose flags (bit 3 => sizes live in a data descriptor)
        18..22  compressed size
        26..28  file name length
        28..30  extra field length
        30..    file name, extra field, then the entry payload
    """
    entries: list = []
    raw = bytes(data)
    offset = 0
    limit = len(raw)
    while offset + 30 <= limit:
        if raw[offset:offset + 4] == b'PK\x01\x02':
            # Central directory - every local header has been seen.
            return entries, False
        if raw[offset:offset + 4] != b'PK\x03\x04':
            break
        try:
            flags = int.from_bytes(raw[offset + 6:offset + 8], 'little')
            comp_size = int.from_bytes(raw[offset + 18:offset + 22], 'little')
            name_len = int.from_bytes(raw[offset + 26:offset + 28], 'little')
            extra_len = int.from_bytes(raw[offset + 28:offset + 30], 'little')
        except Exception:
            break
        if name_len <= 0:
            break
        name_start = offset + 30
        name_end = name_start + name_len
        if name_end > limit:
            return entries, True
        name = raw[name_start:name_end].decode('utf-8', errors='replace')
        data_start = name_end + extra_len
        if flags & 0x08:
            # Sizes are in a trailing data descriptor we cannot locate without
            # decompressing; the enumeration cannot continue reliably.
            entries.append((name, data_start, limit))
            return entries, True
        data_end = data_start + comp_size
        entries.append((name, data_start, min(data_end, limit)))
        if data_end > limit:
            return entries, True
        offset = data_end
    return entries, offset < limit


def _zip_container_names(
    data: Union[bytes, bytearray, memoryview],
    path: Optional[str] = None,
) -> Tuple[Optional[list], bool]:
    """Authoritative top-level entry names of a ZIP container.

    Prefers the central directory via :mod:`zipfile` (complete regardless of
    producer or entry order); falls back to walking the local headers when no
    path is available. Returns ``(names_or_None, truncated)``.
    """
    if path:
        try:
            import zipfile
            with zipfile.ZipFile(path) as zf:
                return zf.namelist(), False
        except Exception:
            pass  # corrupt/encrypted/streamed - fall through to the byte walk
    entries, truncated = _walk_zip_local_headers(data)
    if not entries:
        return None, truncated
    return [name for name, _, _ in entries], truncated


def sniff_file_type(path: str, size: int = SNIFF_HEADER_SIZE) -> Tuple[str, str]:
    """Identify a file's type from its on-disk content.

    Args:
        path: Path to the file.
        size: Maximum number of leading bytes to inspect.

    Returns:
        ``(extension, confidence)`` - see :func:`detect_file_type_with_confidence`.
        An unreadable file returns ``('.bin', 'none')``.
    """
    header = read_file_header(path, size)
    if not header:
        return '.bin', CONFIDENCE_NONE
    return detect_file_type_with_confidence(header, path)


def detect_file_type_with_confidence(
    data: Union[bytes, bytearray, memoryview],
    path: Optional[str] = None,
) -> Tuple[str, str]:
    """Identify a byte string's type, reporting how much evidence backs it.

    Args:
        data: Leading bytes of a file.
        path: Optional path the bytes came from. Used to consult a ZIP container's
            central directory, which is authoritative for telling a document
            package apart from an archive that merely contains one.

    Returns:
        ``(extension, confidence)`` where confidence is one of
        :data:`CONFIDENCE_STRONG`, :data:`CONFIDENCE_WEAK` or
        :data:`CONFIDENCE_NONE`.
    """
    if not data:
        return '.bin', CONFIDENCE_NONE
    if isinstance(data, (bytearray, memoryview)):
        data = bytes(data)
    if len(data) < 2:
        return '.bin', CONFIDENCE_NONE

    # Offset checks
    if len(data) > 132 and data[128:132] == b'DICM':
        return '.dcm', CONFIDENCE_STRONG
    if len(data) > 262 and data[257:262] == b'ustar':
        return '.tar', CONFIDENCE_STRONG
    if len(data) > 0x8806 and (data[0x8001:0x8006] == b'CD001' or data[0x8801:0x8806] == b'CD001'):
        return '.iso', CONFIDENCE_STRONG
    
    # Signatures: (bytes, ext, len)
    sigs = [
        (b'\xE4\x52\x5C\x7B\x8C\xD8\xA7\x4D\xAE\xB1\x53\x78\xD0\x29\x96\xD3','.one',16),(b'%PDF','.pdf',4),(b'\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1','.ole',8),
        (b'{\\rtf','.rtf',4),(b'\xDB\xA5-\x00\x00\x00','.wp',6),(b'\x00\x01\x00\x00Standard ACE DB','.accdb',19),(b'\x00\x01\x00\x00Standard Jet DB','.mdb',19),
        (b'SQLite format 3\x00','.sqlite',16),(b'ITSF','.chm',4),(b'!BDN','.pst',4),(b'\x21\x42\x4E\x41','.ost',4),(b'\x4C\x00\x00\x00\x01\x14\x02\x00','.lnk',8),
        (b'regf','.hiv',4),(b'MSCF','.cab',4),(b'ISc(','.cab',4),(b'MSWIM\x00\x00\x00','.wim',8),(b'conectix','.vhd',8),(b'vhdxfile','.vhdx',8),
        (b'KDMV','.vmdk',4),(b'\xD7\xCD\xC6\x9A','.wmf',4),(b'LfLe','.evt',4),(b'ElfFile\x00','.evtx',8),(b'7z\xBC\xAF\x27\x1C','.7z',6),
        (b'Rar!\x1A\x07\x01\x00','.rar',8),(b'Rar!\x1A\x07\x00','.rar',7),(b'Rar!\x1A\x07','.rar',6),(b'\xFD7zXZ\x00','.xz',6),(b'\x1F\x8B','.gz',2),
        (b'BZh','.bz2',3),(b'PK\x07\x08','.zip',4),(b'PK\x05\x06','.zip',4),(b'PK\x03\x04','.zip',4),(b'\x1F\x9D','.Z',2),(b'\x89PNG\r\n\x1a\n','.png',8),
        (b'\xFF\xD8\xFF\xDB','.jpg',4),(b'\xFF\xD8\xFF\xE0\x00\x10JFIF','.jpg',11),(b'\xFF\xD8\xFF\xE1','.jpg',4),(b'\xFF\xD8\xFF\xE0','.jpg',4),
        (b'\xFF\xD8\xFF','.jpg',3),(b'GIF89a','.gif',6),(b'GIF87a','.gif',6),(b'BM','.bmp',2),(b'RIFF','.webp',4),(b'MM\x00*','.tiff',4),
        (b'II*\x00','.tiff',4),(b'\x00\x00\x01\x00','.ico',4),(b'8BPS','.psd',4),(b'ID3','.mp3',3),(b'\xFF\xFB','.mp3',2),(b'OggS','.ogg',4),
        (b'fLaC','.flac',4),(b'MThd','.midi',4),(b'\x00\x00\x00\x20ftyp','.mp4',8),(b'\x00\x00\x00\x1Cftyp','.mp4',8),(b'\x00\x00\x00\x18ftyp','.mp4',8),
        (b'\x1A\x45\xDF\xA3','.mkv',4),(b'FLV\x01','.flv',4),(b'\x30\x26\xB2\x75\x8E\x66\xCF\x11','.wmv',8),(b'\x00\x00\x01\xBA','.mpeg',4),
        (b'\x7FELF','.elf',4),(b'\xCA\xFE\xBA\xBE','.mach-o',4),(b'MZ','.exe',2),(b'#!','.sh',2),(b'wOFF','.woff',4),(b'\x00\x01\x00\x00\x00','.ttf',5),
    ]
    
    #: Extensions that name the same format, mapped from the extension the
    #: signature table reports to the spellings a file may declare.  A JPEG is
    #: commonly written ``.jpg`` or ``.jpeg``; reporting the canonical ``.jpg``
    #: for a file that declares ``.jpeg`` is not a contradiction, and treating
    #: it as one made every ``.jpeg`` file in a corpus emit a "declared
    #: extension .jpeg contradicted by content signature .jpg" warning (five
    #: such warnings per image in the production log).
    #:
    #: Only spellings that resolve to a registered reader belong here: the
    #: declared alias is reported back as the file's type, so an alias with no
    #: reader (``.jfif``) would divert the file away from the reader that
    #: handles its content.
    extension_aliases = {'.jpg': ('.jpeg',), '.tiff': ('.tif',), '.mpeg': ('.mpg',)}

    for sig, ext, ml in sigs:
        if len(data) >= ml and data.startswith(sig):
            declared_extension = os.path.splitext(str(path))[1].lower() if path else ''
            for canonical, aliases in extension_aliases.items():
                if ext == canonical and declared_extension in aliases:
                    ext = declared_extension
                    break
            if sig == b'PK\x03\x04' and len(data) > 500:
                s = data[:4096].decode('latin-1', errors='ignore').lower()
                # OOXML / EPUB containers are ZIPs; name them by payload so the
                # router reaches the document reader, not the archive reader.
                #
                # BUGFIX: this used to test only for the *substring*
                # '[content_types].xml' anywhere in the first 4 KiB. A plain ZIP
                # that contains a document stores that document's bytes inline
                # (ZIP_STORED is zipfile's default), so the nested file's own
                # OOXML markers matched and the outer archive was declared a
                # .docx. The OfficeFileReader then failed with
                # "There is no item named '[Content_Types].xml' in the archive"
                # and the archive was never expanded at all - every member,
                # attachment and embedded object below it silently vanished from
                # the processing workload and from progress accounting.
                #
                # The formats are actually defined by their FIRST entry, so test
                # that instead: '[Content_Types].xml' for OOXML, an uncompressed
                # 'mimetype' for ODF/EPUB. An ordinary ZIP containing a document
                # has that document's name first and is now correctly left alone.
                # Decide from the container's actual entry names, not from a
                # substring search over raw bytes. ``[Content_Types].xml`` must
                # be a top-level ENTRY of this ZIP for it to be an OOXML
                # package - a plain archive holding a .docx contains that string
                # only as the nested file's payload. Entry order is not a
                # usable test: python-docx writes it first, openpyxl does not.
                names, truncated = _zip_container_names(data, path)
                lowered = [n.lower() for n in names] if names else []

                if lowered:
                    is_ooxml = '[content_types].xml' in lowered
                    is_mimetype_first = lowered[0] == 'mimetype'
                else:
                    # The header is not an enumerable ZIP (synthetic fixture,
                    # streamed with data descriptors, or too truncated to walk).
                    # Fall back to raw substring evidence rather than losing
                    # ODF/EPUB recognition: a real container that cannot be
                    # parsed is still better named by its payload markers.
                    is_ooxml = '[content_types].xml' in s
                    is_mimetype_first = True

                if not is_ooxml and '[content_types].xml' in s and truncated:
                    # The enumeration was cut short by the sniff window. Only
                    # fall back to the old substring test when the marker is
                    # NOT attributable to a nested document's payload.
                    entries, _ = _walk_zip_local_headers(data)
                    marker_at = s.find('[content_types].xml')
                    inside_nested_doc = any(
                        name.lower().endswith(_NESTED_DOCUMENT_EXTENSIONS)
                        and start <= marker_at < end
                        for name, start, end in entries
                    )
                    is_ooxml = not inside_nested_doc

                if is_ooxml:
                    if 'word/' in s:
                        return '.docx', CONFIDENCE_STRONG
                    if 'xl/' in s or 'worksheets/' in s:
                        return '.xlsx', CONFIDENCE_STRONG
                    if 'ppt/' in s or 'slides/' in s:
                        return '.pptx', CONFIDENCE_STRONG
                if is_mimetype_first:
                    # OpenDocument and EPUB both store their media type in the
                    # first, uncompressed `mimetype` member.
                    if 'opendocument.text' in s:
                        return '.odt', CONFIDENCE_STRONG
                    if 'opendocument.spreadsheet' in s:
                        return '.ods', CONFIDENCE_STRONG
                    if 'opendocument.presentation' in s:
                        return '.odp', CONFIDENCE_STRONG
                    if 'epub+zip' in s:
                        return '.epub', CONFIDENCE_STRONG
                # draw.io / diagrams.net .drawio is a ZIP containing exactly
                # file.xml (the mxfile document) + metadata.xml. Name it by
                # payload so the router reaches the diagram reader: the
                # archive reader would explode the document into orphan
                # members and lose every trace of its page structure.
                if (
                    not is_ooxml
                    and 'file.xml' in lowered
                    and 'metadata.xml' in lowered
                ):
                    return '.drawio', CONFIDENCE_STRONG
                return '.zip', CONFIDENCE_STRONG
            elif sig == b'RIFF' and len(data) > 12:
                t = data[8:12]
                if t == b'WEBP':
                    return '.webp', CONFIDENCE_STRONG
                if t == b'WAVE':
                    return '.wav', CONFIDENCE_STRONG
                if t == b'AVI ':
                    return '.avi', CONFIDENCE_STRONG
                # Recognised container, unknown payload. Report the container
                # rather than guessing '.webp', so the caller can fall back to
                # the declared extension instead of mis-routing.
                return '.riff', CONFIDENCE_STRONG
            elif sig == b'\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1' and len(data) > 512:
                # OLE compound files store directory entry names as UTF-16LE,
                # so an ASCII-only probe never matches. Check both encodings.
                s = data[:8192]
                s_lower = s.lower()
                u16 = s.decode('utf-16-le', errors='ignore').lower()

                def _has(name: str) -> bool:
                    encoded = name.encode('utf-16-le')
                    return (name.lower().encode() in s_lower
                            or encoded in s
                            or name.lower() in u16)

                if _has('WordDocument'):
                    return '.doc', CONFIDENCE_STRONG
                if _has('Workbook'):
                    return '.xls', CONFIDENCE_STRONG
                if _has('PowerPoint'):
                    return '.ppt', CONFIDENCE_STRONG
                if _has('__substg1.0_'):
                    return '.msg', CONFIDENCE_STRONG
                return '.ole', CONFIDENCE_STRONG
            return ext, CONFIDENCE_STRONG

    if len(data) > 10:
        try:
            t = data[:500].decode('utf-8', errors='ignore').lstrip().lower()
            if t.startswith('<?xml'):
                return ('.svg' if '<svg' in t else '.xml'), CONFIDENCE_WEAK
            if t.startswith('<!doctype html') or t.startswith('<html'):
                return '.html', CONFIDENCE_WEAK
            if t.startswith('{') and '"' in t:
                return '.json', CONFIDENCE_WEAK
        except Exception:
            pass

    return '.bin', CONFIDENCE_NONE


def detect_file_type(data: Union[bytes, bytearray, memoryview]) -> str:
    """Return the detected extension for ``data`` (content-based).

    Thin wrapper over :func:`detect_file_type_with_confidence` for callers that
    only need the extension.
    """
    return detect_file_type_with_confidence(data)[0]



def get_filename_with_correct_extension(filename: str, data: Union[bytes, bytearray, memoryview]) -> str:
    """
    Ensure a filename has the correct extension based on file content detection.
    If the filename already has an extension that matches the detected type, keep it.
    Otherwise, replace or add the correct extension.
    
    Args:
        filename: Original filename (may have wrong or no extension)
        data: File content bytes
    
    Returns:
        Filename with correct extension
    """
    if not filename:
        detected_ext = detect_file_type(data)
        return f"attachment{detected_ext}"
    
    path = Path(filename)
    current_ext = path.suffix.lower()
    detected_ext = detect_file_type(data)
    
    # If no extension or extension is .bin, use detected extension
    if not current_ext or current_ext == '.bin':
        return f"{path.stem}{detected_ext}"
    
    # If current extension matches detected, keep it
    if current_ext == detected_ext:
        return filename
    
    # If detected type is more specific than .bin, use it
    if detected_ext != '.bin':
        # Check if current extension is a generic one
        generic_extensions = {'.bin', '.dat', '.tmp', '.file', '.attachment'}
        if current_ext in generic_extensions:
            return f"{path.stem}{detected_ext}"
        # If both are specific but different, prefer detected (it's from magic bytes)
        # But keep original if it's a known good extension
        known_good = {'.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', 
                     '.jpg', '.jpeg', '.png', '.gif', '.zip', '.rar', '.7z'}
        if current_ext not in known_good and detected_ext != '.bin':
            return f"{path.stem}{detected_ext}"
    
    # Default: keep original filename
    return filename
