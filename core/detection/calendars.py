"""Calendar conversion to the proleptic Gregorian calendar.

Two non-Gregorian calendars are supported, with deliberately different
confidence:

* **Jalali (Solar Hijri, Iran/Afghanistan)** - converted *exactly* with the
  33-year break-table algorithm used by ``jalaali-js`` (Borkowski's
  arithmetic, valid for Jalali years -61..3177). The Iranian civil calendar is
  itself defined astronomically but this table reproduces it for the whole
  modern range, so the result is a single day.

* **Hijri (Islamic lunar)** - converted with the *tabular* (arithmetic, civil
  epoch 16 July 622 CE, 11-leap-year 30-year cycle) calendar. Real-world
  dates follow either the Umm al-Qura tables or local moon sighting, which
  differ from the tabular calendar by up to about two days. A Hijri date is
  therefore returned as a **range** (``HIJRI_UNCERTAINTY_DAYS`` either side),
  never as a falsely precise single day.
"""

from __future__ import annotations

import datetime
from typing import Tuple

HIJRI_UNCERTAINTY_DAYS = 2
HIJRI_METHOD = "tabular-islamic-civil (±2 days vs. Umm al-Qura/sighting)"
JALALI_METHOD = "jalaali 33-year break table (exact)"

_JALALI_BREAKS = (-61, 9, 38, 199, 426, 686, 756, 818, 1111, 1181, 1210, 1635, 2060,
                  2097, 2192, 2262, 2324, 2394, 2456, 3178)


class CalendarError(ValueError):
    """A date that does not exist in its calendar (e.g. 31 Mehr)."""


def _div(a: int, b: int) -> int:
    """Integer division truncating toward zero (JavaScript ``~~(a / b)``)."""
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b >= 0) else -q


def _mod(a: int, b: int) -> int:
    return a - _div(a, b) * b


def _jal_cal(jy: int) -> Tuple[int, int, int]:
    """(leap, gregorian_year, march_day_of_farvardin_1) for Jalali year ``jy``."""
    if jy < _JALALI_BREAKS[0] or jy >= _JALALI_BREAKS[-1]:
        raise CalendarError(f"Jalali year {jy} is outside the supported range")
    gy = jy + 621
    leap_j = -14
    jp = _JALALI_BREAKS[0]
    jump = 0
    for jm in _JALALI_BREAKS[1:]:
        jump = jm - jp
        if jy < jm:
            break
        leap_j += _div(jump, 33) * 8 + _div(_mod(jump, 33), 4)
        jp = jm
    n = jy - jp
    leap_j += _div(n, 33) * 8 + _div(_mod(n, 33) + 3, 4)
    if _mod(jump, 33) == 4 and jump - n == 4:
        leap_j += 1
    leap_g = _div(gy, 4) - _div((_div(gy, 100) + 1) * 3, 4) - 150
    march = 20 + leap_j - leap_g
    if jump - n < 6:
        n = n - jump + _div(jump + 4, 33) * 33
    leap = _mod(_mod(n + 1, 33) - 1, 4)
    if leap == -1:
        leap = 4
    return leap, gy, march


def jalali_is_leap(jy: int) -> bool:
    return _jal_cal(jy)[0] == 0


def jalali_month_length(jy: int, jm: int) -> int:
    if not 1 <= jm <= 12:
        raise CalendarError(f"Jalali month {jm} does not exist")
    if jm <= 6:
        return 31
    if jm <= 11:
        return 30
    return 30 if jalali_is_leap(jy) else 29


def jalali_to_gregorian(jy: int, jm: int, jd: int) -> datetime.date:
    """Exact conversion; raises :class:`CalendarError` for non-existent dates."""
    if not 1 <= jd <= jalali_month_length(jy, jm):
        raise CalendarError(f"{jd} is not a day of Jalali month {jm} in {jy}")
    _, gy, march = _jal_cal(jy)
    offset = (jm - 1) * 31 - _div(jm, 7) * (jm - 7) + jd - 1
    return datetime.date(gy, 3, march) + datetime.timedelta(days=offset)


# Tabular Islamic calendar -------------------------------------------------

#: Julian Day Number of 1 Muharram 1 AH in the civil (Friday) epoch.
_HIJRI_EPOCH_JDN = 1948440
_JDN_OF_ORDINAL_1 = 1721426  # 0001-01-01 (proleptic Gregorian)


def hijri_month_length_tabular(hy: int, hm: int) -> int:
    if not 1 <= hm <= 12:
        raise CalendarError(f"Hijri month {hm} does not exist")
    if hm % 2 == 1:
        return 30
    if hm < 12:
        return 29
    return 30 if (14 + 11 * hy) % 30 < 11 else 29


def hijri_to_gregorian_tabular(hy: int, hm: int, hd: int) -> datetime.date:
    """Tabular conversion (the centre of the uncertainty range)."""
    if hy < 1:
        raise CalendarError("Hijri year must be positive")
    # Observed months are 29 or 30 days whatever the tabular length, so day
    # 30 is accepted for every month (it is within the declared uncertainty).
    if not 1 <= hd <= 30:
        raise CalendarError(f"{hd} is not a day of a Hijri month")
    if not 1 <= hm <= 12:
        raise CalendarError(f"Hijri month {hm} does not exist")
    jdn = (hd + -(-(59 * (hm - 1)) // 2)   # ceil(29.5 * (hm - 1))
           + (hy - 1) * 354 + (3 + 11 * hy) // 30 + _HIJRI_EPOCH_JDN - 1)
    return datetime.date.fromordinal(jdn - _JDN_OF_ORDINAL_1 + 1)


def hijri_to_gregorian_range(hy: int, hm: int, hd: int) -> Tuple[datetime.date, datetime.date]:
    centre = hijri_to_gregorian_tabular(hy, hm, hd)
    delta = datetime.timedelta(days=HIJRI_UNCERTAINTY_DAYS)
    return centre - delta, centre + delta


def hijri_month_range(hy: int, hm: int) -> Tuple[datetime.date, datetime.date]:
    first = hijri_to_gregorian_tabular(hy, hm, 1)
    last = first + datetime.timedelta(days=hijri_month_length_tabular(hy, hm) - 1)
    delta = datetime.timedelta(days=HIJRI_UNCERTAINTY_DAYS)
    return first - delta, last + delta


def jalali_month_range(jy: int, jm: int) -> Tuple[datetime.date, datetime.date]:
    return (jalali_to_gregorian(jy, jm, 1),
            jalali_to_gregorian(jy, jm, jalali_month_length(jy, jm)))
