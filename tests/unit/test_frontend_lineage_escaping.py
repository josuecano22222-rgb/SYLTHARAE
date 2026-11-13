"""Unit: lineage rendering escapes untrusted names for the right context.

These tests import the SHIPPED module
static/js/modules/file-operations/file-details.js and execute its real
functions under Node - not a copy of the logic, so a regression in the shipped
file fails here.

Why this matters: a filename inside an uploaded archive is attacker-chosen.
File Details renders those names into the lineage block, so an unescaped name
is stored XSS against whoever opens the file. The specific hazard found while
writing this:

    escapeHtml() escapes via textContent -> innerHTML, which handles & < > but
    NOT quotes, because a text node never contains them. Correct for element
    content, unsafe inside a double-quoted attribute. A lineage name of

        x" onmouseover="alert(1)

    survives escapeHtml unchanged and breaks out of data-lineage-name.

escapeAttribute() exists for attribute context. Both escapers are asserted
here, in their own contexts.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

MODULE = PROJECT_ROOT / "static/js/modules/file-operations/file-details.js"

# Minimal DOM stub. innerHTML replicates the browser's text-node
# serialisation: & < > escaped, quotes NOT. escapeHtml() depends on exactly
# that behaviour, so a stub that over-escaped would hide the defect this
# suite exists to catch.
DOM_STUB = r"""
const esc = (t) => String(t)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const el = () => {
    let t = '';
    return {
        set textContent(v) { t = String(v); },
        get innerHTML() { return esc(t); },
        set innerHTML(v) { t = v; },
        style: {}, classList: { add(){}, remove(){}, toggle(){} },
        addEventListener(){}, removeEventListener(){},
        appendChild(){}, setAttribute(){}, getAttribute(){ return null; },
        querySelectorAll(){ return []; }, querySelector(){ return null; }
    };
};
globalThis.document = {
    createElement: el,
    addEventListener(){}, removeEventListener(){},
    getElementById(){ return null; },
    querySelector(){ return null; },
    querySelectorAll(){ return []; },
    body: el(), documentElement: el()
};
globalThis.window = globalThis;
globalThis.localStorage = { getItem(){ return null; }, setItem(){}, removeItem(){} };
globalThis.fetch = async () => ({ ok: false, status: 0, json: async () => ({}) });
"""

HARNESS = r"""
import '__STUB__';
const mod = await import('__MODULE__');

const HOSTILE = 'x" onmouseover="alert(1)';
const file = {
    lineage: {
        ancestors: [{ id: 7, name: HOSTILE, depth: 1 }],
        descendants: [{ id: 9, name: "child<script>alert(2)</script>.png", depth: 1 }],
        errors: []
    }
};
const html = mod.renderLineageSection(file);

// Pull the attribute value back out of the rendered markup.
const m = /data-lineage-name="([^"]*)"/.exec(html);

// Enumerate the attribute NAMES actually present on each <a> tag, using a
// quote-aware walk. A plain /name=/ regex false-positives on correctly escaped
// output, where 'onmouseover=' appears as literal characters inside a quoted
// value - it cannot tell an injected attribute from escaped text. What decides
// the security question is what an HTML parser would see.
const parseAttrs = (inner) => {
    const names = [];
    let i = 0;
    while (i < inner.length) {
        while (i < inner.length && /\s/.test(inner[i])) i++;
        if (i >= inner.length) break;
        const nm = /^[A-Za-z_:][-\w:.]*/.exec(inner.slice(i));
        if (!nm) { i++; continue; }
        names.push(nm[0]);
        i += nm[0].length;
        if (inner[i] === '=') {
            i++;
            const q = inner[i];
            if (q === '"' || q === "'") {
                i++;
                while (i < inner.length && inner[i] !== q) i++;
                i++;
            } else {
                while (i < inner.length && !/\s/.test(inner[i])) i++;
            }
        }
    }
    return names;
};
const attrNames = [...html.matchAll(/<a\b([^>]*)>/g)]
    .flatMap(t => parseAttrs(t[1]));

process.stdout.write(JSON.stringify({
    html: html,
    attributeValue: m ? m[1] : null,
    anchorAttributes: attrNames,
    hasEventAttr: attrNames.some(n => n.toLowerCase().startsWith('on')),
    hasScriptTag: /<script/i.test(html),
    escapeAttribute: mod.escapeAttribute(HOSTILE),
    topLevel: mod.renderLineageSection({ lineage: { ancestors: [], descendants: [], errors: [] } }),
    noLineage: mod.renderLineageSection({}),
    badId: mod.renderLineageSection({
        lineage: {
            ancestors: [{ id: "javascript:alert(3)", name: "n", depth: 1 }],
            descendants: [], errors: []
        }
    })
}));
"""


@pytest.fixture(scope="module")
def node():
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    return shutil.which("node")


@pytest.fixture(scope="module")
def result(node, tmp_path_factory):
    """Import the real module under Node and return its rendered output."""
    assert MODULE.exists(), f"shipped module missing: {MODULE}"
    tmp = tmp_path_factory.mktemp("jslineage")
    stub = tmp / "domstub.mjs"
    stub.write_text(DOM_STUB)
    harness = tmp / "harness.mjs"
    harness.write_text(
        HARNESS.replace("__STUB__", stub.as_uri())
               .replace("__MODULE__", MODULE.as_uri())
    )
    proc = subprocess.run(
        [node, str(harness)], capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 0, f"node failed: {proc.stderr[:2000]}"
    return json.loads(proc.stdout)


def test_no_event_handler_can_be_injected(result):
    """The core security assertion: no attribute breakout in the output."""
    assert result["hasEventAttr"] is False, result["anchorAttributes"]


def test_anchor_carries_only_the_attributes_we_put_there(result):
    """An injected attribute would show up here as an extra name."""
    expected = {"href", "class", "data-lineage-id", "data-lineage-name"}
    assert set(result["anchorAttributes"]) == expected, result["anchorAttributes"]


def test_no_script_element_survives(result):
    assert result["hasScriptTag"] is False, result["html"]


def test_the_hostile_quote_was_neutralised_in_the_attribute(result):
    assert result["attributeValue"] is not None
    assert '"' not in result["attributeValue"], result["attributeValue"]
    assert "&quot;" in result["attributeValue"], result["attributeValue"]


def test_escape_attribute_handles_all_five_dangerous_characters(node, tmp_path):
    stub = tmp_path / "s.mjs"
    stub.write_text(DOM_STUB)
    script = tmp_path / "t.mjs"
    script.write_text(
        f"import '{stub.as_uri()}';\n"
        f"const m = await import('{MODULE.as_uri()}');\n"
        "process.stdout.write(JSON.stringify(m.escapeAttribute(`<>&\"'`)));\n"
    )
    out = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[:1000]
    assert json.loads(out.stdout) == "&lt;&gt;&amp;&quot;&#39;"


def test_escape_attribute_passes_benign_names_unchanged(node, tmp_path):
    stub = tmp_path / "s2.mjs"
    stub.write_text(DOM_STUB)
    script = tmp_path / "t2.mjs"
    script.write_text(
        f"import '{stub.as_uri()}';\n"
        f"const m = await import('{MODULE.as_uri()}');\n"
        "process.stdout.write(JSON.stringify(m.escapeAttribute('report Q1 2026.pdf')));\n"
    )
    out = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[:1000]
    assert json.loads(out.stdout) == "report Q1 2026.pdf"


def test_non_integer_ids_are_dropped_not_rendered(result):
    """A malformed id must not reach an href or a handler."""
    assert "javascript:" not in result["badId"], result["badId"]
    assert "lineage-link" not in result["badId"], result["badId"]


def test_nothing_renders_when_there_is_no_lineage(result):
    assert result["noLineage"] == ""
    assert result["topLevel"] == ""


def test_lineage_block_is_present_for_a_nested_object(result):
    assert "lineage-section" in result["html"]
    assert "data-lineage-id=\"7\"" in result["html"]
    assert "data-lineage-id=\"9\"" in result["html"]
