"""Chart customisation is attached only to a chart that still exists on the page.

The reported failure, "Uncaught Error: Chart canvas is required", came from a
150 ms timer in chart-export.js that attached the customiser to a chart which
had been destroyed (a chart-type change rebuilds it; Chart.js clears
``chart.canvas``). The timer's try/catch cannot catch an error thrown later,
so the fix is a liveness check when the timer fires. The customiser itself
keeps rejecting bad input, which is a real programming error for a direct
caller.
"""
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CUSTOMIZER = (PROJECT_ROOT / "static/js/modules/charts/chart-customizer.js").read_text(encoding="utf-8")
EXPORT = (PROJECT_ROOT / "static/js/modules/charts/chart-export.js").read_text(encoding="utf-8")


def _timer_bodies(source):
    """The body of every 150 ms timer that attaches the customiser.

    A timer body is taken only up to its own closing ``}, 150);``, and never
    across another ``setTimeout`` (a 10 ms timer can precede it).
    """
    pattern = re.compile(r"setTimeout\(\(\)\s*=>\s*\{((?:(?!setTimeout).)*?)\}, 150\);", re.S)
    return [m.group(1) for m in pattern.finditer(source) if "attachCustomizationButton" in m.group(1)]


def test_the_customiser_exports_the_liveness_check():
    assert re.search(r"function isAttachable\(chart\)", CUSTOMIZER)
    assert "isAttachable: isAttachable" in CUSTOMIZER


def test_liveness_requires_a_connected_canvas_and_a_config():
    match = re.search(r"function isAttachable\(chart\)\s*\{\s*return ([^;]+);", CUSTOMIZER)
    assert match, "isAttachable must be a single return expression"
    expression = match.group(1)
    assert "chart.canvas" in expression
    assert "isConnected" in expression, "a removed canvas (soft navigation) must not count as live"
    assert "chart.config" in expression


def test_both_deferred_callers_check_liveness_before_attaching():
    bodies = _timer_bodies(EXPORT)
    assert len(bodies) == 2, f"expected two deferred attach sites in chart-export.js, found {len(bodies)}"
    for body in bodies:
        guard = body.find("isAttachable(chart)")
        attach = body.find("attachCustomizationButton(")
        assert guard != -1 and attach != -1 and guard < attach, body


def test_the_customiser_still_rejects_a_missing_canvas_for_direct_callers():
    # A direct call with a bad chart is a programming error, so it still throws.
    assert "throw new Error('Chart canvas is required')" in CUSTOMIZER
    assert "throw new Error('Chart instance is required')" in CUSTOMIZER


def test_no_blanket_catch_swallows_the_attach_error():
    # The fix must not hide unrelated failures: the attach timers keep their
    # narrow guard rather than wrapping everything in a silent catch.
    for body in _timer_bodies(EXPORT):
        if "try" in body:
            assert "console.warn" in body, "a caught attach error must still be reported"
