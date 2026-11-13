"""Archive members that are noise must be skipped before processing."""

from core.file_utils import nested_extract_skip_reason


def test_macosx_appledouble_skipped():
    assert nested_extract_skip_reason("__MACOSX/._foo.jpg") == "macosx_metadata"
    assert nested_extract_skip_reason("subdir/._ASF_Tika-colour.png") == "appledouble_resource_fork"


def test_iwork_shards_skipped():
    assert nested_extract_skip_reason("Index/DocumentStylesheet.iwa") == "iwork_internal_shard"


def test_normal_members_not_skipped():
    assert nested_extract_skip_reason("Preview.pdf") is None
    assert nested_extract_skip_reason("index.xml") is None
