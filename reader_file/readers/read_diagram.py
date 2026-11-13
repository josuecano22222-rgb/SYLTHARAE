"""
Diagram file reader - draw.io / diagrams.net documents (.drawio).

Format contract implemented here (draw.io / diagrams.net, mxGraphModel):

* A ``.drawio`` file is either **plain XML** (an ``mxfile``/``mxGraphModel``
  document) or a **ZIP container** holding ``file.xml`` (the mxfile document)
  and ``metadata.xml``. The sniffer content-verifies the ZIP form
  (``file.xml`` + ``metadata.xml`` entry names) so the router delivers it to
  this reader instead of exploding it through the archive reader, which would
  scatter the diagram into orphan members and lose all page structure.

* Document shape::

      <mxfile>
        <diagram id="..." name="Page 1" [compressed="true"]> ... </diagram>
        ...
      </mxfile>

  Each ``<diagram>`` is one page. The page body is one of:
    - an ``<mxGraphModel>`` child element (uncompressed, the modern default);
    - text content holding the ``mxGraphModel`` XML (some exporters);
    - ``compressed="true"`` with a base64 payload whose decoded bytes are a
      **raw DEFLATE** stream of the UTF-8 ``mxGraphModel`` XML - what draw.io
      produces via pako. Percent-encoded payloads (legacy writers) are
      tolerated, and the decoder tries raw (-15), zlib and gzip wrappers.

  A bare ``<mxGraphModel>`` root with no ``mxfile`` wrapper is treated as a
  single page named after the file.

* Inside ``<mxGraphModel><root>``:
    - cells ``id="0"`` / ``id="1"`` are the document and layer scaffolding -
      not content;
    - ``<object label="...">`` wrappers supply the label (and id) for the
      ``<mxCell>`` they contain;
    - ``vertex="1"`` cells are nodes, ``edge="1"`` cells are edges; edge
      ``source``/``target`` ids are resolved to labels after the walk;
    - ``mxGeometry`` and friends are deliberately dropped: coordinates are
      not searchable content, and the previous behaviour - storing the whole
      raw XML - buried every real label under megabytes of style/geometry
      noise (a 4.4 MB file produced ~155K "words" of which none were the
      actual diagram vocabulary).

* Output: a readable ``content`` string (what the storage pipeline persists
  as ``direct_content``) plus structured ``pages`` / ``counts`` for callers
  and tests. Structured element lists are capped (``MAX_DIAGRAM_ELEMENTS``)
  with an explicit ``truncated`` flag; the readable text keeps every label.
"""

import base64
import logging
import re
import zlib
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import unquote
import xml.etree.ElementTree as ET

from .base_reader import BaseReader

logger = logging.getLogger(__name__)

#: Retained structured elements per file (counts always reflect the truth).
MAX_DIAGRAM_ELEMENTS = 20_000

#: ZIP member inflation guard (a diagram XML bigger than this is not a
#: diagram we can usefully index).
MAX_PAGE_XML_BYTES = 256 * 1024 * 1024

_STRUCTURE_IDS = frozenset({'0', '1'})
_GEOMETRY_TAGS = frozenset({
    'mxGeometry', 'mxPoint', 'mxRectangle', 'mxArc', 'Array',
})


def _local(tag: str) -> str:
    """Strip an XML namespace from a tag name."""
    return tag.rsplit('}', 1)[-1]


class _LabelTextExtractor(HTMLParser):
    """Turn a draw.io HTML label into plain readable text.

    ``<br>``/block boundaries become newlines, markup is dropped, character
    references are decoded, and ``<img alt="...">`` text is kept because
    embedded-image alt text is real diagram vocabulary.
    """

    _BREAKS = frozenset({
        'br', 'p', 'div', 'li', 'ul', 'ol', 'h1', 'h2', 'h3', 'h4', 'h5',
        'h6', 'tr', 'table', 'hr',
    })
    _SKIP = frozenset({'style', 'script', 'head'})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
            return
        if tag in self._BREAKS:
            self.parts.append('\n')
        elif tag == 'img':
            alt = dict(attrs).get('alt')
            if alt:
                self.parts.append(alt)

    def handle_endtag(self, tag):
        if tag in self._SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in self._BREAKS:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self._skip_depth and data:
            self.parts.append(data)


def clean_label(value: Optional[str]) -> str:
    """Normalise a cell label: strip markup, collapse blank lines."""
    if not value:
        return ''
    text = value
    if '<' in text or '&' in text:
        extractor = _LabelTextExtractor()
        try:
            extractor.feed(text)
            extractor.close()
            text = ''.join(extractor.parts)
        except Exception:
            # Malformed markup: fall back to a tag regex so one bad label
            # cannot fail the whole document.
            text = re.sub(r'<[^>]+>', ' ', text)
    lines = [re.sub(r'[ \t\xa0]+', ' ', line).strip() for line in text.split('\n')]
    return '\n'.join(line for line in lines if line)


def _style_shape(style: Optional[str]) -> str:
    """Extract a short shape hint from an mxCell style string."""
    if not style:
        return ''
    for part in style.split(';'):
        part = part.strip()
        if part.startswith('shape='):
            return part.split('=', 1)[1].strip() or ''
    first = style.split(';', 1)[0].strip()
    if first in ('swimlane', 'group', 'ellipse', 'rhombus', 'cylinder',
                 'cloud', 'actor', 'note', 'document', 'step', 'tape',
                 'card', 'callout'):
        return first
    return ''


def _inflate_diagram_payload(payload: bytes) -> str:
    """Decode a base64 ``compressed="true"`` diagram body to XML text.

    Tries raw DEFLATE (pako/draw.io's actual encoding), then zlib, then
    gzip/auto - and validates the result really is a diagram model so a
    mis-selected wrapper cannot silently produce garbage.
    """
    errors: List[str] = []
    for wbits in (-15, 15, 47):
        try:
            raw = zlib.decompress(payload, wbits)
        except zlib.error as exc:
            errors.append(f'wbits={wbits}: {exc}')
            continue
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError as exc:
            errors.append(f'wbits={wbits}: {exc}')
            continue
        if 'mxGraphModel' in text:
            return text
        errors.append(f'wbits={wbits}: decompressed data is not a diagram model')
    raise ValueError('Could not decompress diagram payload (%s)' % '; '.join(errors[:3]))


class DiagramFileReader(BaseReader):
    """Reader for draw.io / diagrams.net ``.drawio`` documents."""

    def get_supported_extensions(self) -> Set[str]:
        """``.drawio`` (current) and ``.dio`` (legacy draw.io)."""
        return {'.drawio', '.dio'}

    def read_file(self, file_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Read a .drawio file (plain XML or ZIP container).

        Args:
            file_info: Dictionary containing file information with 'path' key

        Returns:
            Dictionary with diagram content/pages or an error result.
        """
        is_valid, error_msg = self.validate_file_info(file_info)
        if not is_valid:
            return self.create_error_result(
                error_msg or 'Invalid file info', file_info.get('path', 'unknown')
            )

        file_path = str(file_info.get('path'))
        try:
            return self.read_drawio_file(file_path)
        except Exception as exc:
            return self.handle_read_error(exc, file_path, 'read_file')

    # ------------------------------------------------------------------
    # Container handling
    # ------------------------------------------------------------------

    def read_drawio_file(self, filepath: str) -> Dict[str, Any]:
        """Parse a .drawio file from either container form."""
        try:
            with open(filepath, 'rb') as handle:
                magic = handle.read(4)
        except OSError as exc:
            return self.handle_read_error(exc, filepath, 'read_drawio_file')

        container = 'zip' if magic.startswith(b'PK') else 'xml'
        try:
            if container == 'zip':
                root, metadata = self._load_zip_document(filepath)
            else:
                root, metadata = self._load_plain_document(filepath), None
        except Exception as exc:
            return self.handle_read_error(exc, filepath, '_load_document')

        pages, load_errors = self._parse_document(root, filepath)
        if pages is None:
            return self.handle_read_error(
                ValueError(
                    'Not a draw.io document: no <mxfile> or <mxGraphModel> root'
                ),
                filepath,
                '_parse_document',
            )

        filename = Path(filepath).name
        content_text = self._build_content(filename, pages)
        counts = self._count(pages)
        counts['pages'] = len(pages)
        counts['pages_failed'] = load_errors

        result: Dict[str, Any] = {
            'filepath': filepath,
            'format': 'drawio',
            'container': container,
            'page_count': len(pages),
            'pages': pages,
            'counts': counts,
            'content': content_text,
        }
        if metadata:
            result['drawio_metadata'] = metadata
        return result

    def _load_plain_document(self, filepath: str) -> ET.Element:
        """Read a plain-XML .drawio file (encoding handled by ElementTree)."""
        try:
            with open(filepath, 'rb') as handle:
                head = handle.read(4096)
        except OSError as exc:
            raise ValueError(f'Cannot read {filepath}: {exc}') from exc
        if b'\x00' in head:
            raise ValueError('Binary data in a .drawio file that is not a ZIP container')
        return ET.parse(filepath).getroot()

    def _load_zip_document(self, filepath: str) -> Tuple[ET.Element, Optional[Dict[str, str]]]:
        """Extract the mxfile document (and metadata) from a ZIP .drawio."""
        import zipfile

        with zipfile.ZipFile(filepath) as archive:
            names = archive.namelist()
            lowered = {name.lower(): name for name in names}

            main = lowered.get('file.xml')
            if main is None:
                # Tolerate renamed members: first XML that is not metadata.
                candidates = [
                    original for original in names
                    if original.lower().endswith('.xml')
                    and 'metadata' not in original.lower()
                ]
                if not candidates:
                    raise ValueError('ZIP container has no diagram XML member')
                main = candidates[0]

            info = archive.getinfo(main)
            if info.file_size > MAX_PAGE_XML_BYTES:
                raise ValueError(
                    f'Diagram XML member too large to index ({info.file_size} bytes)'
                )
            xml_bytes = archive.read(main)

            metadata = None
            meta_name = lowered.get('metadata.xml')
            if meta_name is not None:
                try:
                    meta_bytes = archive.read(meta_name)
                    if len(meta_bytes) <= 1 * 1024 * 1024:
                        metadata = self._parse_metadata(meta_bytes)
                except Exception:
                    metadata = None
            return ET.fromstring(xml_bytes), metadata

    @staticmethod
    def _parse_metadata(meta_bytes: bytes) -> Optional[Dict[str, str]]:
        """Best-effort key/value view of metadata.xml (provenance only)."""
        try:
            root = ET.fromstring(meta_bytes)
        except ET.ParseError:
            return None
        metadata: Dict[str, str] = {}
        for child in root:
            text = (child.text or '').strip()
            if text:
                metadata[_local(child.tag)] = text
        return metadata or None

    # ------------------------------------------------------------------
    # Document / page parsing
    # ------------------------------------------------------------------

    def _parse_document(
        self, root: ET.Element, filepath: str = ''
    ) -> Tuple[Optional[List[Dict]], int]:
        """Parse an mxfile/mxGraphModel document into page dicts.

        Returns ``(pages, failed_page_count)``; ``pages is None`` means the
        document root is not a draw.io document at all.
        """
        root_tag = _local(root.tag)
        pages: List[Dict] = []
        failures = 0

        if root_tag == 'mxfile':
            for index, diagram in enumerate(
                (child for child in root if _local(child.tag) == 'diagram'), start=1
            ):
                name = (
                    diagram.get('name')
                    or diagram.get('id')
                    or f'Page {index}'
                )
                try:
                    model_root = self._resolve_diagram_model(diagram)
                    page = self._parse_page(index, name, model_root)
                except Exception as exc:
                    failures += 1
                    page = {
                        'index': index,
                        'name': name,
                        'error': str(exc),
                        'node_count': 0,
                        'edge_count': 0,
                        'nodes': [],
                        'edges': [],
                    }
                pages.append(page)
            if not pages:
                # mxfile wrapper with zero diagrams is still a valid (empty) file.
                pages.append(self._parse_page(1, 'Page 1', self._empty_model()))
            return pages, failures

        if root_tag == 'mxGraphModel':
            stem = Path(filepath).stem if filepath else 'diagram'
            pages.append(self._parse_page(1, stem or 'diagram', root))
            return pages, 0

        return None, 0

    @staticmethod
    def _empty_model() -> ET.Element:
        return ET.fromstring('<mxGraphModel><root/></mxGraphModel>')

    def _resolve_diagram_model(self, diagram: ET.Element) -> ET.Element:
        """Return the ``mxGraphModel`` element for a ``<diagram>`` page."""
        for child in diagram:
            if _local(child.tag) == 'mxGraphModel':
                return child

        text = (diagram.text or '').strip()
        if not text:
            compressed = (diagram.get('compressed') or '').lower()
            raise ValueError(
                f'<diagram> has no model data (compressed={compressed or "unset"})'
            )

        if text.lstrip().startswith('<'):
            parsed = ET.fromstring(text)
            if _local(parsed.tag) == 'mxGraphModel':
                return parsed
            raise ValueError(f'Unexpected diagram payload root <{_local(parsed.tag)}>')

        payload_text = unquote(text) if '%' in text else text
        payload_text = ''.join(payload_text.split())
        payload = base64.b64decode(payload_text)
        inflated = _inflate_diagram_payload(payload)
        parsed = ET.fromstring(inflated)
        if _local(parsed.tag) != 'mxGraphModel':
            raise ValueError(f'Unexpected diagram payload root <{_local(parsed.tag)}>')
        return parsed

    # ------------------------------------------------------------------
    # Cell graph
    # ------------------------------------------------------------------

    @staticmethod
    def _iter_cells(container: ET.Element, pending_label: Optional[str] = None,
                    pending_id: Optional[str] = None):
        """Yield ``(element, wrapper_label, wrapper_id)`` for every cell.

        ``<object label=...>`` wrappers pass their label/id down to the
        ``<mxCell>`` they contain; geometry children are skipped.
        """
        for child in container:
            tag = _local(child.tag)
            if tag in _GEOMETRY_TAGS:
                continue
            if tag == 'object':
                wrapper_label = child.get('label')
                if wrapper_label is None:
                    wrapper_label = pending_label
                wrapper_id = child.get('id') or pending_id
                yielded = False
                for item in DiagramFileReader._iter_cells(child, wrapper_label, wrapper_id):
                    yielded = True
                    yield item
                if not yielded and (child.get('vertex') or child.get('edge')):
                    # The object itself carries the cell semantics.
                    yield child, wrapper_label, wrapper_id
            elif tag == 'mxCell':
                yield child, pending_label, pending_id

    @staticmethod
    def _cell_value(cell: ET.Element, wrapper_label: Optional[str]) -> str:
        value = cell.get('value')
        if value is None:
            value = wrapper_label
        return clean_label(value)

    def _parse_page(self, index: int, name: str, model_root: ET.Element) -> Dict[str, Any]:
        """Walk one page's cell graph into nodes/edges/counts.

        The walk collects *light* records for every cell so counts, label
        resolution and the readable text always reflect the whole page; the
        heavier structured ``nodes``/``edges`` dicts are then built for at
        most ``MAX_DIAGRAM_ELEMENTS`` elements (with ``truncated`` set), the
        same posture the CSV reader takes for row storage.
        """
        root_container = None
        for child in model_root:
            if _local(child.tag) == 'root':
                root_container = child
                break
        if root_container is None:
            root_container = model_root

        light_nodes: List[Tuple[str, str, str, str]] = []  # id, label, parent, shape
        light_edges: List[Tuple[str, str, str, str]] = []  # id, label, source, target
        id_to_label: Dict[str, str] = {}

        for cell, wrapper_label, wrapper_id in self._iter_cells(root_container):
            cell_id = cell.get('id') or wrapper_id or ''
            value = self._cell_value(cell, wrapper_label)
            vertex = cell.get('vertex')
            edge = cell.get('edge')

            if not vertex and not edge:
                # id 0/1 and plain layer scaffolding: structural only. Still
                # record its label for id lookups (layer names can appear).
                if cell_id and value:
                    id_to_label.setdefault(cell_id, value)
                continue

            if cell_id:
                id_to_label.setdefault(cell_id, value)
                if cell_id in _STRUCTURE_IDS:
                    continue

            if edge:
                light_edges.append((
                    cell_id, value,
                    cell.get('source') or '', cell.get('target') or '',
                ))
            else:
                light_nodes.append((
                    cell_id, value,
                    cell.get('parent') or '',
                    _style_shape(cell.get('style')),
                ))

        total_nodes = len(light_nodes)
        total_edges = len(light_edges)
        truncated = total_nodes + total_edges > MAX_DIAGRAM_ELEMENTS

        node_ids = {node[0] for node in light_nodes if node[0]}

        # Depths from the parent chain (draw.io cells reference parents by id).
        depth_cache: Dict[str, int] = {}
        parents = {node[0]: node[2] for node in light_nodes if node[0]}

        def depth_of(node_id: str, guard: int = 0) -> int:
            if not node_id or guard > 64:
                return 0
            if node_id in depth_cache:
                return depth_cache[node_id]
            parent = parents.get(node_id, '')
            if not parent or parent not in parents or parent == node_id:
                depth_cache[node_id] = 0
            else:
                depth_cache[node_id] = min(depth_of(parent, guard + 1) + 1, 64)
            return depth_cache[node_id]

        child_counts: Dict[str, int] = {}
        for node in light_nodes:
            if node[2] in node_ids:
                child_counts[node[2]] = child_counts.get(node[2], 0) + 1

        # Structured dicts: capped. Readable text below uses the FULL lists.
        stored_nodes = light_nodes[:MAX_DIAGRAM_ELEMENTS]
        stored_edge_count = max(0, MAX_DIAGRAM_ELEMENTS - len(stored_nodes))
        stored_edge_index = set(range(min(stored_edge_count, total_edges)))

        nodes = []
        for node_id, label, parent, shape in stored_nodes:
            nodes.append({
                'id': node_id,
                'label': label,
                'parent': parent,
                'depth': depth_of(node_id) if node_id else 0,
                'shape': shape,
                'is_container': bool(node_id and child_counts.get(node_id)),
            })

        # Resolve every edge once: label, or the source/target labels when
        # the edge itself is unlabelled.
        edge_texts: List[str] = []
        edges = []
        for position, (edge_id, label, source, target) in enumerate(light_edges):
            source_label = id_to_label.get(source, '')
            target_label = id_to_label.get(target, '')
            text = label
            if not text and (source_label or target_label):
                text = f'{source_label} → {target_label}' if (
                    source_label and target_label
                ) else (source_label or target_label)
            if text:
                edge_texts.append(text)
            if position in stored_edge_index:
                edges.append({
                    'id': edge_id,
                    'label': label,
                    'source': source,
                    'target': target,
                    'source_label': source_label,
                    'target_label': target_label,
                    'text': text,
                })

        labelled_nodes = sum(1 for node in light_nodes if node[1])
        labelled_edges = len(edge_texts)

        # Full readable text for this page: header + every labelled node
        # (indent by hierarchy depth) + every resolvable edge. Labels beyond
        # the structured cap remain searchable here.
        page_name = name or f'Page {index}'
        page_lines: List[str] = [f'Page: {page_name}']
        for node_id, label, _parent, _shape in light_nodes:
            if not label:
                continue
            indent = '  ' * max(0, depth_of(node_id) if node_id else 0)
            page_lines.append(f'{indent}{label}')
        if edge_texts:
            page_lines.append('Edges:')
            page_lines.extend(f'  {text}' for text in edge_texts)

        page: Dict[str, Any] = {
            'index': index,
            'name': page_name,
            'node_count': total_nodes,
            'edge_count': total_edges,
            'labelled_node_count': labelled_nodes,
            'labelled_edge_count': labelled_edges,
            'nodes': nodes,
            'edges': edges,
            'page_text': '\n'.join(page_lines),
        }
        if truncated:
            page['truncated'] = True
            page['truncation_note'] = (
                f'structured element lists capped at {MAX_DIAGRAM_ELEMENTS}; '
                f'counts above reflect the full page and every label is still '
                f'present in the readable content'
            )
        return page

    # ------------------------------------------------------------------
    # Readable output
    # ------------------------------------------------------------------

    @staticmethod
    def _build_content(filename: str, pages: List[Dict[str, Any]]) -> str:
        """Render pages/nodes/edges as the searchable document text."""
        sections: List[str] = []
        for page in pages:
            if page.get('error'):
                page_name = page.get('name') or f"Page {page.get('index', 1)}"
                sections.append(
                    f'Page: {page_name} (unreadable diagram data: {page["error"]})'
                )
                continue
            sections.append(page.get('page_text') or '')
        return '\n\n'.join(sections).strip()

    @staticmethod
    def _count(pages: List[Dict[str, Any]]) -> Dict[str, int]:
        counts = {
            'nodes': 0, 'edges': 0, 'labelled_nodes': 0, 'labelled_edges': 0,
            'nodes_stored': 0, 'edges_stored': 0,
        }
        for page in pages:
            counts['nodes'] += page.get('node_count', 0)
            counts['edges'] += page.get('edge_count', 0)
            counts['labelled_nodes'] += page.get('labelled_node_count', 0)
            counts['labelled_edges'] += page.get('labelled_edge_count', 0)
            counts['nodes_stored'] += len(page.get('nodes', ()))
            counts['edges_stored'] += len(page.get('edges', ()))
        return counts
