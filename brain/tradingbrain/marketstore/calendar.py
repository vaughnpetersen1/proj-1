"""US equity trading calendar.

Gap detection needs to know which missing days are real gaps and which are
holidays. Without this, every Thanksgiving looks like a data outage and the
warnings become noise nobody reads.

Rules-based rather than a downloaded file, so it works offline and for any year.
It covers the regular NYSE/Nasdaq holiday schedule from 1998 onward, including
the Juneteenth addition (2022) and the Good Friday closure. It does NOT model
one-off closures (hurricanes, national days of mourning, 9/11) -- those are
listed explicitly below and can be extended.
"""

from __future__ import annotations

import datetime as dt
import functools

#: Unscheduled full-day closures. Extend as needed; each needs a reason.
UNSCHEDULED_CLOSURES: dict[dt.date, str] = {
    dt.date(2001, 9, 11): "9/11 attacks",
    dt.date(2001, 9, 12): "9/11 attacks",
    dt.date(2001, 9, 13): "9/11 attacks",
    dt.date(2001, 9, 14): "9/11 attacks",
    dt.date(2004, 6, 11): "Reagan national day of mourning",
    dt.date(2007, 1, 2): "Ford national day of mourning",
    dt.date(2012, 10, 29): "Hurricane Sandy",
    dt.date(2012, 10, 30): "Hurricane Sandy",
    dt.date(2018, 12, 5): "G.H.W. Bush national day of mourning",
    dt.date(2025, 1, 9): "Carter national day of mourning",
}

#: Half days (1pm ET close). Bars exist but volume is not comparable.
EARLY_CLOSE_RULES = "day after Thanksgiving; July 3 when July 4 falls Tue-Fri; Christmas Eve on a weekday"


def _easter(year: int) -> dt.date:
    """Anonymous Gregorian algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return dt.date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    """n-th (1-based) `weekday` of the month. n=-1 means the last one."""
    if n > 0:
        d = dt.date(year, month, 1)
        offset = (weekday - d.weekday()) % 7
        return d + dt.timedelta(days=offset + 7 * (n - 1))
    nxt = dt.date(year + (month == 12), (month % 12) + 1, 1)
    d = nxt - dt.timedelta(days=1)
    return d - dt.timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: dt.date) -> dt.date:
    """Saturday holidays observe Friday, Sunday holidays observe Monday."""
    if d.weekday() == 5:
        return d - dt.timedelta(days=1)
    if d.weekday() == 6:
        return d + dt.timedelta(days=1)
    return d


@functools.lru_cache(maxsize=128)
def holidays(year: int) -> frozenset[dt.date]:
    out = {
        _observed(dt.date(year, 1, 1)),                       # New Year's Day
        _nth_weekday(year, 1, 0, 3),                          # MLK (3rd Monday)
        _nth_weekday(year, 2, 0, 3),                          # Presidents (3rd Monday)
        _easter(year) - dt.timedelta(days=2),                 # Good Friday
        _nth_weekday(year, 5, 0, -1),                         # Memorial (last Monday)
        _observed(dt.date(year, 7, 4)),                       # Independence Day
        _nth_weekday(year, 9, 0, 1),                          # Labor (1st Monday)
        _nth_weekday(year, 11, 3, 4),                         # Thanksgiving (4th Thursday)
        _observed(dt.date(year, 12, 25)),                     # Christmas
    }
    if year >= 2022:
        out.add(_observed(dt.date(year, 6, 19)))              # Juneteenth
    if year >= 1998:
        out.add(_nth_weekday(year, 1, 0, 3))
    return frozenset(d for d in out if d.year == year)


def is_trading_day(d: dt.date) -> bool:
    if d.weekday() >= 5:
        return False
    if d in UNSCHEDULED_CLOSURES:
        return False
    return d not in holidays(d.year)


def trading_days(start: dt.date, end: dt.date) -> list[dt.date]:
    out: list[dt.date] = []
    d = start
    while d <= end:
        if is_trading_day(d):
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def previous_trading_day(d: dt.date) -> dt.date:
    d -= dt.timedelta(days=1)
    while not is_trading_day(d):
        d -= dt.timedelta(days=1)
    return d


def next_trading_day(d: dt.date) -> dt.date:
    d += dt.timedelta(days=1)
    while not is_trading_day(d):
        d += dt.timedelta(days=1)
    return d


def count_trading_days(start: dt.date, end: dt.date) -> int:
    return len(trading_days(start, end))


#: Regular session in US/Eastern. Used to label bars and to detect out-of-session
#: timestamps; the store keeps everything in UTC epoch seconds.
SESSION_OPEN = dt.time(9, 30)
SESSION_CLOSE = dt.time(16, 0)
PREMARKET_OPEN = dt.time(4, 0)
AFTERHOURS_CLOSE = dt.time(20, 0)
