"""Unit: the .drawio diagram reader (DRAWIO-01).

Before this reader existed, ``.drawio`` was advertised by no reader: plain
XML documents fell through to the generic XML/text path (their raw markup
- style strings, geometry, ids - became the "content", ~155K words of noise
for a 4.4 MB file) and ZIP-form documents were content-verified as ``.zip``
and exploded into orphan members by the archive reader, losing every trace
of page structure. These tests pin the contract:

* both container forms route to ``DiagramFileReader`` (ZIP is sniffer-
  verified so the archive reader never sees it);
* compressed ``<diagram>`` payloads (base64 + raw DEFLATE) decode;
* HTML labels become clean text, ``<object>`` wrappers supply labels,
  edge endpoints resolve to labels;
* the readable ``content`` carries every label even when the structured
  element lists hit their cap - counts always reflect the full page;
* storage's searchable-text extractor picks the labels up unchanged.
"""

import base64
import sys
import zipfile
import zlib
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.detect_binanry_utils import sniff_file_type  # noqa: E402
from reader_file.readers.read_diagram import (  # noqa: E402
    DiagramFileReader,
    clean_label,
)
from reader_file.services.file_reader_service import FileReaderService  # noqa: E402
from reader_file.services.file_router_service import FileRouterService  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def compressed_diagram_payload(model_xml: str) -> str:
    """Encode an mxGraphModel exactly the way draw.io does: raw DEFLATE."""
    compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
    payload = compressor.compress(model_xml.encode('utf-8')) + compressor.flush()
    return base64.b64encode(payload).decode('ascii')


PAGE_1_MODEL = '''<mxGraphModel dx="800" dy="400" grid="1" page="1">
  <root>
    <mxCell id="0"/>
    <mxCell id="1" parent="0"/>
    <mxCell id="c1" value="Architecture Layer" style="swimlane;html=1;" vertex="1" parent="1">
      <mxGeometry x="40" y="40" width="260" height="160" as="geometry"/>
    </mxCell>
    <mxCell id="n3" value="&lt;b&gt;API &amp;amp; Gateway&lt;/b&gt;&lt;br&gt;second line" style="rounded=0;html=1;" vertex="1" parent="c1">
      <mxGeometry x="80" y="80" width="120" height="60" as="geometry"/>
    </mxCell>
    <mxCell id="n4" value="" vertex="1" parent="1">
      <mxGeometry x="400" y="40" width="80" height="40" as="geometry"/>
    </mxCell>
    <object id="u1" label="Wrapped Object Label">
      <mxCell style="shape=ellipse;" vertex="1" parent="1">
        <mxGeometry x="400" y="160" width="90" height="50" as="geometry"/>
      </mxCell>
    </object>
    <mxCell id="e1" value="connects" edge="1" parent="1" source="n3" target="n4">
      <mxGeometry relative="1" as="geometry"/>
    </mxCell>
    <mxCell id="e2" edge="1" parent="1" source="u1" target="n4"/>
  </root>
</mxGraphModel>'''

PAGE_2_MODEL = '''<mxGraphModel dx="800" dy="400" grid="1" page="1">
  <root>
    <mxCell id="0"/>
    <mxCell id="1" parent="0"/>
    <mxCell id="n1" value="Compressed Node Alpha" vertex="1" parent="1">
      <mxGeometry x="40" y="40" width="120" height="60" as="geometry"/>
    </mxCell>
    <mxCell id="e9" value="flows to Beta" edge="1" parent="1" source="n1" target="n2"/>
    <mxCell id="n2" value="Beta Service" vertex="1" parent="1">
      <mxGeometry x="300" y="40" width="120" height="60" as="geometry"/>
    </mxCell>
  </root>
</mxGraphModel>'''

PLAIN_DOCUMENT = f'''<?xml version="1.0" encoding="UTF-8"?>
<mxfile host="app.diagrams.net" type="device">
  <diagram id="page1" name="Architecture Overview">
    {PAGE_1_MODEL}
  </diagram>
  <diagram id="page2" name="Compressed Page" compressed="true">{compressed_diagram_payload(PAGE_2_MODEL)}</diagram>
</mxfile>'''

METADATA_XML = '<mxfile><Modified>2026-09-25</Modified><Agent>pytest</Agent></mxfile>'


def write_plain(tmp_path: Path, name: str = 'system_arch.drawio') -> Path:
    target = tmp_path / name
    target.write_text(PLAIN_DOCUMENT, encoding='utf-8')
    return target


def write_zip(tmp_path: Path, name: str = 'compressed.drawio') -> Path:
    target = tmp_path / name
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('file.xml', PLAIN_DOCUMENT)
        archive.writestr('metadata.xml', METADATA_XML)
    return target


@pytest.fixture(scope='module')
def reader_service():
    return FileReaderService()


@pytest.fixture(scope='module')
def router():
    return FileRouterService()


def process(router, path: Path):
    file_info = {
        'path': str(path),
        'name': path.name,
        'extension': path.suffix.lower(),
        'type': 'FILE',
        'size': path.stat().st_size,
    }
    result = router.process_file(file_info, collect=False, store_result=False)
    return result['Content']


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

class TestDrawioRouting:
    def test_extension_resolves_to_the_diagram_reader(self, reader_service):
        reader = reader_service.get_reader_for_extension('.drawio')
        assert isinstance(reader, DiagramFileReader)
        # legacy draw.io extension too
        assert isinstance(reader_service.get_reader_for_extension('.dio'),
                          DiagramFileReader)

    def test_drawio_is_claimed_by_exactly_one_reader(self, reader_service):
        claims = {}
        for reader in reader_service._readers:
            for ext in reader.get_supported_extensions():
                claims.setdefault(ext, []).append(type(reader).__name__)
        assert claims['.drawio'] == ['DiagramFileReader'], (
            'a contested .drawio would require an explicit '
            'EXTENSION_PREFERENCES entry (ROUTE-01)'
        )
        assert '.drawio' not in FileReaderService.EXTENSION_PREFERENCES

    def test_zip_form_is_content_verified_as_drawio_not_zip(self, tmp_path):
        path = write_zip(tmp_path)
        detected, confidence = sniff_file_type(str(path))
        assert (detected, confidence) == ('.drawio', 'strong')

    def test_plain_zip_is_still_a_zip(self, tmp_path):
        target = tmp_path / 'bundle.zip'
        with zipfile.ZipFile(target, 'w') as archive:
            archive.writestr('file.xml', '<a/>')
            archive.writestr('readme.txt', 'hello')
        detected, _ = sniff_file_type(str(target))
        assert detected == '.zip'

    def test_plain_form_sniffs_as_weak_xml_so_declared_extension_wins(
        self, tmp_path
    ):
        path = write_plain(tmp_path)
        detected, confidence = sniff_file_type(str(path))
        # weak evidence must not override a usable declared extension
        assert detected == '.xml'
        assert confidence == 'weak'

    def test_zip_drawio_does_not_go_to_the_archive_reader(self, reader_service, tmp_path):
        path = write_zip(tmp_path)
        reader, decision = reader_service.resolve_reader_for_file(str(path), '.drawio')
        assert isinstance(reader, DiagramFileReader), decision
        assert decision['effective_extension'] == '.drawio'

    def test_plain_drawio_resolves_to_the_diagram_reader(self, reader_service, tmp_path):
        path = write_plain(tmp_path)
        reader, decision = reader_service.resolve_reader_for_file(str(path), '.drawio')
        assert isinstance(reader, DiagramFileReader), decision
        assert decision['effective_extension'] == '.drawio'


# ---------------------------------------------------------------------------
# Parsing - plain XML form
# ---------------------------------------------------------------------------

class TestPlainXmlParsing:
    def test_pages_nodes_and_edges_are_structured(self, router, tmp_path):
        content = process(router, write_plain(tmp_path))
        assert not content.get('error'), content.get('error')
        assert content['format'] == 'drawio'
        assert content['container'] == 'xml'
        assert content['page_count'] == 2

        page1 = content['pages'][0]
        assert page1['name'] == 'Architecture Overview'
        labels = [node['label'] for node in page1['nodes']]
        assert 'Architecture Layer' in labels
        assert 'API & Gateway\nsecond line' in labels
        assert 'Wrapped Object Label' in labels
        # the unlabelled cell stays in the structure but contributes no text
        assert any(node['label'] == '' for node in page1['nodes'])

        edge_texts = [edge['text'] for edge in page1['edges']]
        assert 'connects' in edge_texts
        # unlabelled edge resolves to its endpoint labels
        resolved = [e for e in page1['edges'] if e['id'] == 'e2'][0]
        assert resolved['source_label'] == 'Wrapped Object Label'
        assert resolved['text'] == 'Wrapped Object Label'

    def test_hierarchy_depth_and_container_flags(self, router, tmp_path):
        content = process(router, write_plain(tmp_path))
        nodes = {node['id']: node for node in content['pages'][0]['nodes']}
        assert nodes['c1']['depth'] == 0
        assert nodes['c1']['is_container'] is True
        assert nodes['n3']['depth'] == 1
        assert nodes['n3']['parent'] == 'c1'
        assert nodes['n4']['is_container'] is False

    def test_object_wrapper_supplies_label_and_shape(self, router, tmp_path):
        content = process(router, write_plain(tmp_path))
        wrapped = [
            node for node in content['pages'][0]['nodes']
            if node['label'] == 'Wrapped Object Label'
        ][0]
        assert wrapped['id'] == 'u1'
        assert wrapped['shape'] == 'ellipse'

    def test_html_markup_is_not_in_the_readable_content(self, router, tmp_path):
        content = process(router, write_plain(tmp_path))
        text = content['content']
        assert '<b>' not in text and '<br>' not in text and 'mxCell' not in text
        assert 'mxGraphModel' not in text
        assert 'API & Gateway' in text
        assert 'second line' in text
        assert 'Page: Architecture Overview' in text
        assert 'Page: Compressed Page' in text

    def test_compressed_page_decodes(self, router, tmp_path):
        content = process(router, write_plain(tmp_path))
        page2 = content['pages'][1]
        assert page2['name'] == 'Compressed Page'
        labels = [node['label'] for node in page2['nodes']]
        assert 'Compressed Node Alpha' in labels
        assert 'Beta Service' in labels
        assert [e['text'] for e in page2['edges']] == ['flows to Beta']
        assert content['counts']['pages_failed'] == 0

    def test_counts_agree_with_the_lists(self, router, tmp_path):
        content = process(router, write_plain(tmp_path))
        counts = content['counts']
        assert counts['pages'] == 2
        assert counts['nodes'] == 6
        assert counts['edges'] == 3
        assert counts['nodes_stored'] == counts['nodes']
        assert counts['edges_stored'] == counts['edges']


# ---------------------------------------------------------------------------
# Parsing - ZIP form
# ---------------------------------------------------------------------------

class TestZipParsing:
    def test_zip_yields_the_same_structure_as_plain(self, router, tmp_path):
        plain = process(router, write_plain(tmp_path))
        zipped = process(router, write_zip(tmp_path))
        assert zipped['container'] == 'zip'
        assert zipped['content'] == plain['content']
        assert zipped['page_count'] == plain['page_count']
        # no archive extraction side effects
        assert 'extraction_path' not in zipped
        assert 'archive_info' not in zipped

    def test_metadata_is_captured_as_provenance(self, router, tmp_path):
        zipped = process(router, write_zip(tmp_path))
        assert zipped['drawio_metadata'] == {
            'Modified': '2026-09-25',
            'Agent': 'pytest',
        }
        # metadata must not pollute the searchable text
        assert 'pytest' not in zipped['content']

    def test_metadata_absent_from_plain_documents(self, router, tmp_path):
        plain = process(router, write_plain(tmp_path))
        assert 'drawio_metadata' not in plain


# ---------------------------------------------------------------------------
# Readable content / storage contract
# ---------------------------------------------------------------------------

class TestContentAndStorage:
    def test_storage_text_extractor_returns_the_labels(self, router, tmp_path):
        from pipeline.storage_pipeline import StoragePipeline

        content = process(router, write_plain(tmp_path))

        class _Stub(StoragePipeline):
            def __init__(self):
                pass

        text = _Stub()._extract_text_from_content(content)
        assert 'API & Gateway' in text
        assert 'Compressed Node Alpha' in text
        assert 'Architecture Overview' in text
        assert 'mxCell' not in text

    def test_content_ends_up_on_the_direct_content_path(self, router, tmp_path):
        """A non-empty string content becomes searchable direct_content with
        no storage changes."""
        content = process(router, write_plain(tmp_path))
        assert isinstance(content['content'], str)
        assert content['content'].strip()

    def test_bare_mxgraphmodel_is_a_single_page(self, router, tmp_path):
        target = tmp_path / 'bare_model.drawio'
        target.write_text(
            '<mxGraphModel><root>'
            '<mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="x" value="Bare Model Node" vertex="1" parent="1">'
            '<mxGeometry as="geometry"/></mxCell>'
            '</root></mxGraphModel>',
            encoding='utf-8',
        )
        content = process(router, target)
        assert content['page_count'] == 1
        assert content['pages'][0]['name'] == 'bare_model'
        assert 'Bare Model Node' in content['content']

    def test_unreadable_page_fails_soft_and_keeps_the_rest(
        self, router, tmp_path
    ):
        document = (
            '<mxfile>'
            '<diagram id="1" name="Good">'
            '<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="n" value="Survivor Label" vertex="1" parent="1">'
            '<mxGeometry as="geometry"/></mxCell></root></mxGraphModel>'
            '</diagram>'
            '<diagram id="2" name="Broken" compressed="true">bm90IGRlZmxhdGU=</diagram>'
            '</mxfile>'
        )
        target = tmp_path / 'half_broken.drawio'
        target.write_text(document, encoding='utf-8')
        content = process(router, target)
        assert content['page_count'] == 2
        assert content['counts']['pages_failed'] == 1
        assert 'error' in content['pages'][1]
        assert 'Survivor Label' in content['content']
        assert 'Broken' in content['content']

    def test_garbage_file_returns_an_error_result(self, router, tmp_path):
        target = tmp_path / 'garbage.drawio'
        target.write_text('this is not xml at all <<<', encoding='utf-8')
        content = process(router, target)
        assert content.get('error')
        assert not content.get('pages')

    def test_storage_cannot_be_confused_by_binary_payload_in_xml_wrapper(
        self, router, tmp_path
    ):
        target = tmp_path / 'binary.drawio'
        target.write_bytes(b'\x00\x01\x02\x03' + b'garbage' * 40)
        content = process(router, target)
        assert content.get('error')


# ---------------------------------------------------------------------------
# Caps
# ---------------------------------------------------------------------------

class TestStructuredCap:
    def test_counts_and_text_survive_the_structured_cap(
        self, router, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(
            'reader_file.readers.read_diagram.MAX_DIAGRAM_ELEMENTS', 5
        )
        cells = ''.join(
            f'<mxCell id="n{i}" value="Node{i}" vertex="1" parent="1">'
            f'<mxGeometry as="geometry"/></mxCell>'
            for i in range(10)
        )
        target = tmp_path / 'wide.drawio'
        target.write_text(
            f'<mxfile><diagram id="1" name="Wide"><mxGraphModel><root>'
            f'<mxCell id="0"/><mxCell id="1" parent="0"/>{cells}'
            f'<mxCell id="e1" edge="1" parent="1" source="n0" target="n9"/>'
            f'</root></mxGraphModel></diagram></mxfile>',
            encoding='utf-8',
        )
        content = process(router, target)
        page = content['pages'][0]

        assert page['node_count'] == 10, 'counts reflect the FULL page'
        assert page['edge_count'] == 1
        assert len(page['nodes']) == 5, 'structured lists honour the cap'
        assert page['truncated'] is True
        assert 'cap' in page['truncation_note']

        # labels beyond the cap remain searchable in the readable text
        assert 'Node0' in content['content']
        assert 'Node9' in content['content']
        assert 'Edges:' in content['content'], 'the edge still resolves'


# ---------------------------------------------------------------------------
# Label cleaning
# ---------------------------------------------------------------------------

class TestLabelCleaning:
    def test_tags_and_breaks_become_text(self):
        assert clean_label('<b>Bold</b><br>next') == 'Bold\nnext'
        assert clean_label('plain text') == 'plain text'
        assert clean_label(None) == ''
        assert clean_label('') == ''

    def test_entities_are_decoded(self):
        assert clean_label('A &amp; B') == 'A & B'
        assert clean_label('5&nbsp;MB') == '5 MB'

    def test_malformed_markup_falls_back_to_tag_stripping(self):
        result = clean_label('<b>unclosed & broken')
        assert '<b>' not in result
        assert 'unclosed' in result

    def test_image_alt_text_is_kept(self):
        assert clean_label('<img alt="flow chart" src="x.png"/>') == 'flow chart'

    def test_whitespace_collapses_per_line(self):
        assert clean_label('  spaced   out  \n\n\n  next  ') == 'spaced out\nnext'
