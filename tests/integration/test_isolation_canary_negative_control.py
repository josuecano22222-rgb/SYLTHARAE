"""The canary's negative control: with the reset disabled it must fail.

A canary that has never been seen to fail proves nothing. Its partners
(``test_isolation_canary_plants`` and ``test_isolation_canary_asserts``) pass in
both orders and would keep passing if the reset fixture silently became a no-op,
because the assertion "the marker is not here" is trivially true when nothing
ever planted it in this run.

So this module runs the pair again, in a **subprocess**, with the reset turned
off - not by a product switch (there is none, and adding one would let a
configuration mistake disable isolation in production) but by an **injected
pytest plugin** that replaces ``conftest.reset_database`` with a no-op for the
duration of that run. The pair must then fail, and it must fail on the marker
assertion, not on an error somewhere else.

The subprocess is the point: the plugin has to be installed before the
collection this module measures, and this module's own process keeps its real
reset.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import textwrap

import pytest

pytestmark = pytest.mark.integration

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
PLANTS = "tests/integration/test_isolation_canary_plants.py"
ASSERTS = "tests/integration/test_isolation_canary_asserts.py"

#: A pytest plugin that turns the module boundary into a no-op.
#:
#: ``tests/conftest.py`` does ``from suite_isolation import reset_database``, so
#: the fixture resolves that name from the conftest module's globals on every
#: call. Rebinding it there disables the reset without touching the product or
#: the fixture itself. Hooked at ``pytest_collection_finish`` because the
#: conftest module is imported by then and no test has run yet.
PLUGIN = textwrap.dedent('''
    """Injected by test_isolation_canary_negative_control: disable the reset."""
    import sys


    def _disabled(cfg):
        return []


    def pytest_collection_finish(session):
        patched = []
        for name, module in list(sys.modules.items()):
            if name == "conftest" or name.endswith(".conftest"):
                if hasattr(module, "reset_database"):
                    module.reset_database = _disabled
                    patched.append(name)
        print("RESET_DISABLED_IN " + ", ".join(patched))
''')


@pytest.fixture(scope="module")
def disabled_run(tmp_path_factory):
    """The canary pair, run in a subprocess with the reset disabled."""
    plugin_dir = tmp_path_factory.mktemp("reset_disabled")
    (plugin_dir / "reset_disabled_plugin.py").write_text(PLUGIN, encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", PLANTS, ASSERTS,
         "-p", "reset_disabled_plugin", "-q", "-p", "no:cacheprovider",
         "--no-header", "-rA"],
        cwd=str(PROJECT_ROOT),
        env={**_environment(), "PYTHONPATH": str(plugin_dir)},
        capture_output=True, text=True, timeout=900,
    )
    return completed


def _environment():
    import os

    return dict(os.environ)


def test_the_plugin_was_actually_injected(disabled_run):
    """Without this the rest of the module could pass for the wrong reason."""
    assert "RESET_DISABLED_IN" in disabled_run.stdout, (
        "the injected plugin never patched conftest; the run proves nothing\n"
        + disabled_run.stdout[-3000:])


def test_the_canary_pair_fails_when_the_reset_is_disabled(disabled_run):
    assert disabled_run.returncode != 0, (
        "the canary pair passed with the reset disabled - the canary cannot "
        "detect a broken reset\n" + disabled_run.stdout[-3000:])


def test_it_fails_on_the_marker_assertion(disabled_run):
    """The right test, failing for the right reason."""
    output = disabled_run.stdout
    assert "test_the_previous_module_row_is_not_visible_here" in output, output[-3000:]
    assert "survived the module boundary" in output, (
        "the pair failed, but not on the leaked row:\n" + output[-3000:])
