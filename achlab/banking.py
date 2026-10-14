"""Federal Reserve banking days and ACH settlement timing.

ACH settles only on days the Federal Reserve is open: weekdays that are not one
of the eleven Fed holidays. The Fed's observance rule differs from the federal
government's: a holiday that falls on a Sunday is observed the following Monday,
but a holiday that falls on a Saturday is *not* moved — the Reserve Banks are
open on the preceding Friday.

Same Day ACH runs three settlement windows on each banking day (Eastern time):

    window   submission deadline   settlement
    SD1      10:30                 13:00
    SD2      14:45                 17:00
    SD3      16:45                 18:00

An entry is eligible for same-day settlement only when its amount is at most
$1,000,000 and it is not an IAT entry. A file whose effective entry date is
today (or already stale) and which misses the last window, or is ineligible,
settles on the next banking day. A future effective date settles on that date,
rolled forward to a banking day.
"""
from __future__ import annotations

import datetime as dt
from functools import lru_cache

SAME_DAY_LIMIT_CENTS = 1_000_000_00
WINDOWS = (
    ("SD1", dt.time(10, 30), dt.time(13, 0)),
    ("SD2", dt.time(14, 45), dt.time(17, 0)),
    ("SD3", dt.time(16, 45), dt.time(18, 0)),
)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    d = dt.date(year, month, 1)
    d += dt.timedelta(days=(weekday - d.weekday()) % 7)
    return d + dt.timedelta(weeks=n - 1)


def _last_weekday(year: int, month: int, weekday: int) -> dt.date:
    nxt = dt.date(year + (month == 12), month % 12 + 1, 1)
    d = nxt - dt.timedelta(days=1)
    return d - dt.timedelta(days=(d.weekday() - weekday) % 7)


@lru_cache(maxsize=None)
def fed_holidays(year: int) -> frozenset[dt.date]:
    """Observed Federal Reserve holidays in `year`."""
    MON, THU = 0, 3
    fixed = [
        dt.date(year, 1, 1),    # New Year's Day
        dt.date(year, 6, 19),   # Juneteenth
        dt.date(year, 7, 4),    # Independence Day
        dt.date(year, 11, 11),  # Veterans Day
        dt.date(year, 12, 25),  # Christmas
    ]
    floating = [
        _nth_weekday(year, 1, MON, 3),   # Martin Luther King Jr. Day
        _nth_weekday(year, 2, MON, 3),   # Washington's Birthday
        _last_weekday(year, 5, MON),     # Memorial Day
        _nth_weekday(year, 9, MON, 1),   # Labor Day
        _nth_weekday(year, 10, MON, 2),  # Columbus Day
        _nth_weekday(year, 11, THU, 4),  # Thanksgiving
    ]
    observed = set(floating)
    for d in fixed:
        if d.weekday() == 6:            # Sunday -> Monday
            observed.add(d + dt.timedelta(days=1))
        elif d.weekday() < 5:           # Saturday: not observed by the Fed
            observed.add(d)
    return frozenset(observed)


def is_banking_day(d: dt.date) -> bool:
    return d.weekday() < 5 and d not in fed_holidays(d.year)


def next_banking_day(d: dt.date) -> dt.date:
    """First banking day strictly after `d`."""
    d += dt.timedelta(days=1)
    while not is_banking_day(d):
        d += dt.timedelta(days=1)
    return d


def roll_forward(d: dt.date) -> dt.date:
    """`d` itself if it is a banking day, else the next one."""
    return d if is_banking_day(d) else next_banking_day(d)


def add_banking_days(d: dt.date, n: int) -> dt.date:
    for _ in range(n):
        d = next_banking_day(d)
    return d


def banking_days_between(start: dt.date, end: dt.date) -> int:
    """Number of banking days in the half-open interval (start, end]."""
    n, d = 0, start
    while d < end:
        d += dt.timedelta(days=1)
        n += is_banking_day(d)
    return n


def settlement(submitted_at: dt.datetime, effective_date: dt.date,
               amount_cents: int, *, iat: bool = False) -> tuple[dt.date, str]:
    """Settlement date and window for an entry submitted at `submitted_at` (ET).

    Returns (date, window) where window is 'SD1'/'SD2'/'SD3', 'next-day'
    (same-day intent that missed or was ineligible) or 'future-dated'.
    """
    today = submitted_at.date()
    if effective_date > today:
        return roll_forward(effective_date), "future-dated"
    eligible = amount_cents <= SAME_DAY_LIMIT_CENTS and not iat
    if eligible and is_banking_day(today):
        for name, deadline, _ in WINDOWS:
            if submitted_at.time() <= deadline:
                return today, name
    return next_banking_day(today), "next-day"
