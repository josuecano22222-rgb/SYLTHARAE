"""Unit: the browser half of large-file staging (node, stubbed network).

Chunked staging is the one path where the client does real work: it slices the
file, hashes each slice, sends chunks in order, retries a failed chunk, and
tells the server to abandon the session when the transfer is stopped. All of
that used to be broken in a way no server test could see — the old client
posted its completion to an endpoint that did not exist — so the contract is
tested from this side too, against a stubbed `fetch`, on the file object a
browser actually hands over (including the folder-relative name, which is what
makes a large file inside a folder keep its place).

Skipped when node is not installed.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
MODULE = PROJECT_ROOT / "static" / "js" / "modules" / "upload" / "large-file-upload.js"

DRIVER = """
const calls = [];
let chunkFailures = 0;

function jsonResponse(body, status = 200) {{
  return {{
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  }};
}}

globalThis.fetch = async (url, options = {{}}) => {{
  const record = {{ url, method: options.method || 'GET', body: options.body }};
  calls.push(record);
  if (url.endsWith('/start')) {{
    return jsonResponse({{
      success: true, upload_id: 'a'.repeat(32), chunk_size: 8, total_chunks: 3,
    }}, 201);
  }}
  if (url.includes('/chunk/')) {{
    record.chunk = options.body.get('chunk');
    record.hash = options.body.get('sha256');
    if (chunkFailures > 0) {{
      chunkFailures -= 1;
      return jsonResponse({{ success: false, error: {{ message: 'boom' }} }}, 500);
    }}
    return jsonResponse({{ success: true }}, 200);
  }}
  if (url.endsWith('/complete')) {{
    return jsonResponse({{
      success: true, staged_path: '/staging/out/big.bin', name: 'Case 1/big.bin',
      bytes: 20, sha256: 'f'.repeat(64),
    }}, 201);
  }}
  return jsonResponse({{ success: true }});
}};

const makeFile = (name, bytes) => {{
  const file = new Blob([Uint8Array.from(bytes)], {{ type: 'application/octet-stream' }});
  file.name = name;
  return file;
}};

const out = {{}};
const mod = await import('{module_url}');

out.canHash = typeof mod.canHash() === 'boolean';

// --- a straightforward transfer, with a folder-relative name ----------------
calls.length = 0;
const result = await mod.stageLargeFile(makeFile('big.bin', [...Array(20).keys()]), {{
  endpoint: '/api/input/uploads/chunked',
  filename: 'Case 1/big.bin',
}});
out.result = result;
out.sequence = calls.map((call) => {{
  if (call.url.endsWith('/start')) return `start:${{JSON.parse(call.body).filename}}:${{JSON.parse(call.body).size}}`;
  if (call.url.includes('/chunk/')) return `chunk${{call.url.split('/').pop()}}:${{call.chunk.size}}`;
  if (call.url.endsWith('/complete')) return 'complete';
  return `${{call.method}}:${{call.url}}`;
}});
out.hashes = calls.filter((call) => call.hash).map((call) => call.hash.length);

// --- a dropped connection is retried, not restarted -------------------------
calls.length = 0;
chunkFailures = 1;
const retried = await mod.stageLargeFile(makeFile('retry.bin', [...Array(20).keys()]), {{
  endpoint: '/api/input/uploads/chunked', retries: 2,
}});
out.retriedOk = retried.bytes === 20;
out.retryCalls = calls.filter((call) => call.url.includes('/chunk/')).length;

// --- stopping a transfer abandons the session server-side -------------------
calls.length = 0;
const controller = new AbortController();
chunkFailures = 0;
globalThis.fetch = async (url, options = {{}}) => {{
  calls.push({{ url, method: options.method || 'GET' }});
  if (url.endsWith('/start')) {{
    return jsonResponse({{ success: true, upload_id: 'b'.repeat(32), chunk_size: 8, total_chunks: 3 }}, 201);
  }}
  if (url.includes('/chunk/')) {{
    const error = new Error('aborted');
    error.name = 'AbortError';
    throw error;
  }}
  return jsonResponse({{ success: true }});
}};
let aborted = false;
try {{
  await mod.stageLargeFile(makeFile('stop.bin', [...Array(20).keys()]), {{
    endpoint: '/api/input/uploads/chunked', signal: controller.signal, retries: 2,
  }});
}} catch (error) {{
  aborted = error.name === 'AbortError';
}}
out.aborted = aborted;
out.cleanup = calls.filter((call) => call.method === 'DELETE').length;

console.log(JSON.stringify(out));
"""


PLAIN_HTTP_DRIVER = """
// The browser this runs against is the one an operator reaches over plain HTTP
// from another machine (a Windows desktop on the LAN, say): `crypto.subtle` is
// only defined in a secure context there, so there is no hashing API at all.
// The transfer must still work - the server verifies size and the hash of what
// it stored - and it must not claim to have hashed anything.
Object.defineProperty(globalThis, 'crypto', {{
  value: {{ getRandomValues: (array) => array }}, configurable: true, writable: true,
}});

const calls = [];

function jsonResponse(body, status = 200) {{
  return {{ ok: status >= 200 && status < 300, status, json: async () => body }};
}}

globalThis.fetch = async (url, options = {{}}) => {{
  calls.push({{ url, body: options.body }});
  if (url.endsWith('/start')) {{
    return jsonResponse({{ success: true, upload_id: 'c'.repeat(32), chunk_size: 8, total_chunks: 3 }}, 201);
  }}
  if (url.includes('/chunk/')) return jsonResponse({{ success: true }}, 200);
  if (url.endsWith('/complete')) {{
    return jsonResponse({{
      success: true, staged_path: '/staging/out/lan.bin', name: 'lan.bin',
      bytes: 20, sha256: 'e'.repeat(64),
    }}, 201);
  }}
  return jsonResponse({{ success: true }});
}};

const out = {{}};
const mod = await import('{module_url}');
out.canHash = mod.canHash();

const file = new Blob([Uint8Array.from([...Array(20).keys()])], {{ type: 'application/octet-stream' }});
file.name = 'lan.bin';
const result = await mod.stageLargeFile(file, {{ endpoint: '/api/input/uploads/chunked' }});
out.clientHashed = result.clientHashed;
out.bytes = result.bytes;
out.sha256 = result.sha256;
out.sequence = calls.map((call) => {{
  if (call.url.endsWith('/start')) return 'start';
  if (call.url.includes('/chunk/')) return `chunk:${{call.body.get('sha256')}}`;
  return 'complete';
}});
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def plain_http_client(tmp_path_factory):
    """The same client in a context with no SubtleCrypto (plain HTTP on a LAN)."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")

    driver = tmp_path_factory.mktemp("large-file-client-plain") / "driver.mjs"
    driver.write_text(PLAIN_HTTP_DRIVER.format(module_url=MODULE.as_uri()), encoding="utf-8")
    proc = subprocess.run(
        [node, str(driver)], capture_output=True, text=True, timeout=120, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    assert MODULE.exists(), MODULE

    driver = tmp_path_factory.mktemp("large-file-client") / "driver.mjs"
    driver.write_text(DRIVER.format(module_url=MODULE.as_uri()), encoding="utf-8")
    proc = subprocess.run(
        [node, str(driver)], capture_output=True, text=True, timeout=120, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_the_client_reports_whether_it_can_hash(client):
    assert client["canHash"] is True   # node exposes SubtleCrypto, like a secure context


def test_a_transfer_goes_start_chunks_complete_in_order(client):
    assert client["sequence"] == [
        "start:Case 1/big.bin:20",   # the folder-relative name is what is sent
        "chunk0:8", "chunk1:8", "chunk2:4",
        "complete",
    ]
    assert client["hashes"] == [64, 64, 64]   # per-chunk SHA-256


def test_the_staged_file_is_reported_back_with_the_server_hash(client):
    result = client["result"]
    assert result["stagedPath"] == "/staging/out/big.bin"
    assert result["name"] == "Case 1/big.bin"
    assert result["bytes"] == 20
    assert result["sha256"] == "f" * 64
    assert result["clientHashed"] is True


def test_a_failed_chunk_is_retried_rather_than_restarting_the_file(client):
    assert client["retryCalls"] == 4   # 3 chunks + 1 retry of the failed one
    assert client["retriedOk"] is True


def test_stopping_a_transfer_abandons_the_session_on_the_server(client):
    assert client["aborted"] is True
    assert client["cleanup"] == 1      # DELETE <endpoint>/<upload_id>


def test_a_context_without_a_hashing_api_still_stages_the_file(plain_http_client):
    assert plain_http_client["canHash"] is False
    # No sha256 field on any chunk: the hash is omitted rather than faked.
    assert plain_http_client["sequence"] == [
        "start", "chunk:null", "chunk:null", "chunk:null", "complete",
    ]
    assert plain_http_client["bytes"] == 20
    # The server's hash of what it stored is what the caller gets told.
    assert plain_http_client["sha256"] == "e" * 64
    assert plain_http_client["clientHashed"] is False
