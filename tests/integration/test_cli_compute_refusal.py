"""The CLI adapter must always be able to say what it did and what refused.

Regression guards for two defects in the compute-policy wiring of
``apps/cli/main.py``:

``output_json`` was read one line before it was assigned, so every
``cli_main(...)`` invocation raised ``NameError`` before doing any work - the
non-interactive entry point was broken end to end while the policy module's own
unit tests stayed green. And in ``--json`` mode the policy printer was replaced
by a no-op, so a refusal (GPU-ONLY on a host with no accelerator) exited 1 with
*no output at all*: a machine-readable consumer learned nothing.

The refusal is produced here by patching ``mode_support``, so the tests hold on
hosts with and without accelerators alike.
"""

import contextlib
import io
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from apps.cli.main import cli_main  # noqa: E402
from core.compute import policy as compute_policy  # noqa: E402


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli_main(argv)
    return code, out.getvalue(), err.getvalue()


def _base_args():
    return ["--path", "/nonexistent/path/for/test", "--source", "s", "--side", "t"]


def _refusing_support(selection=None, *args, **kwargs):
    selection = selection or compute_policy.mode_selection()
    return compute_policy.ModeSupport(
        ok=False,
        selection=selection,
        problem="GPU-ONLY was requested but no usable accelerator is present",
        action="select another policy: --compute-mode cpu",
    )


def test_json_run_reports_a_refusal_instead_of_exiting_silently(monkeypatch):
    monkeypatch.setattr(compute_policy, "mode_support", _refusing_support)
    code, out, _ = _run(_base_args() + ["--json", "--compute-mode", "gpu"])
    assert code == 1
    payload = json.loads(out)          # the stream must stay parseable
    assert payload["success"] is False
    assert "refused" in payload["error"]
    detail = json.dumps(payload)
    assert "GPU-ONLY was requested" in detail, "the reason was swallowed"
    assert any("select another policy" in line
               for line in payload["details"]["policy"])


def test_text_run_reports_a_refusal(monkeypatch):
    monkeypatch.setattr(compute_policy, "mode_support", _refusing_support)
    code, out, _ = _run(_base_args() + ["--compute-mode", "gpu"])
    assert code == 1
    assert "no usable accelerator" in out


def test_quiet_run_still_reports_a_refusal(monkeypatch):
    """--quiet suppresses progress, never the reason a run did not start."""
    monkeypatch.setattr(compute_policy, "mode_support", _refusing_support)
    code, out, _ = _run(_base_args() + ["--quiet", "--compute-mode", "gpu"])
    assert code == 1
    assert "no usable accelerator" in out


def test_json_flag_is_resolved_before_the_policy_is_applied():
    """The call that reads OUTPUT_JSON must not precede its assignment."""
    code, out, _ = _run(_base_args() + ["--json", "--compute-mode", "cpu"])
    # Path validation rejects the request, but only *after* the policy step,
    # and it must do so in JSON - a NameError here would abort with a traceback.
    assert code == 1, (code, out)
    payload = json.loads(out)
    assert payload["success"] is False
    assert "Invalid ingestion request" in payload["error"]


def test_selected_mode_is_announced_at_startup():
    code, out, _ = _run(_base_args() + ["--compute-mode", "cpu"])
    assert "Compute Mode: CPU-ONLY" in out
    assert "command line" in out, "the configuration layer must be named"
    assert code == 1  # rejected path, not a policy refusal


def test_unknown_mode_is_rejected_with_an_explanation():
    code, out, _ = _run(_base_args() + ["--compute-mode", "quantum"])
    assert code == 1
    assert "ERROR" in out


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
