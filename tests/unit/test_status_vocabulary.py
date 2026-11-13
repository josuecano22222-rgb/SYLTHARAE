"""Unit: one owner for how a status is shown.

The application says `COMPLETED_WITH_WARNINGS`; the interface decides that this
is a warning. Those are two different facts, and the defect this suite prevents
is the one that existed before it: three JavaScript maps and a handful of
hand-written badges, each deciding for itself - and the job list and the job
detail page disagreeing about the same status.

So the tests are mostly about *where the answer lives*:

* every status the application can actually emit is in the vocabulary, taken
  from the state machine and task manager rather than retyped;
* a status the vocabulary has never heard of is not given a meaning;
* the server-rendered badge and the JavaScript renderer read the same table;
* nothing keeps a second copy of the mapping.
"""

from __future__ import annotations

import json
import pathlib
import re

from core.frontend.status_vocabulary import (
    PRESENTATION_STATES,
    VOCABULARY,
    known_statuses,
    normalise,
    present,
    state_for,
    vocabulary_json,
)

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
STATUS_JS = PROJECT_ROOT / "static/js/modules/core/status.js"


class TestTheVocabularyCoversWhatTheApplicationEmits:
    def test_every_job_state_is_in_the_vocabulary(self):
        """Taken from the state machine, so a new job state cannot be missed."""
        from services.jobs import job_state

        missing = [state for state in job_state.ALL_STATES
                   if normalise(state) not in VOCABULARY]
        assert missing == [], (
            "the job engine can emit these states and the interface has no "
            "presentation for them: " + repr(missing))

    def test_every_task_state_is_in_the_vocabulary(self):
        from Api.task_manager import TaskStatus

        missing = [status.value for status in TaskStatus
                   if normalise(status.value) not in VOCABULARY]
        assert missing == [], missing

    def test_every_interface_lifecycle_status_is_in_the_vocabulary(self):
        from core.interfaces import InterfaceStatus

        missing = [str(status) for status in InterfaceStatus
                   if normalise(str(status)) not in VOCABULARY]
        assert missing == [], missing

    def test_the_states_the_brief_named_are_covered(self):
        for status in ("running", "paused", "queued", "completed",
                       "completed_with_warnings", "failed", "active",
                       "inactive", "deprecated", "retired"):
            entry = present(status)
            assert entry.known, status
            assert entry.state in PRESENTATION_STATES

    def test_completed_with_warnings_is_a_warning_not_a_success(self):
        """The example from the brief, and the answer it has to give."""
        entry = present("COMPLETED_WITH_WARNINGS")
        assert entry.state == "warning"
        assert entry.label == "Completed with warnings"


class TestThePresentationStatesAreFew:
    def test_every_state_is_one_of_the_declared_ones(self):
        for status, (state, label) in VOCABULARY.items():
            assert state in PRESENTATION_STATES, (status, state)
            assert label, status

    def test_there_are_far_more_statuses_than_states(self):
        """The point of the mapping: many words, few presentations."""
        assert len(VOCABULARY) > 3 * len(PRESENTATION_STATES)


class TestAnUnknownStatusIsNotGivenAMeaning:
    def test_it_keeps_its_own_words(self):
        entry = present("SOMETHING_NEW_FROM_A_SERVICE")
        assert entry.known is False
        assert entry.state == "neutral"
        assert entry.label == "Something New From A Service"

    def test_a_caller_label_is_kept(self):
        assert present("WHATEVER", label="What the service said").label == \
            "What the service said"

    def test_it_is_marked_in_the_markup(self):
        """A reader can see that this status was not one the interface knows."""
        assert present("WHATEVER").to_dict()["known"] is False

    def test_normalisation_is_forgiving_about_form(self):
        for form in ("completed_with_warnings", "COMPLETED_WITH_WARNINGS",
                     "Completed With Warnings", "completed-with-warnings"):
            assert state_for(form) == "warning", form

    def test_an_empty_status_has_no_state(self):
        entry = present(None)
        assert entry.state == "neutral" and entry.label == ""


class TestOneOwner:
    def test_the_json_and_the_table_are_the_same_table(self):
        payload = json.loads(vocabulary_json())
        assert set(payload) == set(known_statuses())
        for status, values in payload.items():
            state, label = VOCABULARY[status]
            assert values == {"state": state, "label": label}

    def test_the_javascript_renderer_knows_the_same_states(self):
        """Server-rendered and client-rendered badges have to agree."""
        text = STATUS_JS.read_text()
        block = text[text.index("var STATE_CLASSES"):text.index("};", text.index("var STATE_CLASSES"))]
        states = set(re.findall(r"(\w+):\s*'bg-", block))
        assert states == set(PRESENTATION_STATES), (
            "the JavaScript renderer and the Python vocabulary disagree about "
            "which presentation states exist: " + repr(sorted(states)))

    def test_the_javascript_renderer_reads_the_injected_vocabulary(self):
        text = STATUS_JS.read_text()
        assert "status-vocabulary" in text
        assert "JSON.parse" in text

    def test_the_page_injects_the_vocabulary(self):
        base = (PROJECT_ROOT / "templates/base.html").read_text()
        assert 'id="status-vocabulary"' in base
        assert "modules/core/status.js" in base

    def test_no_template_keeps_its_own_status_colour_map(self):
        """Three local maps is what this replaced; none may come back."""
        offenders = []
        for path in sorted((PROJECT_ROOT / "templates").rglob("*.html")):
            text = path.read_text(errors="ignore")
            if re.search(r"\{RUNNING:\s*'primary'|RUNNING:'primary'", text):
                offenders.append(str(path.relative_to(PROJECT_ROOT)))
            if 'bg-${cls}' in text:
                offenders.append(str(path.relative_to(PROJECT_ROOT)))
        assert offenders == [], (
            "these templates decide a status colour for themselves; the "
            "vocabulary owns that decision:\n  " + "\n  ".join(sorted(set(offenders))))

    def test_the_component_does_not_own_the_vocabulary(self):
        """The badge turns a state into classes; it does not know the words.

        Its own prose may name a status as an example - that is how the rule
        is written down - but its markup may not know one.
        """
        text = (PROJECT_ROOT / "templates/components/status_badge.html").read_text()
        markup = re.sub(r"\{#.*?#\}", " ", text, flags=re.DOTALL)
        for status in ("COMPLETED_WITH_WARNINGS", "RUNNING", "QUEUED", "PAUSED"):
            assert status not in markup


class TestTheBadgeRendersTheVocabulary:
    def _render(self, source):
        import jinja2

        environment = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(PROJECT_ROOT / "templates")),
            autoescape=True)
        environment.globals["_"] = lambda text: text
        environment.globals["status_presentation"] = present
        return environment.from_string(source).render()

    def test_an_application_status_renders_its_state_and_words(self):
        out = self._render("""{% from 'components/status_badge.html' import status_badge %}
{{ status_badge(status='completed_with_warnings') }}""")
        assert 'class="badge bg-warning text-dark"' in out
        assert "Completed with warnings" in out
        assert 'data-status-known="true"' in out

    def test_an_unknown_status_says_so_without_guessing(self):
        out = self._render("""{% from 'components/status_badge.html' import status_badge %}
{{ status_badge(status='SOMETHING_ELSE') }}""")
        assert "bg-secondary" in out
        assert 'data-status-known="false"' in out
        assert "Something Else" in out

    def test_a_chip_that_is_not_a_status_still_works(self):
        """Counts, methods and ids keep passing an explicit label and tone."""
        out = self._render("""{% from 'components/status_badge.html' import status_badge %}
{{ status_badge('#7', tone='muted') }}""")
        assert "bg-secondary" in out and "#7" in out
        assert "data-status-known" not in out
