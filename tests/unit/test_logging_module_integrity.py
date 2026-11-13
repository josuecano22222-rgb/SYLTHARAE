"""Importing the CLI must not replace the stdlib ``logging`` module.

``apps/cli/main.py`` used to delete ``logging`` from ``sys.modules`` and
re-import it, to "protect the standard library logging module" from a
multiprocessing spawn or a shadowing ``managers.logging``. Executing the module
a second time does not protect anything - it builds a *second* logging
universe:

* a new ``RootLogger``, a new ``Manager``, new handler and level state;
* every module that imported logging earlier (the web app, the database layer,
  libraries, the test harness) keeps writing to the first universe, while code
  imported later writes to the second, so a configured handler/level silently
  does not apply to half the process.

The observable damage in the suite: after a CLI integration test imported
``apps.cli.main``, a warning logged by the database layer reached stderr but
never reached pytest's capture handler, so an unrelated pool-advice test failed
with "advice emitted 0 times" while the message was visibly printed. These
tests pin both the module identity and the capture consequence; the fix is a
plain ``import logging`` plus a loud refusal if a local module ever shadows it.
"""

import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def test_importing_the_cli_keeps_the_logging_module_identity():
    module_before = sys.modules.get("logging")
    root_before = logging.getLogger()
    manager_before = logging.Logger.manager

    import apps.cli.main  # noqa: F401

    assert sys.modules.get("logging") is module_before, (
        "apps.cli.main replaced the stdlib logging module in sys.modules"
    )
    assert logging.getLogger() is root_before, (
        "importing apps.cli.main created a second root logger"
    )
    assert logging.Logger.manager is manager_before, (
        "importing apps.cli.main created a second logging manager"
    )


def test_capture_still_works_after_the_cli_is_imported(caplog):
    """The consequence, not just the mechanism: pytest must still see records."""
    import apps.cli.main  # noqa: F401
    from database.database import database as db_module

    with caplog.at_level(logging.WARNING, logger="database.database.database"):
        db_module.logger.warning(
            "Connection pool size (4) may be too small for concurrent processing."
        )

    captured = [r for r in caplog.records if "may be too small" in r.getMessage()]
    assert captured, (
        "a warning logged after importing apps.cli.main never reached the"
        " capture handler: the process now has two logging configurations"
    )
