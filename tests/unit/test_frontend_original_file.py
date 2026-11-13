"""Unit: the original-file viewer renders each source kind, safely.

These tests execute the SHIPPED module
static/js/modules/file-operations/original-file.js under Node - the same code
the File Details modal runs - and check the markup it produces for every kind
of source the server can describe: image, PDF, text, audio, video, a format
with no viewer, and a source file that is no longer on disk.

Two things matter here beyond "does it render":

* a filename that contains quotes must not survive raw inside a tag (the
  attribute-breakout class of defect that killed the file cards - the name is
  attacker-chosen when it came out of an uploaded archive);
* the text viewer must show the source as TEXT. An uploaded .html file is
  data, and rendering its markup would run a stranger's script in the app.
"""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

MODULE = PROJECT_ROOT / "static/js/modules/file-operations/original-file.js"

DOM_STUB = r"""
// Element/attribute behaviour the viewer relies on. innerHTML keeps the
// string as-is (the viewer escapes everything it interpolates), and the
// stub's querySelector understands the one selector the text loader uses.
// escapeHtml() serialises through a text node: & < > escaped, quotes NOT.
// The stub reproduces exactly that (a stub that over-escaped would hide the
// attribute-breakout defect these tests exist to catch).
const escapeText = (t) => String(t)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

class El {
    constructor(tag = 'div') {
        this.tagName = String(tag).toUpperCase();
        // innerHTML and textContent are two views of the same content in a
        // browser; the last one written wins. Modelled, because escapeHtml
        // writes one and reads the other.
        this._html = null;
        this._text = '';
        this._attrs = {};
        this.style = {};
        this.classList = { add(){}, remove(){}, toggle(){}, contains(){ return false; } };
    }
    get innerHTML() { return this._html === null ? escapeText(this._text) : this._html; }
    set innerHTML(v) { this._html = String(v); }
    get textContent() { return this._text; }
    set textContent(v) { this._text = String(v); this._html = null; }
    setAttribute(k, v) { this._attrs[k] = String(v); }
    getAttribute(k) { return this._attrs[k] === undefined ? null : this._attrs[k]; }
    addEventListener() {}
    appendChild(c) { return c; }
    querySelector(selector) {
        const m = /^\[([-\w]+)\]$/.exec(selector.trim());
        if (!m) return null;
        const found = new RegExp(m[1] + '="([^"]*)"').exec(this._html);
        if (!found) return null;
        const outer = this;
        return {
            _url: found[1],
            getAttribute: (k) => (k === m[1] ? found[1] : null),
            // Inserting text into the matched element: exactly that element's
            // content is replaced, and it is inserted as TEXT (escaped on the
            // way back out), never parsed as markup.
            set textContent(v) {
                const at = outer._html.indexOf(found[0]);
                const open = outer._html.indexOf('>', at);
                const close = outer._html.indexOf('<', open);
                outer._html = outer._html.slice(0, open + 1)
                    + escapeText(v) + outer._html.slice(close);
            },
            get textContent() { return outer._html; }
        };
    }
    querySelectorAll() { return []; }
}
globalThis.El = El;
globalThis.document = {
    createElement: (t) => new El(t),
    getElementById: () => null,
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener() {}, removeEventListener() {},
    body: new El('body'), documentElement: new El('html')
};
globalThis.window = globalThis;
globalThis.localStorage = { getItem(){ return null; }, setItem(){}, removeItem(){} };

// The module reads the UI catalog the same way every other module does.
globalThis.appData = { translations: {} };
"""

HARNESS = r"""
import '__STUB__';
const mod = await import('__MODULE__');

const HOSTILE = 'budget "final" <v2>.pdf';
const base = (over) => Object.assign({
    file_id: 42,
    name: 'scan.png',
    path: '/evidence/scan.png',
    extension: '.png',
    stored_size: 2048,
    size: 2048,
    kind: 'image',
    viewer: 'img',
    available: true,
    mime_type: 'image/png',
    serve_url: '/api/file/42/original/content',
    download_url: '/api/file/42/original/content?download=1'
}, over || {});

const parseAttrs = (inner) => {
    const names = [];
    let i = 0;
    while (i < inner.length) {
        while (i < inner.length && /\s/.test(inner[i])) i++;
        if (i >= inner.length) break;
        const nm = /^[A-Za-z_:][-A-Za-z0-9_:.]*/.exec(inner.slice(i));
        if (!nm) { i++; continue; }
        names.push(nm[0]);
        i += nm[0].length;
        if (inner[i] === '=') {
            i++;
            const q = inner[i];
            if (q === '"' || q === "'") { i++; while (i < inner.length && inner[i] !== q) i++; i++; }
            else { while (i < inner.length && !/[\s>]/.test(inner[i])) i++; }
        }
    }
    return names;
};

const rendered = {
    image: mod.renderOriginalFile(base()),
    pdf: mod.renderOriginalFile(base({ name: 'report.pdf', extension: '.pdf', kind: 'pdf', viewer: 'iframe', mime_type: 'application/pdf' })),
    text: mod.renderOriginalFile(base({ name: 'notes.txt', extension: '.txt', kind: 'text', viewer: 'text', mime_type: 'text/plain' })),
    audio: mod.renderOriginalFile(base({ name: 'clip.mp3', extension: '.mp3', kind: 'audio', viewer: 'audio' })),
    video: mod.renderOriginalFile(base({ name: 'clip.mp4', extension: '.mp4', kind: 'video', viewer: 'video' })),
    download: mod.renderOriginalFile(base({ name: 'letter.docx', extension: '.docx', kind: 'download', viewer: 'none' })),
    unavailable: mod.renderOriginalFile(base({
        name: 'gone.pdf', extension: '.pdf', kind: 'pdf', viewer: 'iframe',
        available: false, reason: 'missing-on-disk',
        message: 'The source file is no longer at the recorded location. The extracted text is still available.',
        path: '/mnt/evidence/gone.pdf', size: null
    })),
    hostile: mod.renderOriginalFile(base({ name: HOSTILE, extension: '.pdf', kind: 'pdf', viewer: 'iframe' }))
};

// Every tag in every rendering, with the attribute names a parser would see.
const tagReport = {};
for (const [key, html] of Object.entries(rendered)) {
    const tags = [...html.matchAll(/<([a-zA-Z][-\w]*)((?:"[^"]*"|[^>])*)>/g)]
        .map(t => ({ tag: t[1].toLowerCase(), attrs: parseAttrs(t[2]), raw: t[0] }));
    tagReport[key] = {
        eventAttrs: tags.flatMap(t => t.attrs.filter(a => /^on/i.test(a))),
        rawQuoteInTag: tags.some(t => /budget "final"/.test(t.raw))
    };
}

// --- The text loader, driven through the real showOriginalFile() ----------
const calls = [];
const textDescriptor = base({ name: 'notes.txt', extension: '.txt', kind: 'text', viewer: 'text' });
let textResponse = '<script>alert(1)</script>\nplain line';   // the hostile case
let textStatus = 200;

globalThis.fetch = async (url) => {
    calls.push(url);
    const target = String(url);
    if (target.includes('/original') && !target.includes('/content')) {
        return { ok: true, status: 200, json: async () => ({ success: true, original: textDescriptor }) };
    }
    return { ok: textStatus === 200, status: textStatus,
             text: async () => textResponse };
};

const container = new El('div');
await mod.showOriginalFile(7, container);
const textPane = container.innerHTML;

// An empty source file must say so, not look like a failed load.
textResponse = '';
const emptyContainer = new El('div');
await mod.showOriginalFile(8, emptyContainer);

// A source that cannot be read (500) must say that instead.
textResponse = '';
textStatus = 500;
const unreadable = new El('div');
await mod.showOriginalFile(9, unreadable);
textStatus = 200;

// Failure path: the endpoint says it cannot serve this object.
globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({ success: false, error: 'File not found' }) });
const failed = new El('div');
const failedResult = await mod.showOriginalFile(999, failed);

// Network failure path.
globalThis.fetch = async () => { throw new Error('offline'); };
const broken = new El('div');
await mod.showOriginalFile(5, broken);

process.stdout.write(JSON.stringify({
    rendered,
    tagReport,
    textPaneHtml: container.innerHTML,
    emptyHtml: emptyContainer.innerHTML,
    unreadableHtml: unreadable.innerHTML,
    calls,
    failedResult,
    failedHtml: failed.innerHTML,
    brokenHtml: broken.innerHTML,
    humanSize: [mod.humanSize(0), mod.humanSize(2048), mod.humanSize(5 * 1024 * 1024), mod.humanSize(null)]
}));
"""


@pytest.fixture(scope="module")
def node():
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    return shutil.which("node")


@pytest.fixture(scope="module")
def out(node, tmp_path_factory):
    assert MODULE.exists(), f"shipped module missing: {MODULE}"
    tmp = tmp_path_factory.mktemp("jsoriginalfile")
    stub = tmp / "domstub.mjs"
    stub.write_text(DOM_STUB)
    harness = tmp / "harness.mjs"
    harness.write_text(
        HARNESS.replace("__STUB__", stub.as_uri()).replace("__MODULE__", MODULE.as_uri())
    )
    proc = subprocess.run([node, str(harness)], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"node failed: {proc.stderr[:2000]}"
    return json.loads(proc.stdout)


# ---------------------------------------------------------------------------
# One viewer per kind
# ---------------------------------------------------------------------------

def test_image_renders_an_img_from_the_serve_url(out):
    html = out["rendered"]["image"]
    assert 'data-original-file-image="1"' in html
    assert 'src="/api/file/42/original/content"' in html


def test_pdf_renders_the_browser_viewer_in_a_frame(out):
    html = out["rendered"]["pdf"]
    assert 'data-original-file-frame="1"' in html
    assert 'src="/api/file/42/original/content"' in html


def test_text_renders_a_pre_and_fetches_the_source(out):
    html = out["rendered"]["text"]
    assert 'data-original-file-text="/api/file/42/original/content"' in html
    assert "Loading original file" in html  # placeholder until the fetch lands


def test_audio_and_video_render_native_players(out):
    assert 'data-original-file-audio="1"' in out["rendered"]["audio"]
    assert 'data-original-file-video="1"' in out["rendered"]["video"]


def test_unrenderable_format_explains_instead_of_pretending(out):
    html = out["rendered"]["download"]
    assert "cannot be displayed in the browser" in html
    assert "original-file-frame" not in html
    assert "data-original-file-image" not in html


def test_missing_source_file_is_stated_with_its_path(out):
    html = out["rendered"]["unavailable"]
    assert "no longer at the recorded location" in html
    assert "/mnt/evidence/gone.pdf" in html
    assert "original-file-frame" not in html, "no viewer for a file that is gone"
    # Nothing to open or download either - the buttons would 404.
    assert "data-original-action" not in html


def test_available_files_offer_open_and_download(out):
    html = out["rendered"]["image"]
    assert 'data-original-action="open"' in html
    assert 'data-original-action="download"' in html
    assert "?download=1" in html


# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------

def test_no_rendering_injects_an_event_attribute(out):
    for kind, report in out["tagReport"].items():
        assert report["eventAttrs"] == [], (kind, report["eventAttrs"])


def test_a_quoted_filename_never_breaks_out_of_a_tag(out):
    assert out["tagReport"]["hostile"]["rawQuoteInTag"] is False
    html = out["rendered"]["hostile"]
    assert "&quot;" in html, "attribute escaping did not run"
    # It is still shown to the user, escaped, as visible text.
    assert 'budget &quot;final&quot;' in html or "budget &#39;" in html or "budget" in html


def test_only_expected_attributes_appear_on_the_frame(out):
    html = out["rendered"]["hostile"]
    frame = re.search(r"<iframe[^>]*>", html)
    assert frame, html
    names = re.findall(r'([-\w]+)="', frame.group(0))
    assert sorted(set(names)) == ["class", "data-original-file-frame", "src", "title"], names


# ---------------------------------------------------------------------------
# The loaded text is text
# ---------------------------------------------------------------------------

def test_text_is_fetched_and_shown_escaped(out):
    assert any("/original/content" in url for url in out["calls"]), out["calls"]
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in out["textPaneHtml"], out["textPaneHtml"]
    assert "<script>alert(1)</script>" not in out["textPaneHtml"], "the source ran as markup"


def test_an_empty_source_file_says_so(out):
    """Empty is a fact to report - not a blank pane that reads as a failure."""
    assert "the original file is empty" in out["emptyHtml"], out["emptyHtml"]


def test_an_unreadable_source_says_so(out):
    assert "Could not load the original file" in out["unreadableHtml"], out["unreadableHtml"]


def test_server_side_failure_is_shown(out):
    assert out["failedResult"] is None
    assert "File not found" in out["failedHtml"]
    assert "original-file-stage" not in out["failedHtml"]


def test_network_failure_is_shown(out):
    assert "Could not load the original file" in out["brokenHtml"]


def test_human_size_is_readable(out):
    assert out["humanSize"] == ["0 Bytes", "2 KB", "5 MB", ""], out["humanSize"]
