"""Unit: prompt() call sites must await the Promise-based override.

``static/js/modules/messages/alert-replacement.js`` replaces the native
``window.prompt`` with a modal that **returns a Promise**. Any caller that
treats the return value as a string throws
``TypeError: name.trim is not a function`` (the exact console error seen
on the advanced-search export/save actions) or - as in
``renameSavedSearch`` before the fix - silently cancels every rename
because the guard coerces a non-string to ``''``.

These tests read the SHIPPED scripts and pin the contract: every prompt()
call must be awaited, and the null/undefined cancelled result must be
checked before any string method is used.
"""

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

SAVED_SEARCHES = PROJECT_ROOT / 'static/js/pages/saved-searches-page.js'
GLOBAL_SEARCH = PROJECT_ROOT / 'static/js/modules/search/global-search.js'
ALERT_REPLACEMENT = PROJECT_ROOT / 'static/js/modules/messages/alert-replacement.js'

AWAITED_PROMPT = re.compile(r'=\s*await\s+(?:window\.)?prompt\s*\(')
ANY_PROMPT_CALL = re.compile(r'(?<![\w$])(?:window\.)?prompt\s*\(')


class TestPromptOverrideContract:
    def test_alert_replacement_returns_a_promise(self):
        source = ALERT_REPLACEMENT.read_text(encoding='utf-8')
        assert 'window.prompt = function' in source
        assert 'return new Promise' in source

    def test_renamed_saved_search_awaits_prompt(self):
        source = SAVED_SEARCHES.read_text(encoding='utf-8')
        assert AWAITED_PROMPT.search(source), (
            'renameSavedSearch must await the Promise-based prompt() '
            'override; without await, name.trim() throws or the rename '
            'silently no-ops'
        )

    def test_renamed_saved_search_checks_cancel_before_trimming(self):
        source = SAVED_SEARCHES.read_text(encoding='utf-8')
        assert re.search(r'newName === null', source)
        # string methods only after the typeof guard
        assert re.search(r"typeof newName === 'string'", source)

    def test_global_search_awaits_prompt(self):
        source = GLOBAL_SEARCH.read_text(encoding='utf-8')
        assert AWAITED_PROMPT.search(source)

    def test_no_unawaited_prompt_calls_in_shipped_scripts(self):
        offenders = []
        scripts = list((PROJECT_ROOT / 'static/js/pages').glob('*.js'))
        scripts.append(GLOBAL_SEARCH)
        for path in scripts:
            source = path.read_text(encoding='utf-8')
            for line in source.splitlines():
                stripped = line.lstrip()
                if stripped.startswith(('*', '//', '/*')):
                    continue
                if not ANY_PROMPT_CALL.search(line):
                    continue
                if 'function prompt' in line or 'promptAsync' in line:
                    continue
                if 'window.prompt =' in line:
                    continue
                if 'await ' in line:
                    continue
                offenders.append(f'{path.name}: {line.strip()}')
        assert not offenders, (
            'prompt() returns a Promise; every call must await it:\n'
            + '\n'.join(offenders)
        )
