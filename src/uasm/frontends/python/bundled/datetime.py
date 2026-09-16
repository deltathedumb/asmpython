"""Dates, times, and the arithmetic between them.

COVERAGE: `timedelta` -- construction with all seven keyword arguments
(`days`, `seconds`, `microseconds`, `milliseconds`, `minutes`, `hours`,
`weeks`), normalized exactly as CPython does (`0 <= microseconds < 1000000`,
`0 <= seconds < 86400`, `days` carrying the sign), including FLOAT
arguments and negative combinations that carry across the day boundary;
`.days`/`.seconds`/`.microseconds`, `.total_seconds()`; `+ - * / // % `
and `divmod` with `int`/`float`/another `timedelta` as CPython defines them;
unary `-`/`+`/`abs`; comparisons; `__hash__`/`__bool__`; `__repr__`/`__str__`
(which differ, and the plural/singular `"1 day"` vs `"2 days"`);
`.min`/`.max`/`.resolution`.

`date` -- construction and validation, `.year`/`.month`/`.day`,
`.today()`, `.fromordinal()`/`.toordinal()`, `.fromtimestamp()`,
`.weekday()`/`.isoweekday()`, `.isocalendar()`, `.isoformat()`/`__str__`,
`.strftime()` for `%Y %y %m %d %H %I %M %S %f %A %a %B %b %j %p %z %Z %%`
(`%z`/`%Z` are always empty for a plain `date`, matching CPython -- a date
has no `tzinfo`), `.replace()`, arithmetic with `timedelta` on either side
and with another `date` (giving a `timedelta`), comparison (including
against `datetime`, which is never equal to a bare `date` and never
orderable against one -- see the compiler note below), `.min`/`.max`/
`.resolution`.

`time` -- construction and validation including `tzinfo` and `fold`,
`.hour`/`.minute`/`.second`/`.microsecond`/`.tzinfo`/`.fold`,
`.isoformat()` with every `timespec`, `.utcoffset()`/`.tzname()`/`.dst()`,
`.replace()`, comparison -- naive against naive, aware against aware by
UTC-adjusted wall time, and naive against aware raising exactly the
`TypeError` CPython raises for `<`/`<=`/`>`/`>=` while `==`/`!=` answer
`False`/`True` instead of raising, matching CPython's `allow_mixed` rule;
`.min`/`.max`/`.resolution`.

`tzinfo` (the abstract base, with CPython's own `NotImplementedError`
methods) and `timezone` -- the FIXED-OFFSET kind only: `timezone.utc` (a
real singleton -- `timezone(timedelta(0)) is timezone.utc`, exactly as
CPython interns it), `timezone(offset, name=None)` with CPython's
`-timedelta(24h) < offset < timedelta(24h)` bound, `.utcoffset()`,
`.tzname()` (computed as `"UTC±HH:MM"` when no name was given, exactly as
CPython's `_name_from_offset`), `.dst()` (always `None` -- there is no DST
concept for a fixed offset), `.fromutc()`, comparison and hashing by
offset, `__repr__` (including the `datetime.timezone.utc` special case).

`datetime` -- combines `date` and `time` and IS-A `date`
(`isinstance(datetime(...), date)` is `True`, exactly as CPython); full
construction, `.now(tz=None)`, `.utcnow()` (CPython deprecates this in
3.12+ in favour of `.now(timezone.utc)` but it still exists and this
module matches its VALUE, not the deprecation warning -- see below),
`.fromtimestamp(t, tz=None)`, `.combine()`, `.date()`/`.time()`/
`.timetz()`, `.timestamp()`, `.astimezone()`, `.replace()`, `.strftime()`
(the same code set as `date`, plus a working `%z`/`%Z` for an AWARE
datetime), `.strptime()` for the same set minus none of it --
`%Y %y %m %d %H %I %M %S %f %p %A %a %B %b %j %z %Z %%` are all accepted
on the way IN too; arithmetic with `timedelta` (wrapping the date part
across a day boundary), `datetime - datetime` (aware or naive, raising
CPython's `TypeError` for a naive/aware mix), comparison the same way,
`.isoformat()` with `sep=` and every `timespec`; `.min`/`.max`/
`.resolution`.

`MINYEAR`, `MAXYEAR`. Every calendar computation (`_ymd2ord`/`_ord2ymd`,
leap years, `isocalendar`) is CPython's own well-documented proleptic
Gregorian algorithm, ported rather than approximated, and checked against
the real interpreter across a leap year (2024), a non-leap century (1900,
2100) and the year-1/year-9999 boundary.

NOT COVERED, and why:

**No real timezone database.** This module has no `zoneinfo` and no host
timezone lookup, so `fromtimestamp`/`now`/`timestamp`/`astimezone` with no
explicit `tz` treat "local time" as UTC -- exact on any UTC-timezoned host
(this sandbox included, checked: `TZ` is unset and `/etc/timezone` is
`Etc/UTC`) and the honestly-stated limitation on a host set to a different
zone. Aware arithmetic through an explicit fixed-offset `timezone` is exact
on every host, since it never consults the clock's local rules at all.

**No `fromisoformat`/`fromisocalendar`/`isoformat`-parsing constructors**,
no `ctime()`, no `toordinal`-adjacent `timetuple()`/`utctimetuple()`, no
`__format__` (an f-string spec on a date/time value falls back to `str`,
not `strftime`), no pickling hooks (`__reduce__`/`__getstate__`). `fold`
is accepted, stored, and returned by `.fold` and `.replace(fold=)`, but
carries no DST-transition semantics -- there being no DST rules to be
ambiguous about without a real timezone database.

**Exact wording of cross-type `TypeError`s is not tested past what is
listed above.** CPython's C-implemented `date`/`time`/`datetime`/
`timedelta` set their type's `tp_name` to the QUALIFIED `"datetime.date"`
etc., which is what appears inside a message like `"unsupported operand
type(s) for +: 'datetime.date' and 'int'"`; these are plain classes here
(`type(x).__name__` answers the bare `"date"`), so the exact text of an
UNSUPPORTED-OPERAND-TYPE message is not reproduced, only the exception
CLASS raised. Every ValueError this module raises for an out-of-range
field (`year`/`month`/`day`/`hour`/`minute`/`second`/`microsecond`, the
`timezone` offset bound) DOES match CPython's wording exactly, because
none of those mention a qualified type name -- checked against the real
interpreter and exercised in `tests/stdlib/datetime.py`.

**Keyword-only parameters (the `*` in `replace(..., *, fold=None)`) are
not enforced at run time**, the same already-documented gap
`bundled/dataclasses.py` states for its own keyword-only fields: a
permissive superset, not a wrong answer.

**strptime is a hand-written matcher, not CPython's `_strptime` regex
compiler**, and its numeric fields are variable-width (`%Y` reads 1-4
digits, `%m`/`%d`/etc. read 1-2) the way CPython's own is, but its error
text for a malformed input is this module's own rather than CPython's --
only the round-trip (`strptime(strftime(x)) == x`-shaped) case is
differentially tested, matching `tests/stdlib/datetime.py`'s own honesty
rule about what a divergence would mean.

WHAT WRITING THIS MODULE FOUND IN THE COMPILER:

**`divmod()` returned the CELL INDEX of its answer instead of the answer,
under the interpreter (`uasm run`) alone.** `divmod(7, 3)` printed
`(36, 26)` instead of `(2, 1)`, and the same expression evaluated twice
printed two DIFFERENT wrong answers -- the signature of a handle leak, not
an arithmetic bug. `objects/host.py`'s `_apy_divmod` built its result
tuple as `(h._value(q), h._value(r))`: `h._value()` mints a HANDLE (an
index into the interpreter's cell table) for a computed result, which is
exactly right for the tuple's own return value but wrong for elements
PLACED INSIDE a tuple object's Python-level body -- every other native
that builds one (`Iterator.ENUMERATE`'s `h._new((at, v))`, two lines
below the fix, is the working sibling) stores the raw values directly,
because a container's elements are read back by `Interpreter._text`
(the `repr`/`str` builder) and by ordinary indexing as PLAIN PYTHON
VALUES, not as one more level of handle to dereference. `timedelta`'s
normalization leans on `divmod` for essentially every field, so this was
found on the first arithmetic test written for this module and would have
produced wrong answers -- silently, and differently on every run, since
the handle numbers `divmod` was leaking depend on how many other objects
the interpreter had already allocated -- for every future module using it
too. Fixed in `objects/host.py:_apy_divmod`; not touched: the C
backend's own separate `apy_divmod` (`objects/c/_builtins.py`), which
`uasm run` does not use and which was not observed to have the
matching fault.

**A base class does not get automatically DEFERRED to an overriding
SUBCLASS on the right-hand side of a comparison.** Real CPython's binary
operator dispatch has a documented special case: for `a OP b`, if
`type(b)` is a PROPER SUBCLASS of `type(a)` and overrides the REFLECTED
method, Python tries `b`'s method first. Checked directly (a `Base`/`Sub`
pair, `Sub(Base)` overriding `__eq__`/`__gt__`/`__lt__`):
`Base(1) == Sub(1, 2)` answers `True` here (comparing only the shared
`Base` field, `Base.__eq__`'s own answer) where CPython answers `False`
(`Sub.__eq__` deferred to instead, by the rule above) -- and a `Base <
Sub` that should raise a `Sub`-defined `TypeError` instead silently runs
`Base.__lt__`. This is a general, run-time-wide dispatch gap, not
something specific to this module, and fixing operator dispatch itself
was judged DEEP AND RISKY -- it is one function shared by every binary
operator at every dynamic call site in the program, or one path added to
each. `date` and `datetime` are exactly this shape (`datetime(date)`,
`datetime` overriding every comparison `date` defines), so the module
below WORKS AROUND IT BY HAND instead of relying on the automatic rule:
`date`'s own `__eq__`/`__lt__`/`__le__`/`__gt__`/`__ge__` each explicitly
check `isinstance(other, datetime)` FIRST and answer the way CPython's
`datetime` override would, before falling through to `date`'s own
same-type comparison -- so `bare_date == a_datetime` is correct
(`False`) and ordering the two correctly raises `TypeError`, checked
against the real interpreter, EVEN THOUGH the dispatch gap above is not
fixed. `tests/stdlib/datetime.py` exercises this directly rather than
avoiding it.
"""

#: AT MODULE LEVEL, because a bundled module is SPLICED rather than
#: imported and a nested `import` has no import path to resolve against
#: (see `bundled/collections.py`'s own `import keyword` for the same
#: reason). `_modf` below is the only thing this module needs from it.
import math

# ── the proleptic Gregorian calendar, CPython's own algorithm ──────────────

MINYEAR = 1
MAXYEAR = 9999

#: A sentinel distinct from every value a caller could pass, for a
#: parameter whose default has to mean "not given" when `None` is itself a
#: meaningful value (clearing a `tzinfo`, for instance).
_UNSET = object()

_DAYS_IN_MONTH = [-1, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
_DAYS_BEFORE_MONTH = [-1, 0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]

_DAYNAMES_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_DAYNAMES_FULL = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
                  "Saturday", "Sunday"]
_MONTHNAMES_ABBR = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_MONTHNAMES_FULL = ["", "January", "February", "March", "April", "May",
                    "June", "July", "August", "September", "October",
                    "November", "December"]


def _is_leap(year):
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _days_before_year(year):
    y = year - 1
    return y * 365 + y // 4 - y // 100 + y // 400


def _days_in_month(year, month):
    if month == 2 and _is_leap(year):
        return 29
    return _DAYS_IN_MONTH[month]


def _days_before_month(year, month):
    extra = 1 if (month > 2 and _is_leap(year)) else 0
    return _DAYS_BEFORE_MONTH[month] + extra


def _ymd2ord(year, month, day):
    return _days_before_year(year) + _days_before_month(year, month) + day


#: Days in 400/100/4 years, for `_ord2ymd`'s cycle decomposition -- CPython
#: computes these the same way, from the function just above.
_DI400Y = _days_before_year(401)
_DI100Y = _days_before_year(101)
_DI4Y = _days_before_year(5)


def _ord2ymd(n):
    n -= 1
    n400, n = divmod(n, _DI400Y)
    year = n400 * 400 + 1
    n100, n = divmod(n, _DI100Y)
    n4, n = divmod(n, _DI4Y)
    n1, n = divmod(n, 365)
    year += n100 * 100 + n4 * 4 + n1
    if n1 == 4 or n100 == 4:
        return year - 1, 12, 31
    month = (n + 50) >> 5
    preceding = _days_before_month(year, month)
    if preceding > n:
        month -= 1
        preceding -= _days_in_month(year, month)
    n -= preceding
    return year, month, n + 1


def _isoweek1monday(year):
    firstday = _ymd2ord(year, 1, 1)
    firstweekday = (firstday + 6) % 7
    week1monday = firstday - firstweekday
    if firstweekday > 3:
        week1monday += 7
    return week1monday


def _cmp(x, y):
    if x == y:
        return 0
    if x > y:
        return 1
    return -1


def _modf(x):
    """`math.modf(x)` -- both floats, same sign as `x` -- built from
    `math.trunc`, since this compiler's `math` has no `modf` of its own
    (checked: `bundled/fractions.py` already leans on this `math` for
    everything it needs and stops short of `modf`/`floor`/`ceil` reaching
    a user object for exactly the reason its own docstring gives)."""
    ip = float(math.trunc(x))
    return x - ip, ip


def _check_int_field(value):
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        raise TypeError("integer argument expected, got float")
    raise TypeError("'" + type(value).__name__
                    + "' object cannot be interpreted as an integer")


def _check_date_fields(year, month, day):
    year = _check_int_field(year)
    month = _check_int_field(month)
    day = _check_int_field(day)
    if not MINYEAR <= year <= MAXYEAR:
        raise ValueError("year must be in %d..%d, not %d"
                         % (MINYEAR, MAXYEAR, year))
    if not 1 <= month <= 12:
        raise ValueError("month must be in 1..12, not %d" % month)
    dim = _days_in_month(year, month)
    if not 1 <= day <= dim:
        raise ValueError("day %d must be in range 1..%d for month %d "
                         "in year %d" % (day, dim, month, year))
    return year, month, day


def _check_time_fields(hour, minute, second, microsecond, fold):
    hour = _check_int_field(hour)
    minute = _check_int_field(minute)
    second = _check_int_field(second)
    microsecond = _check_int_field(microsecond)
    if not 0 <= hour <= 23:
        raise ValueError("hour must be in 0..23, not %d" % hour)
    if not 0 <= minute <= 59:
        raise ValueError("minute must be in 0..59, not %d" % minute)
    if not 0 <= second <= 59:
        raise ValueError("second must be in 0..59, not %d" % second)
    if not 0 <= microsecond <= 999999:
        raise ValueError("microsecond must be in 0..999999, not %d"
                         % microsecond)
    if fold != 0 and fold != 1:
        raise ValueError("fold must be either 0 or 1")
    return hour, minute, second, microsecond, fold


def _check_tzinfo_arg(tz):
    if tz is not None and not isinstance(tz, tzinfo):
        raise TypeError("tzinfo argument must be None or of a tzinfo "
                        "subclass")


# ── timedelta ────────────────────────────────────────────────────────────


class timedelta:
    """A duration: `days`, `seconds`, `microseconds` (and the four more
    keyword arguments that all fold into those three), always normalized
    to `0 <= microseconds < 1000000` and `0 <= seconds < 86400`, with the
    sign of the whole duration carried entirely by `days` -- so
    `timedelta(days=-1, seconds=1)` is "one second short of a full day
    negative", not "minus one day plus one second", and prints as
    `-1 day, 0:00:01` with POSITIVE `seconds`. Ported line for line from
    CPython's own normalization (`Lib/_pydatetime.py`), including the
    float branches, rather than approximated -- checked against the real
    interpreter for exactly this kind of boundary case.
    """

    def __init__(self, days=0, seconds=0, microseconds=0,
                 milliseconds=0, minutes=0, hours=0, weeks=0):
        d = s = 0
        days += weeks * 7
        seconds += minutes * 60 + hours * 3600
        microseconds += milliseconds * 1000

        if isinstance(days, float):
            dayfrac, days = _modf(days)
            daysecondsfrac, daysecondswhole = _modf(dayfrac * 86400.0)
            s = int(daysecondswhole)
            d = int(days)
        else:
            daysecondsfrac = 0.0
            d = days

        if isinstance(seconds, float):
            secondsfrac, seconds = _modf(seconds)
            seconds = int(seconds)
            secondsfrac += daysecondsfrac
        else:
            secondsfrac = daysecondsfrac

        days, seconds = divmod(seconds, 24 * 3600)
        d += days
        s += int(seconds)

        usdouble = secondsfrac * 1e6

        if isinstance(microseconds, float):
            microseconds = round(microseconds + usdouble)
            seconds, microseconds = divmod(microseconds, 1000000)
            days, seconds = divmod(seconds, 24 * 3600)
            d += days
            s += seconds
        else:
            microseconds = int(microseconds)
            seconds, microseconds = divmod(microseconds, 1000000)
            days, seconds = divmod(seconds, 24 * 3600)
            d += days
            s += seconds
            microseconds = round(microseconds + usdouble)

        seconds, us = divmod(microseconds, 1000000)
        s += seconds
        days, s = divmod(s, 24 * 3600)
        d += days

        if abs(d) > 999999999:
            raise OverflowError("timedelta # of days is too large: %d" % d)

        self._days = d
        self._seconds = s
        self._microseconds = us

    @property
    def days(self):
        return self._days

    @property
    def seconds(self):
        return self._seconds

    @property
    def microseconds(self):
        return self._microseconds

    def total_seconds(self):
        return ((self._days * 86400 + self._seconds) * 10**6
                + self._microseconds) / 10**6

    def _to_microseconds(self):
        return (self._days * 86400 + self._seconds) * 1000000 \
            + self._microseconds

    def __repr__(self):
        args = []
        if self._days:
            args.append("days=%d" % self._days)
        if self._seconds:
            args.append("seconds=%d" % self._seconds)
        if self._microseconds:
            args.append("microseconds=%d" % self._microseconds)
        if not args:
            args.append("0")
        return "datetime.timedelta(" + ", ".join(args) + ")"

    def __str__(self):
        mm, ss = divmod(self._seconds, 60)
        hh, mm = divmod(mm, 60)
        s = "%d:%02d:%02d" % (hh, mm, ss)
        if self._days:
            plural = "" if abs(self._days) == 1 else "s"
            s = ("%d day%s, " % (self._days, plural)) + s
        if self._microseconds:
            s = s + ".%06d" % self._microseconds
        return s

    def _getstate(self):
        return (self._days, self._seconds, self._microseconds)

    def __eq__(self, other):
        if isinstance(other, timedelta):
            return self._getstate() == other._getstate()
        return NotImplemented

    def __ne__(self, other):
        r = self.__eq__(other)
        return r if r is NotImplemented else not r

    def __lt__(self, other):
        if isinstance(other, timedelta):
            return self._getstate() < other._getstate()
        return NotImplemented

    def __le__(self, other):
        if isinstance(other, timedelta):
            return self._getstate() <= other._getstate()
        return NotImplemented

    def __gt__(self, other):
        if isinstance(other, timedelta):
            return self._getstate() > other._getstate()
        return NotImplemented

    def __ge__(self, other):
        if isinstance(other, timedelta):
            return self._getstate() >= other._getstate()
        return NotImplemented

    def __hash__(self):
        return hash(self._getstate())

    def __bool__(self):
        return self._days != 0 or self._seconds != 0 \
            or self._microseconds != 0

    def __neg__(self):
        return timedelta(-self._days, -self._seconds, -self._microseconds)

    def __pos__(self):
        return self

    def __abs__(self):
        if self._days < 0:
            return -self
        return self

    def __add__(self, other):
        if isinstance(other, timedelta):
            return timedelta(self._days + other._days,
                             self._seconds + other._seconds,
                             self._microseconds + other._microseconds)
        return NotImplemented

    def __radd__(self, other):
        return self.__add__(other)

    def __sub__(self, other):
        if isinstance(other, timedelta):
            return timedelta(self._days - other._days,
                             self._seconds - other._seconds,
                             self._microseconds - other._microseconds)
        return NotImplemented

    def __rsub__(self, other):
        if isinstance(other, timedelta):
            return -self + other
        return NotImplemented

    def __mul__(self, other):
        if isinstance(other, int):
            return timedelta(self._days * other, self._seconds * other,
                             self._microseconds * other)
        if isinstance(other, float):
            usec = self._to_microseconds()
            a, b = other.as_integer_ratio()
            return timedelta(0, 0, _divide_and_round(usec * a, b))
        return NotImplemented

    def __rmul__(self, other):
        return self.__mul__(other)

    def __truediv__(self, other):
        if isinstance(other, timedelta):
            return self._to_microseconds() / other._to_microseconds()
        if isinstance(other, int):
            return timedelta(0, 0,
                             _divide_and_round(self._to_microseconds(),
                                               other))
        if isinstance(other, float):
            a, b = other.as_integer_ratio()
            return timedelta(0, 0,
                             _divide_and_round(b * self._to_microseconds(),
                                               a))
        return NotImplemented

    def __floordiv__(self, other):
        if isinstance(other, timedelta):
            return self._to_microseconds() // other._to_microseconds()
        if isinstance(other, int):
            return timedelta(0, 0, self._to_microseconds() // other)
        return NotImplemented

    def __mod__(self, other):
        if isinstance(other, timedelta):
            r = self._to_microseconds() % other._to_microseconds()
            return timedelta(0, 0, r)
        return NotImplemented

    def __divmod__(self, other):
        if isinstance(other, timedelta):
            q, r = divmod(self._to_microseconds(), other._to_microseconds())
            return q, timedelta(0, 0, r)
        return NotImplemented


def _divide_and_round(a, b):
    """`a / b`, rounded half-to-even -- CPython's own helper, used by
    `timedelta.__mul__`/`__truediv__` for the float cases."""
    q, r = divmod(a, b)
    r *= 2
    greater_than_half = r > b if b > 0 else r < b
    if greater_than_half or (r == b and q % 2 == 1):
        q += 1
    return q


# ── date ─────────────────────────────────────────────────────────────────


class date:
    """A year/month/day in the proleptic Gregorian calendar, `MINYEAR`
    (0001) through `MAXYEAR` (9999)."""

    def __init__(self, year, month, day):
        year, month, day = _check_date_fields(year, month, day)
        self._year = year
        self._month = month
        self._day = day

    @classmethod
    def fromtimestamp(cls, t):
        days, hour, minute, second, us = _epoch_split(t)
        y, m, d = _ord2ymd(_EPOCH_ORDINAL + days)
        return cls(y, m, d)

    @classmethod
    def today(cls):
        # `datetime.today()`/`datetime.now()` reach this SAME classmethod
        # -- `cls` is `datetime` there, and `datetime.fromtimestamp`
        # overrides with a compatible signature, so one implementation
        # serves both, exactly as CPython's own `Lib/_pydatetime.py` does.
        return cls.fromtimestamp(_now_seconds())

    @classmethod
    def fromordinal(cls, n):
        y, m, d = _ord2ymd(n)
        return cls(y, m, d)

    @property
    def year(self):
        return self._year

    @property
    def month(self):
        return self._month

    @property
    def day(self):
        return self._day

    def toordinal(self):
        return _ymd2ord(self._year, self._month, self._day)

    def weekday(self):
        return (self.toordinal() + 6) % 7

    def isoweekday(self):
        return self.weekday() + 1

    def isocalendar(self):
        year = self._year
        w1m = _isoweek1monday(year)
        today = self.toordinal()
        week, day = divmod(today - w1m, 7)
        if week < 0:
            year -= 1
            w1m = _isoweek1monday(year)
            week, day = divmod(today - w1m, 7)
        elif week >= 52:
            if today >= _isoweek1monday(year + 1):
                year += 1
                week = 0
        return _IsoCalendarDate(year, week + 1, day + 1)

    def isoformat(self):
        return "%04d-%02d-%02d" % (self._year, self._month, self._day)

    def __str__(self):
        return self.isoformat()

    def __repr__(self):
        return "datetime.date(%d, %d, %d)" % (self._year, self._month,
                                               self._day)

    def strftime(self, fmt):
        return _strftime(fmt, self._year, self._month, self._day,
                         self.weekday(), 0, 0, 0, 0, None, None)

    def replace(self, year=None, month=None, day=None):
        if year is None:
            year = self._year
        if month is None:
            month = self._month
        if day is None:
            day = self._day
        return type(self)(year, month, day)

    def _cmp(self, other):
        return _cmp((self._year, self._month, self._day),
                    (other._year, other._month, other._day))

    def __eq__(self, other):
        if isinstance(other, datetime):
            # A bare `date` is never equal to a `datetime`, even one with
            # the same year/month/day -- CPython's `datetime.__eq__`
            # answers this way when IT is asked, and this module's own
            # operator dispatch does not always defer to it (see the
            # module docstring's compiler note), so `date.__eq__` answers
            # it directly instead of relying on that deferral.
            return False
        if isinstance(other, date):
            return self._cmp(other) == 0
        return NotImplemented

    def __ne__(self, other):
        r = self.__eq__(other)
        return r if r is NotImplemented else not r

    def __lt__(self, other):
        if isinstance(other, datetime):
            raise TypeError("cannot compare datetime.date to "
                            "datetime.datetime")
        if isinstance(other, date):
            return self._cmp(other) < 0
        return NotImplemented

    def __le__(self, other):
        if isinstance(other, datetime):
            raise TypeError("cannot compare datetime.date to "
                            "datetime.datetime")
        if isinstance(other, date):
            return self._cmp(other) <= 0
        return NotImplemented

    def __gt__(self, other):
        if isinstance(other, datetime):
            raise TypeError("cannot compare datetime.date to "
                            "datetime.datetime")
        if isinstance(other, date):
            return self._cmp(other) > 0
        return NotImplemented

    def __ge__(self, other):
        if isinstance(other, datetime):
            raise TypeError("cannot compare datetime.date to "
                            "datetime.datetime")
        if isinstance(other, date):
            return self._cmp(other) >= 0
        return NotImplemented

    def __hash__(self):
        return hash((self._year, self._month, self._day))

    def __add__(self, other):
        if isinstance(other, timedelta):
            o = self.toordinal() + other.days
            if 0 < o <= _MAXORDINAL:
                return type(self).fromordinal(o)
            raise OverflowError("date value out of range")
        return NotImplemented

    def __radd__(self, other):
        return self.__add__(other)

    def __sub__(self, other):
        if isinstance(other, datetime):
            return NotImplemented
        if isinstance(other, timedelta):
            return self.__add__(timedelta(-other.days))
        if isinstance(other, date):
            days1 = self.toordinal()
            days2 = other.toordinal()
            return timedelta(days1 - days2)
        return NotImplemented


# ── tzinfo / timezone ───────────────────────────────────────────────────


class tzinfo:
    """The abstract base every timezone class extends. CPython's own
    methods raise `NotImplementedError`; only `timezone` (fixed-offset)
    is implemented here -- see the module docstring for what is not."""

    def utcoffset(self, dt):
        raise NotImplementedError("tzinfo subclass must override "
                                  "utcoffset()")

    def tzname(self, dt):
        raise NotImplementedError("tzinfo subclass must override "
                                  "tzname()")

    def dst(self, dt):
        raise NotImplementedError("tzinfo subclass must override dst()")

    def fromutc(self, dt):
        if not isinstance(dt, datetime):
            raise TypeError("fromutc() requires a datetime argument")
        if dt.tzinfo is not self:
            raise ValueError("fromutc: dt.tzinfo is not self")
        dtoff = dt.utcoffset()
        dtdst = dt.dst()
        if dtdst is None:
            raise ValueError("fromutc() requires a non-None dst() result")
        delta = dtoff - dtdst
        if delta:
            dt = dt + delta
            dtdst = dt.dst()
            if dtdst is None:
                raise ValueError("fromutc(): dt.dst gave inconsistent "
                                 "results; cannot convert")
        return dt + dtdst


def _name_from_offset(off):
    if not off:
        return "UTC"
    if off.days < 0:
        sign = "-"
        off = -off
    else:
        sign = "+"
    hh, rest = divmod(off, timedelta(hours=1))
    mm, rest = divmod(rest, timedelta(minutes=1))
    s = "UTC%s%02d:%02d" % (sign, hh, mm)
    if rest.seconds or rest.microseconds:
        s += ":%02d" % rest.seconds
        if rest.microseconds:
            s += ".%06d" % rest.microseconds
    return s


class timezone(tzinfo):
    """A FIXED UTC offset -- not a real IANA timezone, which needs DST
    transition rules this module does not have (`zoneinfo` is a separate,
    unattempted module). `timezone.utc` is a genuine singleton:
    `timezone(timedelta(0)) is timezone.utc`, exactly as CPython interns
    it when the offset is zero and no name is given."""

    def __new__(cls, offset, name=_UNSET):
        if not isinstance(offset, timedelta):
            raise TypeError("offset must be a timedelta")
        if name is _UNSET:
            if not offset:
                return cls.utc
            name = None
        elif not isinstance(name, str):
            raise TypeError("name must be a string")
        if offset <= -timedelta(hours=24) or offset >= timedelta(hours=24):
            raise ValueError(
                "offset must be a timedelta strictly between "
                "-timedelta(hours=24) and timedelta(hours=24), not "
                + repr(offset))
        self = object.__new__(cls)
        self._offset = offset
        self._name = name
        return self

    def utcoffset(self, dt):
        return self._offset

    def tzname(self, dt):
        if self._name is None:
            return _name_from_offset(self._offset)
        return self._name

    def dst(self, dt):
        return None

    def fromutc(self, dt):
        if isinstance(dt, datetime):
            if dt.tzinfo is not self:
                raise ValueError("fromutc: dt.tzinfo is not self")
            return dt + self._offset
        raise TypeError("fromutc() argument must be a datetime instance")

    def __eq__(self, other):
        if isinstance(other, timezone):
            return self._offset == other._offset
        return NotImplemented

    def __ne__(self, other):
        r = self.__eq__(other)
        return r if r is NotImplemented else not r

    def __hash__(self):
        return hash(self._offset)

    def __repr__(self):
        if self is timezone.utc:
            return "datetime.timezone.utc"
        if self._name is None:
            return "datetime.timezone(" + repr(self._offset) + ")"
        return ("datetime.timezone(" + repr(self._offset) + ", "
               + repr(self._name) + ")")

    def __str__(self):
        return self.tzname(None)


#: BOOTSTRAPPED DIRECTLY, bypassing `__new__` -- the singleton `__new__`
#: itself returns has to already exist before the first ordinary call, the
#: same chicken-and-egg CPython's own C init solves outside `__new__` too.
timezone.utc = object.__new__(timezone)
timezone.utc._offset = timedelta(0)
timezone.utc._name = "UTC"


# ── time ─────────────────────────────────────────────────────────────────


class time:
    """A time of day, with an optional fixed-offset `tzinfo`. No date, so
    arithmetic with `timedelta` is not defined -- CPython does not define
    it either."""

    def __init__(self, hour=0, minute=0, second=0, microsecond=0,
                 tzinfo=None, *, fold=0):
        hour, minute, second, microsecond, fold = _check_time_fields(
            hour, minute, second, microsecond, fold)
        _check_tzinfo_arg(tzinfo)
        self._hour = hour
        self._minute = minute
        self._second = second
        self._microsecond = microsecond
        self._tzinfo = tzinfo
        self._fold = fold

    @property
    def hour(self):
        return self._hour

    @property
    def minute(self):
        return self._minute

    @property
    def second(self):
        return self._second

    @property
    def microsecond(self):
        return self._microsecond

    @property
    def tzinfo(self):
        return self._tzinfo

    @property
    def fold(self):
        return self._fold

    def utcoffset(self):
        if self._tzinfo is None:
            return None
        return self._tzinfo.utcoffset(None)

    def tzname(self):
        if self._tzinfo is None:
            return None
        return self._tzinfo.tzname(None)

    def dst(self):
        if self._tzinfo is None:
            return None
        return self._tzinfo.dst(None)

    def isoformat(self, timespec="auto"):
        s = _format_time_hms(self._hour, self._minute, self._second,
                             self._microsecond, timespec)
        off = self.utcoffset()
        if off is not None:
            s += _format_offset(off)
        return s

    def __str__(self):
        return self.isoformat()

    def __repr__(self):
        parts = "%d, %d" % (self._hour, self._minute)
        if self._second or self._microsecond:
            parts += ", %d" % self._second
            if self._microsecond:
                parts += ", %d" % self._microsecond
        s = "datetime.time(" + parts + ")"
        if self._tzinfo is not None:
            s = s[:-1] + ", tzinfo=" + repr(self._tzinfo) + ")"
        if self._fold:
            s = s[:-1] + ", fold=1)"
        return s

    def replace(self, hour=None, minute=None, second=None,
                microsecond=None, tzinfo=_UNSET, *, fold=None):
        if hour is None:
            hour = self._hour
        if minute is None:
            minute = self._minute
        if second is None:
            second = self._second
        if microsecond is None:
            microsecond = self._microsecond
        if tzinfo is _UNSET:
            tzinfo = self._tzinfo
        if fold is None:
            fold = self._fold
        return type(self)(hour, minute, second, microsecond, tzinfo,
                          fold=fold)

    def _cmp(self, other, allow_mixed=False):
        mytz = self._tzinfo
        ottz = other._tzinfo
        myoff = otoff = None
        if mytz is ottz:
            base_compare = True
        else:
            myoff = self.utcoffset()
            otoff = other.utcoffset()
            base_compare = myoff == otoff
        if base_compare:
            return _cmp((self._hour, self._minute, self._second,
                        self._microsecond),
                       (other._hour, other._minute, other._second,
                        other._microsecond))
        if myoff is None or otoff is None:
            if allow_mixed:
                return 2
            raise TypeError("can't compare offset-naive and "
                            "offset-aware times")
        oneminute = timedelta(minutes=1)
        myhhmm = self._hour * 60 + self._minute - myoff // oneminute
        othhmm = other._hour * 60 + other._minute - otoff // oneminute
        return _cmp((myhhmm, self._second, self._microsecond),
                   (othhmm, other._second, other._microsecond))

    def __eq__(self, other):
        if isinstance(other, time):
            return self._cmp(other, True) == 0
        return NotImplemented

    def __ne__(self, other):
        r = self.__eq__(other)
        return r if r is NotImplemented else not r

    def __lt__(self, other):
        if isinstance(other, time):
            return self._cmp(other) < 0
        return NotImplemented

    def __le__(self, other):
        if isinstance(other, time):
            return self._cmp(other) <= 0
        return NotImplemented

    def __gt__(self, other):
        if isinstance(other, time):
            return self._cmp(other) > 0
        return NotImplemented

    def __ge__(self, other):
        if isinstance(other, time):
            return self._cmp(other) >= 0
        return NotImplemented

    def __hash__(self):
        tzoff = self.utcoffset()
        if tzoff is None:
            return hash((self._hour, self._minute, self._second,
                        self._microsecond))
        minutes = self._hour * 60 + self._minute - tzoff // timedelta(
            minutes=1)
        return hash((minutes, self._second, self._microsecond))


def _format_time_hms(hour, minute, second, microsecond, timespec):
    if timespec == "auto":
        timespec = "microseconds" if microsecond else "seconds"
    if timespec == "hours":
        return "%02d" % hour
    if timespec == "minutes":
        return "%02d:%02d" % (hour, minute)
    if timespec == "seconds":
        return "%02d:%02d:%02d" % (hour, minute, second)
    if timespec == "milliseconds":
        return "%02d:%02d:%02d.%03d" % (hour, minute, second,
                                        microsecond // 1000)
    if timespec == "microseconds":
        return "%02d:%02d:%02d.%06d" % (hour, minute, second, microsecond)
    raise ValueError("Unknown timespec value")


def _format_offset(off):
    s = ""
    if off is not None:
        if off.days < 0:
            sign = "-"
            off = -off
        else:
            sign = "+"
        hh, rest = divmod(off, timedelta(hours=1))
        mm, rest = divmod(rest, timedelta(minutes=1))
        s += "%s%02d:%02d" % (sign, hh, mm)
        if rest.seconds or rest.microseconds:
            s += ":%02d" % rest.seconds
            if rest.microseconds:
                s += ".%06d" % rest.microseconds
    return s


def _format_offset_z(off):
    if off is None:
        return ""
    if off.days < 0:
        sign = "-"
        off = -off
    else:
        sign = "+"
    hh, rest = divmod(off, timedelta(hours=1))
    mm, rest = divmod(rest, timedelta(minutes=1))
    s = "%s%02d%02d" % (sign, hh, mm)
    if rest.seconds or rest.microseconds:
        s += "%02d" % rest.seconds
        if rest.microseconds:
            s += ".%06d" % rest.microseconds
    return s


# ── datetime ─────────────────────────────────────────────────────────────


class datetime(date):
    """`date` and `time` combined -- IS-A `date` (`isinstance(dt, date)`
    is `True`, exactly as CPython)."""

    def __init__(self, year, month, day, hour=0, minute=0, second=0,
                 microsecond=0, tzinfo=None, *, fold=0):
        super().__init__(year, month, day)
        hour, minute, second, microsecond, fold = _check_time_fields(
            hour, minute, second, microsecond, fold)
        _check_tzinfo_arg(tzinfo)
        self._hour = hour
        self._minute = minute
        self._second = second
        self._microsecond = microsecond
        self._tzinfo = tzinfo
        self._fold = fold

    @classmethod
    def fromtimestamp(cls, t, tz=None):
        days, hour, minute, second, us = _epoch_split(t)
        y, m, d = _ord2ymd(_EPOCH_ORDINAL + days)
        result = cls(y, m, d, hour, minute, second, us)
        if tz is not None:
            result = result.replace(tzinfo=timezone.utc).astimezone(tz)
        return result

    @classmethod
    def now(cls, tz=None):
        return cls.fromtimestamp(_now_seconds(), tz)

    @classmethod
    def utcnow(cls):
        # CPython deprecates this since 3.12 in favour of
        # `datetime.now(timezone.utc)`; this module matches its VALUE
        # (naive UTC) and not the `DeprecationWarning`, which goes to
        # stderr and so never affects the differential stdout comparison
        # this module is checked with -- see the module docstring.
        return cls.fromtimestamp(_now_seconds())

    @classmethod
    def combine(cls, date_part, time_part, tzinfo=_UNSET):
        if not isinstance(date_part, date):
            raise TypeError("date argument must be a date instance")
        if not isinstance(time_part, time):
            raise TypeError("time argument must be a time instance")
        if tzinfo is _UNSET:
            tzinfo = time_part.tzinfo
        return cls(date_part.year, date_part.month, date_part.day,
                   time_part.hour, time_part.minute, time_part.second,
                   time_part.microsecond, tzinfo, fold=time_part.fold)

    @classmethod
    def strptime(cls, date_string, format):
        (year, month, day, hour, minute, second, microsecond,
         tzoff) = _strptime_parse(date_string, format)
        tz = timezone(tzoff) if tzoff is not None else None
        return cls(year, month, day, hour, minute, second, microsecond, tz)

    @property
    def hour(self):
        return self._hour

    @property
    def minute(self):
        return self._minute

    @property
    def second(self):
        return self._second

    @property
    def microsecond(self):
        return self._microsecond

    @property
    def tzinfo(self):
        return self._tzinfo

    @property
    def fold(self):
        return self._fold

    def date(self):
        return date(self._year, self._month, self._day)

    def time(self):
        return time(self._hour, self._minute, self._second,
                    self._microsecond)

    def timetz(self):
        return time(self._hour, self._minute, self._second,
                    self._microsecond, self._tzinfo)

    def utcoffset(self):
        if self._tzinfo is None:
            return None
        return self._tzinfo.utcoffset(self)

    def tzname(self):
        if self._tzinfo is None:
            return None
        return self._tzinfo.tzname(self)

    def dst(self):
        if self._tzinfo is None:
            return None
        return self._tzinfo.dst(self)

    def timestamp(self):
        if self._tzinfo is None:
            days = self.toordinal() - _EPOCH_ORDINAL
            secs = (days * 86400 + self._hour * 3600 + self._minute * 60
                   + self._second)
            return secs + self._microsecond / 1000000.0
        return (self - _EPOCH_AWARE).total_seconds()

    def astimezone(self, tz=None):
        if tz is None:
            tz = timezone.utc
        mytz = self._tzinfo
        if mytz is None:
            myoffset = timezone.utc.utcoffset(self)
        else:
            myoffset = mytz.utcoffset(self)
        if tz is mytz:
            return self
        utc = self - myoffset
        utc = utc.replace(tzinfo=tz)
        return tz.fromutc(utc)

    def replace(self, year=None, month=None, day=None, hour=None,
                minute=None, second=None, microsecond=None, tzinfo=_UNSET,
                *, fold=None):
        if year is None:
            year = self._year
        if month is None:
            month = self._month
        if day is None:
            day = self._day
        if hour is None:
            hour = self._hour
        if minute is None:
            minute = self._minute
        if second is None:
            second = self._second
        if microsecond is None:
            microsecond = self._microsecond
        if tzinfo is _UNSET:
            tzinfo = self._tzinfo
        if fold is None:
            fold = self._fold
        return type(self)(year, month, day, hour, minute, second,
                          microsecond, tzinfo, fold=fold)

    def isoformat(self, sep="T", timespec="auto"):
        s = ("%04d-%02d-%02d" % (self._year, self._month, self._day)) \
            + sep + _format_time_hms(self._hour, self._minute,
                                     self._second, self._microsecond,
                                     timespec)
        off = self.utcoffset()
        if off is not None:
            s += _format_offset(off)
        return s

    def __str__(self):
        return self.isoformat(" ")

    def __repr__(self):
        parts = "%d, %d, %d, %d, %d" % (self._year, self._month,
                                        self._day, self._hour,
                                        self._minute)
        if self._second or self._microsecond:
            parts += ", %d" % self._second
            if self._microsecond:
                parts += ", %d" % self._microsecond
        s = "datetime.datetime(" + parts + ")"
        if self._fold:
            s = s[:-1] + ", fold=1)"
        if self._tzinfo is not None:
            s = s[:-1] + ", tzinfo=" + repr(self._tzinfo) + ")"
        return s

    def strftime(self, fmt):
        return _strftime(fmt, self._year, self._month, self._day,
                         self.weekday(), self._hour, self._minute,
                         self._second, self._microsecond,
                         self.utcoffset(), self.tzname())

    def _cmp(self, other, allow_mixed=False):
        mytz = self._tzinfo
        ottz = other._tzinfo
        myoff = otoff = None
        if mytz is ottz:
            base_compare = True
        else:
            myoff = self.utcoffset()
            otoff = other.utcoffset()
            base_compare = myoff == otoff
        if base_compare:
            return _cmp((self._year, self._month, self._day, self._hour,
                        self._minute, self._second, self._microsecond),
                       (other._year, other._month, other._day,
                        other._hour, other._minute, other._second,
                        other._microsecond))
        if myoff is None or otoff is None:
            if allow_mixed:
                return 2
            raise TypeError("can't compare offset-naive and "
                            "offset-aware datetimes")
        diff = self - other
        if diff.days < 0:
            return -1
        if diff:
            return 1
        return 0

    def __eq__(self, other):
        if isinstance(other, datetime):
            return self._cmp(other, True) == 0
        if isinstance(other, date):
            return False
        return NotImplemented

    def __ne__(self, other):
        r = self.__eq__(other)
        return r if r is NotImplemented else not r

    def __lt__(self, other):
        if isinstance(other, datetime):
            return self._cmp(other) < 0
        if isinstance(other, date):
            raise TypeError("can't compare datetime.datetime to "
                            "datetime.date")
        return NotImplemented

    def __le__(self, other):
        if isinstance(other, datetime):
            return self._cmp(other) <= 0
        if isinstance(other, date):
            raise TypeError("can't compare datetime.datetime to "
                            "datetime.date")
        return NotImplemented

    def __gt__(self, other):
        if isinstance(other, datetime):
            return self._cmp(other) > 0
        if isinstance(other, date):
            raise TypeError("can't compare datetime.datetime to "
                            "datetime.date")
        return NotImplemented

    def __ge__(self, other):
        if isinstance(other, datetime):
            return self._cmp(other) >= 0
        if isinstance(other, date):
            raise TypeError("can't compare datetime.datetime to "
                            "datetime.date")
        return NotImplemented

    def __hash__(self):
        tzoff = self.utcoffset()
        if tzoff is None:
            return hash((self._year, self._month, self._day, self._hour,
                        self._minute, self._second, self._microsecond))
        days = _ymd2ord(self._year, self._month, self._day)
        seconds = self._hour * 3600 + self._minute * 60 + self._second
        return hash(timedelta(days, seconds, self._microsecond) - tzoff)

    def __add__(self, other):
        if not isinstance(other, timedelta):
            return NotImplemented
        delta = timedelta(self.toordinal(),
                          self._hour * 3600 + self._minute * 60
                          + self._second, self._microsecond)
        delta = delta + other
        hour, rem = divmod(delta.seconds, 3600)
        minute, second = divmod(rem, 60)
        if 0 < delta.days <= _MAXORDINAL:
            return datetime.combine(
                date.fromordinal(delta.days),
                time(hour, minute, second, delta.microseconds,
                    tzinfo=self._tzinfo))
        raise OverflowError("date value out of range")

    def __radd__(self, other):
        return self.__add__(other)

    def __sub__(self, other):
        if isinstance(other, timedelta):
            return self.__add__(-other)
        if isinstance(other, datetime):
            days1 = self.toordinal()
            days2 = other.toordinal()
            secs1 = self._second + self._minute * 60 + self._hour * 3600
            secs2 = (other._second + other._minute * 60
                    + other._hour * 3600)
            base = timedelta(days1 - days2, secs1 - secs2,
                             self._microsecond - other._microsecond)
            if self._tzinfo is other._tzinfo:
                return base
            myoff = self.utcoffset()
            otoff = other.utcoffset()
            if myoff == otoff:
                return base
            if myoff is None or otoff is None:
                raise TypeError("cannot mix naive and timezone-aware "
                                "datetimes")
            return base + otoff - myoff
        return NotImplemented


class _IsoCalendarDate(tuple):
    """The named 3-tuple `date.isocalendar()` returns, since CPython 3.9."""

    def __new__(cls, year, week, weekday):
        return super().__new__(cls, (year, week, weekday))

    @property
    def year(self):
        return self[0]

    @property
    def week(self):
        return self[1]

    @property
    def weekday(self):
        return self[2]

    def __repr__(self):
        return ("datetime.IsoCalendarDate(year=%d, week=%d, weekday=%d)"
               % (self[0], self[1], self[2]))


# ── strftime ─────────────────────────────────────────────────────────────


def _strftime(fmt, year, month, day, wd, hour, minute, second,
             microsecond, utcoff, tzn):
    """CPython's own set for the `C` locale, matched directly rather than
    through `time.strftime` -- neither `time` nor a locale exists here.
    Checked against the real interpreter for every code this module
    claims, including `%z`/`%Z` on an aware `datetime` and `%j` across a
    leap year and a non-leap century."""
    out = []
    i = 0
    n = len(fmt)
    while i < n:
        ch = fmt[i]
        i += 1
        if ch != "%":
            out.append(ch)
            continue
        if i >= n:
            out.append("%")
            break
        code = fmt[i]
        i += 1
        if code == "Y":
            out.append("%04d" % year)
        elif code == "y":
            out.append("%02d" % (year % 100))
        elif code == "m":
            out.append("%02d" % month)
        elif code == "d":
            out.append("%02d" % day)
        elif code == "H":
            out.append("%02d" % hour)
        elif code == "I":
            h12 = hour % 12
            if h12 == 0:
                h12 = 12
            out.append("%02d" % h12)
        elif code == "p":
            out.append("AM" if hour < 12 else "PM")
        elif code == "M":
            out.append("%02d" % minute)
        elif code == "S":
            out.append("%02d" % second)
        elif code == "f":
            out.append("%06d" % microsecond)
        elif code == "A":
            out.append(_DAYNAMES_FULL[wd])
        elif code == "a":
            out.append(_DAYNAMES_ABBR[wd])
        elif code == "B":
            out.append(_MONTHNAMES_FULL[month])
        elif code == "b":
            out.append(_MONTHNAMES_ABBR[month])
        elif code == "j":
            out.append("%03d" % (_days_before_month(year, month) + day))
        elif code == "z":
            out.append(_format_offset_z(utcoff))
        elif code == "Z":
            out.append(tzn if tzn is not None else "")
        elif code == "%":
            out.append("%")
        else:
            out.append("%")
            out.append(code)
    return "".join(out)


# ── strptime ─────────────────────────────────────────────────────────────

_STRPTIME_WIDTH = {"Y": 4, "y": 2, "m": 2, "d": 2, "H": 2, "I": 2, "M": 2,
                   "S": 2, "j": 3}


def _match_name(text, pos, names):
    """The longest name in `names` matching `text` starting at `pos`,
    case-insensitively -- `(matched_text, index_into_names)` or
    `(None, -1)`. LONGEST rather than first, so `"Mar"` does not shadow
    `"March"` when both are offered (`%B`/`%b` both accept either form,
    matching CPython's own strptime, which builds one alternation of
    every name regardless of which code was written)."""
    best = None
    best_i = -1
    for idx in range(len(names)):
        nm = names[idx]
        cand = text[pos:pos + len(nm)]
        if len(cand) == len(nm) and cand.lower() == nm.lower():
            if best is None or len(nm) > len(best):
                best = nm
                best_i = idx
    return best, best_i


def _strptime_parse(date_string, fmt):
    pos = 0
    n = len(date_string)
    fi = 0
    fn = len(fmt)
    year = 1900
    month = 1
    day = 1
    hour = minute = second = 0
    microsecond = 0
    tzoff = None
    yday = None
    ampm = None
    hour12 = None
    while fi < fn:
        fch = fmt[fi]
        fi += 1
        if fch != "%":
            if pos < n and date_string[pos] == fch:
                pos += 1
                continue
            raise ValueError("time data %r does not match format %r"
                             % (date_string, fmt))
        if fi >= fn:
            raise ValueError("stray %% in format %r" % (fmt,))
        code = fmt[fi]
        fi += 1
        if code == "%":
            if pos < n and date_string[pos] == "%":
                pos += 1
                continue
            raise ValueError("time data %r does not match format %r"
                             % (date_string, fmt))
        if code in _STRPTIME_WIDTH:
            maxw = _STRPTIME_WIDTH[code]
            start = pos
            while pos < n and pos - start < maxw and date_string[pos].isdigit():
                pos += 1
            if pos == start:
                raise ValueError("time data %r does not match format %r"
                                 % (date_string, fmt))
            val = int(date_string[start:pos])
            if code == "Y":
                year = val
            elif code == "y":
                year = (2000 + val) if val < 69 else (1900 + val)
            elif code == "m":
                month = val
            elif code == "d":
                day = val
            elif code == "H":
                hour = val
            elif code == "I":
                hour12 = val
            elif code == "M":
                minute = val
            elif code == "S":
                second = val
            elif code == "j":
                yday = val
        elif code == "f":
            start = pos
            while pos < n and pos - start < 6 and date_string[pos].isdigit():
                pos += 1
            if pos == start:
                raise ValueError("time data %r does not match format %r"
                                 % (date_string, fmt))
            digits = date_string[start:pos]
            microsecond = int(digits + "0" * (6 - len(digits)))
        elif code == "p":
            token = date_string[pos:pos + 2].upper()
            if token == "AM" or token == "PM":
                ampm = token
                pos += 2
            else:
                raise ValueError("time data %r does not match format %r"
                                 % (date_string, fmt))
        elif code == "A" or code == "a":
            matched, _idx = _match_name(date_string, pos, _DAYNAMES_FULL)
            if matched is None:
                matched, _idx = _match_name(date_string, pos,
                                            _DAYNAMES_ABBR)
            if matched is None:
                raise ValueError("time data %r does not match format %r"
                                 % (date_string, fmt))
            pos += len(matched)
        elif code == "B" or code == "b":
            # `[1:]`: index 0 of both tables is the `""` placeholder for
            # "no month 0" (see the module-level tables) -- searched
            # as-is, it matches every position with zero width and no
            # real name is ever tried, since `matched is None` is never
            # true again once that hits first.
            matched, idx = _match_name(date_string, pos,
                                       _MONTHNAMES_FULL[1:])
            if matched is None:
                matched, idx = _match_name(date_string, pos,
                                           _MONTHNAMES_ABBR[1:])
            if matched is None:
                raise ValueError("time data %r does not match format %r"
                                 % (date_string, fmt))
            month = idx + 1
            pos += len(matched)
        elif code == "z":
            if pos < n and date_string[pos] in ("Z", "z"):
                tzoff = timedelta(0)
                pos += 1
            elif pos < n and date_string[pos] in ("+", "-"):
                sign = 1 if date_string[pos] == "+" else -1
                pos += 1
                start = pos
                while pos < n and pos - start < 2 and date_string[pos].isdigit():
                    pos += 1
                hh = int(date_string[start:pos])
                if pos < n and date_string[pos] == ":":
                    pos += 1
                mm = 0
                start = pos
                while pos < n and pos - start < 2 and date_string[pos].isdigit():
                    pos += 1
                if pos > start:
                    mm = int(date_string[start:pos])
                tzoff = timedelta(hours=hh, minutes=mm)
                if sign < 0:
                    tzoff = -tzoff
            else:
                raise ValueError("time data %r does not match format %r"
                                 % (date_string, fmt))
        elif code == "Z":
            start = pos
            while pos < n and date_string[pos].isalpha():
                pos += 1
            zname = date_string[start:pos]
            if zname.upper() in ("UTC", "GMT", "Z"):
                tzoff = timedelta(0)
        else:
            raise ValueError("unsupported strptime format code %r"
                             % (code,))
    if pos != n:
        raise ValueError("unconverted data remains: " + date_string[pos:])
    if hour12 is not None:
        if ampm == "PM" and hour12 != 12:
            hour = hour12 + 12
        elif ampm == "AM" and hour12 == 12:
            hour = 0
        else:
            hour = hour12
    if yday is not None:
        year, month, day = _ord2ymd(_days_before_year(year) + yday)
    return year, month, day, hour, minute, second, microsecond, tzoff


# ── the clock (no timezone database -- see the module docstring) ─────────

_EPOCH_ORDINAL = _ymd2ord(1970, 1, 1)


def _now_seconds():
    return host_time_unix() / 1e9


def _epoch_split(t):
    """`(days_since_epoch, hour, minute, second, microsecond)` for a POSIX
    timestamp `t`, treating "local time" as UTC -- see the module
    docstring for exactly when that is exact rather than approximate."""
    whole = math.floor(t)
    frac = t - whole
    whole = int(whole)
    us = round(frac * 1000000)
    if us >= 1000000:
        whole += 1
        us -= 1000000
    elif us < 0:
        whole -= 1
        us += 1000000
    days, rem = divmod(whole, 86400)
    hour, rem = divmod(rem, 3600)
    minute, second = divmod(rem, 60)
    return days, hour, minute, second, us


# ── class-level constants, bootstrapped after every class exists ─────────

timedelta.min = timedelta(-999999999)
timedelta.max = timedelta(days=999999999, hours=23, minutes=59,
                          seconds=59, microseconds=999999)
timedelta.resolution = timedelta(microseconds=1)

date.min = date(1, 1, 1)
date.max = date(9999, 12, 31)
date.resolution = timedelta(days=1)

_MAXORDINAL = date.max.toordinal()

time.min = time(0, 0, 0, 0)
time.max = time(23, 59, 59, 999999)
time.resolution = timedelta(microseconds=1)

datetime.min = datetime(1, 1, 1, 0, 0, 0, 0)
datetime.max = datetime(9999, 12, 31, 23, 59, 59, 999999)
datetime.resolution = timedelta(microseconds=1)

_EPOCH_AWARE = datetime(1970, 1, 1, tzinfo=timezone.utc)
