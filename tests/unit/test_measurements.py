"""Unit: a reported figure always carries the basis it was derived from.

The product rule (spec section 8) is that SYLTHARAE must never present a
fabricated operational metric as though it were a measurement. These tests pin
the type that makes that rule structural rather than a matter of discipline:

* an ``unavailable`` measurement cannot be built with a value - so nobody can
  smuggle a plausible constant in behind a "no data" path;
* a ``measured``/``estimated`` measurement cannot be built without one;
* the wording the interface shows distinguishes the two, and an unavailable
  figure reads as a statement, not as a number.
"""

import pytest

from core.measurements import (
    ESTIMATED,
    MEASURED,
    NO_MEASUREMENT_TEXT,
    UNAVAILABLE,
    Measurement,
    estimated,
    measured,
    unavailable,
)


class TestConstructionRules:
    def test_unavailable_cannot_carry_a_value(self):
        """The whole point: a fallback constant is not expressible."""
        with pytest.raises(ValueError, match="cannot carry a value"):
            Measurement(value=98.5, state=UNAVAILABLE)

    @pytest.mark.parametrize("state", [MEASURED, ESTIMATED])
    def test_a_claimed_value_must_exist(self, state):
        with pytest.raises(ValueError, match="needs a value"):
            Measurement(value=None, state=state)

    def test_unknown_state_is_rejected(self):
        with pytest.raises(ValueError, match="unknown measurement state"):
            Measurement(value=1.0, state="probably")


class TestPresentation:
    def test_measured_shows_no_qualifier(self):
        assert measured(97.42, unit="%").display() == "97.4 %"
        assert measured(97.42, unit="%").label == ""

    def test_estimated_is_labelled(self):
        value = estimated(1.94, unit="s", sample_size=12,
                          detail="Elapsed time / files processed, across 12 completed jobs")
        assert value.display() == "Estimated 1.9 s"
        assert value.label == "Estimated"
        assert "12 completed jobs" in value.detail

    def test_unavailable_says_so_and_explains(self):
        value = unavailable("Insufficient data: no objects were stored in the last 7 days",
                            unit="%")
        assert value.state == UNAVAILABLE
        assert value.available is False
        assert value.display().startswith("Insufficient data")
        assert value.value is None

    def test_unavailable_without_a_reason_still_states_it(self):
        assert unavailable().display() == NO_MEASUREMENT_TEXT

    def test_dict_exposes_everything_a_template_needs(self):
        payload = measured(12.0, unit="s", sample_size=4, detail="2 of 4 objects").to_dict()
        assert payload["state"] == MEASURED
        assert payload["available"] is True
        assert payload["display"] == "12 s"
        assert payload["detail"] == "2 of 4 objects"
        assert payload["sample_size"] == 4
