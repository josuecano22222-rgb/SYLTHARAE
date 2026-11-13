"""Behaviour of the ingestion page's queue helpers (node, no browser).

The page's decisions - what is a duplicate *selection*, which files need the
chunked transfer, what the readiness line says, what payload the API receives -
are pure functions in ``static/js/modules/upload/upload-queue.js`` precisely so
they can be tested without a DOM. This test imports the real module in node and
checks the answers, so a refactor of the page cannot quietly change what the
operator is told or what the API is sent.

Skipped when node is not installed: the module is the artifact under test, and
re-implementing its functions in Python would test a copy instead.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
MODULE = PROJECT_ROOT / "static" / "js" / "modules" / "upload" / "upload-queue.js"

DRIVER = """
import * as q from '{module_url}';

const file = (name, size, relative, lastModified) => ({{
  name, size, lastModified: lastModified === undefined ? 1 : lastModified,
  webkitRelativePath: relative || '',
}});

const out = {{}};
out.formatBytes = [
  q.formatBytes(0), q.formatBytes(900), q.formatBytes(2048),
  q.formatBytes(5 * 1024 * 1024), q.formatBytes(3 * 1024 * 1024 * 1024),
];
out.extension = [
  q.extensionOf('Report.PDF'), q.extensionOf('a/b/c.TXT'),
  q.extensionOf('archive.tar.gz'), q.extensionOf('noext'),
  q.extensionOf('.hidden'), q.extensionOf('trailing.'),
];
out.icons = [
  q.iconForName('Report.PDF'), q.iconForName('archive.tar.gz'),
  q.iconForName('mystery'), q.iconForName('sheet.xlsx'), q.iconForName('clip.mp4'),
];

const once = q.mergeSelection([], [file('a.txt', 10)]);
const twice = q.mergeSelection(once.entries, [file('a.txt', 10)]);
const folders = q.mergeSelection([], [
  file('a.txt', 10, 'one/a.txt'), file('a.txt', 10, 'two/a.txt'),
]);
// A folder *dropped* onto the page arrives as {{file, path}} items, because the
// browser hands over an entry tree rather than Files with a relative path.
const dropped = q.mergeSelection([], [
  {{ file: file('scan.jpg', 10), path: 'Case 1/scan.jpg' }},
  {{ file: file('scan.jpg', 10), path: 'Case 2/scan.jpg' }},
]);
const droppedAgain = q.mergeSelection(dropped.entries, [
  {{ file: file('scan.jpg', 10), path: 'Case 1/scan.jpg' }},
]);
out.merge = {{
  firstAdded: once.added,
  secondAdded: twice.added,
  skipped: twice.skipped,
  entries: once.entries.length + (twice.entries.length - once.entries.length),
  folderCount: folders.entries.length,
  folderNames: folders.entries.map((entry) => entry.name),
}};

out.dropped = {{
  names: dropped.entries.map((entry) => entry.name),
  added: dropped.added,
  duplicateSkipped: droppedAgain.skipped,
  addedAgain: droppedAgain.added,
}};

out.totals = q.queueTotals([
  {{ size: 100, status: 'ready' }},
  {{ size: 200, status: 'staged' }},
  {{ size: 0, status: 'failed' }},
]);

out.limits = {{
  needsBig: q.needsChunkedUpload(600 * 1024 * 1024, 512 * 1024 * 1024),
  needsSmall: q.needsChunkedUpload(10 * 1024 * 1024, 512 * 1024 * 1024),
  unknownLimit: q.needsChunkedUpload(10, 0),
  chunks: q.planChunks(8 * 1024 * 1024 + 1, 8 * 1024 * 1024),
  emptyChunks: q.planChunks(0, 8 * 1024 * 1024),
}};

const plan = q.planStaging([
  {{ key: 'a', size: 40, status: 'ready' }},
  {{ key: 'b', size: 40, status: 'ready' }},
  {{ key: 'c', size: 40, status: 'ready' }},
  {{ key: 'big', size: 500, status: 'ready' }},
  {{ key: 'done', size: 10, status: 'staged', stagedPath: '/staged/done' }},
  {{ key: 'bad', size: 10, status: 'failed' }},
], 100, 2);
out.plan = {{
  batches: plan.batches.map((batch) => batch.map((entry) => entry.key)),
  chunked: plan.chunked.map((entry) => entry.key),
}};

const empty = q.readiness({{ mode: 'upload', entries: [], source: '', side: '' }});
out.readinessEmpty = {{ ready: empty.ready, reason: empty.reason, steps: empty.steps }};
const serverNoPath = q.readiness({{
  mode: 'server', serverPath: '  ', entries: [], source: 'S', side: 'D',
}});
out.readinessServerNoPath = {{ ready: serverNoPath.ready, reason: serverNoPath.reason }};
const complete = q.readiness({{
  mode: 'server', serverPath: '/data/inbox', entries: [],
  source: 'SRC', side: 'SIDE',
}});
out.readinessComplete = {{ ready: complete.ready, steps: complete.steps }};
const noSide = q.readiness({{
  mode: 'upload', entries: [{{ size: 1, status: 'ready' }}], source: 'SRC', side: '',
}});
out.readinessNoSide = {{ ready: noSide.ready, reason: noSide.reason }};

out.payloadServer = q.buildJobPayload({{
  mode: 'server', serverPath: ' /data/inbox ', source: 'S', side: 'D',
  recursive: false, workers: 4, checkpoint: 'fresh', monitoring: false,
}}, {{ dryRun: true }});
out.payloadUpload = q.buildJobPayload({{
  mode: 'upload', serverPath: '', source: 'S', side: 'D',
  recursive: true, workers: 0, checkpoint: 'auto', monitoring: true,
  entries: [
    {{ size: 1, status: 'staged', stagedPath: '/tmp/staged/a.txt' }},
    {{ size: 1, status: 'ready' }},
  ],
}}, {{ dryRun: false }});

out.tiles = q.preflightTiles({{
  files_discovered: 5, files_eligible: 4, files_unsupported: 1,
  estimated_bytes: 1536,
}});
out.tilesUnknown = q.preflightTiles(null);
out.samples = q.sampledFiles({{
  sample_files: [{{ path: 'a' }}, {{ path: 'b' }}, {{ path: 'c' }}, {{ name: 'd' }}],
}}, 2);

console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def helpers(tmp_path_factory):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    assert MODULE.exists(), MODULE

    driver = tmp_path_factory.mktemp("upload-queue") / "driver.mjs"
    driver.write_text(DRIVER.format(module_url=MODULE.as_uri()), encoding="utf-8")
    proc = subprocess.run(
        [node, str(driver)], capture_output=True, text=True, timeout=120, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_sizes_are_human_readable(helpers):
    assert helpers["formatBytes"] == ["0 B", "900 B", "2.0 KB", "5.0 MB", "3.00 GB"]


def test_extension_and_icon_come_from_the_name(helpers):
    assert helpers["extension"] == ["pdf", "txt", "gz", "", "", ""]
    assert helpers["icons"] == [
        "bi-file-earmark-pdf",
        "bi-file-earmark-zip",
        "bi-file-earmark",
        "bi-file-earmark-spreadsheet",
        "bi-file-earmark-play",
    ]


def test_the_same_selection_is_not_queued_twice(helpers):
    assert helpers["merge"]["firstAdded"] == 1
    assert helpers["merge"]["secondAdded"] == 0
    assert helpers["merge"]["skipped"] == ["a.txt"]
    assert helpers["merge"]["entries"] == 1


def test_same_name_in_two_folders_stays_two_entries(helpers):
    assert helpers["merge"]["folderCount"] == 2
    assert helpers["merge"]["folderNames"] == ["one/a.txt", "two/a.txt"]


def test_a_dropped_folder_arrives_with_its_paths(helpers):
    dropped = helpers["dropped"]
    assert dropped["names"] == ["Case 1/scan.jpg", "Case 2/scan.jpg"]
    assert dropped["added"] == 2
    assert dropped["addedAgain"] == 0
    assert dropped["duplicateSkipped"] == ["Case 1/scan.jpg"]


def test_queue_totals_count_states_and_volume(helpers):
    totals = helpers["totals"]
    assert totals["count"] == 3
    assert totals["bytes"] == 300
    assert totals["largest"] == 200
    assert totals["staged"] == 1
    assert totals["failed"] == 1


def test_only_files_over_the_direct_limit_take_the_chunked_path(helpers):
    limits = helpers["limits"]
    assert limits["needsBig"] is True
    assert limits["needsSmall"] is False
    # No limit reported by the server yet: the safe answer is the chunked path.
    assert limits["unknownLimit"] is True
    assert limits["chunks"] == 2
    assert limits["emptyChunks"] == 1


def test_staging_splits_into_bounded_batches_and_large_transfers(helpers):
    plan = helpers["plan"]
    assert plan["batches"] == [["a", "b"], ["c"]]
    assert plan["chunked"] == ["big"]  # staged and failed entries are not re-sent


def test_readiness_names_one_blocking_reason(helpers):
    empty = helpers["readinessEmpty"]
    assert empty["ready"] is False
    assert "step 1" in empty["reason"]
    assert empty["steps"] == {
        "source": "current", "classify": "pending",
        "processing": "done", "launch": "pending",
    }

    server = helpers["readinessServerNoPath"]
    assert server["ready"] is False
    assert "server path" in server["reason"]

    complete = helpers["readinessComplete"]
    assert complete["ready"] is True
    assert complete["steps"]["source"] == "done"
    assert complete["steps"]["classify"] == "done"
    assert complete["steps"]["launch"] == "current"

    no_side = helpers["readinessNoSide"]
    assert no_side["ready"] is False
    assert "side" in no_side["reason"]


def test_job_payload_matches_what_the_api_validates(helpers):
    server = helpers["payloadServer"]
    assert server["path"] == "/data/inbox"          # trimmed
    assert "file_paths" not in server
    assert server["dry_run"] is True
    assert server["recursive"] is False
    assert server["processing"] == {
        "max_workers": 4, "checkpoint": "fresh", "enable_monitoring": False,
    }

    upload = helpers["payloadUpload"]
    # Only what the server actually staged is sent; a row that never reached
    # the staging directory must not appear as a path the engine cannot open.
    assert upload["file_paths"] == ["/tmp/staged/a.txt"]
    assert "path" not in upload
    assert upload["processing"]["max_workers"] == 0
    assert upload["processing"]["checkpoint"] == "auto"


def test_preflight_tiles_flag_unsupported_files(helpers):
    tiles = helpers["tiles"]
    assert [tile["label"] for tile in tiles] == ["discovered", "to read", "not readable", "volume"]
    assert tiles[0]["value"] == 5
    assert tiles[2]["warn"] is True
    assert tiles[3]["value"] == "1.5 KB"
    # A missing preview must render zeros, not "undefined".
    assert all(tile["value"] == 0 or tile["value"] == "0 B" for tile in helpers["tilesUnknown"])


def test_sampled_files_are_limited_and_normalised(helpers):
    assert helpers["samples"] == ["a", "b"]
