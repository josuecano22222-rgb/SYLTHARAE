"""Regression: file dates must not shift a day in western timezones.

A date-only value such as "2026-10-13" passed straight to ``new Date()`` is
parsed as UTC midnight, so the detail page showed the previous day in
America/Los_Angeles while the list page showed the stored date.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PANELS = REPO_ROOT / "static/js/modules/file-operations/file-details-panels.js"


def test_panels_do_not_parse_date_only_values_as_utc():
    source = PANELS.read_text(encoding="utf-8")
    assert "new Date(file.file_date)" not in source
    assert "new Date(file.date_creation)" not in source
    assert "export function formatFileDate" in source


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
@pytest.mark.parametrize("tz", ["America/Los_Angeles", "Europe/Amsterdam", "Pacific/Honolulu"])
def test_format_file_date_keeps_calendar_day(tz):
    script = f"""
const src = require('fs').readFileSync({json.dumps(str(PANELS))}, 'utf8');
const body = src.match(/export function formatFileDate[\\s\\S]*?\\n}}\\n/)[0].replace('export ', '');
const formatFileDate = new Function(body + '; return formatFileDate;')();
const expected = new Date(2026, 9, 13).toLocaleDateString();
process.stdout.write(JSON.stringify({{
  dateOnly: formatFileDate('2026-10-13'),
  timestamp: formatFileDate('2026-10-13T15:00:00Z'),
  expected,
  empty: formatFileDate(null, 'N/A'),
}}));
"""
    env = {"TZ": tz, "PATH": "/usr/local/bin:/usr/bin:/bin"}
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, env=env, check=True)
    result = json.loads(out.stdout)
    assert result["dateOnly"] == result["expected"]
    assert result["timestamp"] == result["expected"]
    assert result["empty"] == "N/A"
