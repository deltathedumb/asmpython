"""The clock, over the host-service `time` group.

THIS IS THE FIRST TIER-5 MODULE, and it is here because it needed nothing
new. `docs/STDLIB.md` puts `time` in the tier that "NEEDS THE FLOOR TO
GROW", and for `socket` or `subprocess` that is still true -- but the
floor already grew for this one: `objects/hostsvc.py` declares a `time`
group (`host_time_unix`, `host_time_monotonic`, `host_sleep`) and every
backend that answers it gets this module for free, with no `ctypes`
declaration and no promise to a linker. So the tier boundary was about
the CLOCK and not about `time`, and this is the module that says so.

COVERAGE: `time()`/`time_ns()`, `monotonic()`/`monotonic_ns()`,
`perf_counter()`/`perf_counter_ns()`, `sleep()`, `struct_time` (indexable,
attribute-accessible, comparable against a plain tuple, with CPython's own
`repr` and the three named-but-not-indexed fields), `gmtime()`,
`localtime()`, `mktime()`, `asctime()`, `ctime()`, `strftime()`,
`strptime()`, `process_time()`/`process_time_ns()`, and the module
constants `timezone`, `altzone`, `daylight`, `tzname`.

THE ONE DIVERGENCE, AND IT IS THE SAME ONE `datetime` HAS: there is no
timezone database and no host call that would reach one, so LOCAL TIME IS
UTC. `localtime()` answers `gmtime()`'s fields (naming the zone `tzname[0]`
where `gmtime` names it `'GMT'`, exactly as CPython does), `mktime()`
inverts it, `timezone` and `altzone` are 0, `daylight` is 0 and `tzname`
is `('UTC', 'UTC')`.
That is exact on a machine running UTC and off by a fixed offset
elsewhere; it is stated here rather than discovered, and it is the honest
shape -- inventing an offset would be wrong everywhere instead of right
somewhere.

NOT COVERED, each refused BY NAME rather than approximated:
`thread_time`/`thread_time_ns` (there is one thread and no way to ask
about another, so answering `process_time` would be a different
measurement wearing the same name), `monotonic`'s and `perf_counter`'s
`*_info` structures via `get_clock_info` (a `SimpleNamespace` of claims
about resolution and adjustability that nothing here can measure),
`clock_gettime`/`clock_settime`/`CLOCK_*` (POSIX clock ids are a
platform detail this layer deliberately does not have), `tzset` (nothing
to set), `strftime`'s locale-dependent `%c`/`%x`/`%X` directives beyond
the C locale, and `struct_time`'s pickling hooks.
"""


class error(Exception):
    pass


# ── the two clocks ──────────────────────────────────────────────────────
#
# NANOSECONDS UNDERNEATH, seconds on top. `objects/hostsvc.py` answers an
# `i64` of nanoseconds for exactly the reason CPython added the `_ns`
# family: a float carries 53 bits of mantissa, and a POSIX timestamp in
# nanoseconds has passed that since 1970 -- so `time()` is the lossy one
# and `time_ns()` is the truth. Deriving the float from the integer (and
# never the other way round) is what keeps `time_ns()` exact.


def time_ns():
    """Nanoseconds since the epoch, as an int."""
    return host_time_unix()


def time():
    """Seconds since the epoch, as a float."""
    return host_time_unix() / 1000000000.0


def monotonic_ns():
    """Nanoseconds from an unspecified point, never going backwards."""
    return host_time_monotonic()


def monotonic():
    return host_time_monotonic() / 1000000000.0


def perf_counter_ns():
    """The highest-resolution clock available, in nanoseconds.

    THE SAME CLOCK AS `monotonic` HERE, and CPython does not promise
    otherwise -- it promises the highest available resolution and a
    monotonic reading, both of which `host_time_monotonic` is. A backend
    with a finer clock answers this differently by answering that host
    service differently; nothing above this line has to change.
    """
    return host_time_monotonic()


def perf_counter():
    return host_time_monotonic() / 1000000000.0


def process_time_ns():
    """CPU time used by this process, in nanoseconds.

    `host_time_monotonic` is what there is. On the C backend it is
    `clock()`, which IS process CPU time and makes this exact; on a
    backend whose monotonic clock is elapsed time it is an over-estimate
    while the process is descheduled. Named rather than hidden.
    """
    return host_time_monotonic()


def process_time():
    return host_time_monotonic() / 1000000000.0


def sleep(secs):
    """Suspend for `secs` seconds. A negative value is an error, as in
    CPython; zero returns immediately."""
    if secs < 0:
        raise ValueError("sleep length must be non-negative")
    host_sleep(int(secs * 1000000000.0))


# ── the civil calendar ──────────────────────────────────────────────────
#
# The same proleptic Gregorian arithmetic `bundled/datetime.py` uses, and
# deliberately a SECOND COPY of it rather than an import: splicing
# `datetime` in would pull its whole class tower into every program that
# only wanted `time.time()`, and these are forty lines with no state.

_DAYS_IN_MONTH = [-1, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
_DAYS_BEFORE_MONTH = [-1]


def _fill_days_before_month():
    total = 0
    for i in range(1, 13):
        _DAYS_BEFORE_MONTH.append(total)
        total = total + _DAYS_IN_MONTH[i]


_fill_days_before_month()


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
    extra = 0
    if month > 2 and _is_leap(year):
        extra = 1
    return _DAYS_BEFORE_MONTH[month] + extra


def _ymd2ord(year, month, day):
    return _days_before_year(year) + _days_before_month(year, month) + day


_DI400Y = _days_before_year(401)
_DI100Y = _days_before_year(101)
_DI4Y = _days_before_year(5)


def _ord2ymd(n):
    n = n - 1
    n400, n = divmod(n, _DI400Y)
    year = n400 * 400 + 1
    n100, n = divmod(n, _DI100Y)
    n4, n = divmod(n, _DI4Y)
    n1, n = divmod(n, 365)
    year = year + n100 * 100 + n4 * 4 + n1
    if n1 == 4 or n100 == 4:
        return year - 1, 12, 31
    month = (n + 50) >> 5
    preceding = _days_before_month(year, month)
    if preceding > n:
        month = month - 1
        preceding = preceding - _days_in_month(year, month)
    n = n - preceding
    return year, month, n + 1


#: 1970-01-01 as a proleptic Gregorian ordinal, so a POSIX timestamp and a
#: calendar date convert through one addition.
_EPOCH_ORDINAL = _ymd2ord(1970, 1, 1)


# ── struct_time ─────────────────────────────────────────────────────────

_FIELDS = ("tm_year", "tm_mon", "tm_mday", "tm_hour", "tm_min", "tm_sec",
           "tm_wday", "tm_yday", "tm_isdst")


class struct_time:
    """The nine-field time tuple.

    CPython'S IS A "STRUCTSEQ": a real tuple subclass with named fields,
    which is why `gmtime()[0]` and `gmtime().tm_year` are the same value
    and why `t == (2026, 1, 1, ...)` is True against a plain tuple. This
    frontend cannot subclass `tuple`, so the tuple is HELD and every
    sequence operation forwarded -- indexing, slicing, length, iteration,
    comparison, `in`, `+`, `*`, `hash`. What that costs is `isinstance(t,
    tuple)`, which answers False here and True there; what it buys is
    every operation a program actually performs on one.

    `n_fields` is 11 and `n_sequence_fields` is 9 in CPython: `tm_gmtoff`
    and `tm_zone` are NAMED BUT NOT INDEXED, so they survive attribute
    access and never appear in the tuple, in the repr, or in a
    comparison. Reproduced, because a program that unpacks a `struct_time`
    into nine names would break if they did.
    """

    n_fields = 11
    n_sequence_fields = 9
    n_unnamed_fields = 0

    def __init__(self, sequence, zone=None):
        items = tuple(sequence)
        if len(items) != 9:
            raise TypeError("time.struct_time() takes a 9-sequence "
                            "(%d-sequence given)" % (len(items),))
        self._items = items
        self.tm_year = items[0]
        self.tm_mon = items[1]
        self.tm_mday = items[2]
        self.tm_hour = items[3]
        self.tm_min = items[4]
        self.tm_sec = items[5]
        self.tm_wday = items[6]
        self.tm_yday = items[7]
        self.tm_isdst = items[8]
        # OUTSIDE THE SEQUENCE, and the offset is always 0 here -- see the
        # module docstring on why local time is UTC.
        #
        # THE NAME IS NOT ALWAYS THE SAME STRING, though, and CPython is
        # exact about which: `gmtime()` answers `'GMT'` and `localtime()`
        # answers the local zone's name, which on a UTC machine is
        # `'UTC'`. Two spellings of one offset, and a program printing
        # `%Z` sees the difference.
        self.tm_gmtoff = 0
        if zone is None:
            zone = tzname[0]
        self.tm_zone = zone

    def __len__(self):
        return 9

    def __getitem__(self, i):
        return self._items[i]

    def __iter__(self):
        return iter(self._items)

    def __contains__(self, item):
        return item in self._items

    def __add__(self, other):
        if isinstance(other, struct_time):
            return self._items + other._items
        return self._items + other

    def __radd__(self, other):
        return other + self._items

    def __mul__(self, n):
        return self._items * n

    def __rmul__(self, n):
        return self._items * n

    def __hash__(self):
        return hash(self._items)

    def _other(self, other):
        if isinstance(other, struct_time):
            return other._items
        return other

    def __eq__(self, other):
        return self._items == self._other(other)

    def __ne__(self, other):
        return self._items != self._other(other)

    def __lt__(self, other):
        return self._items < self._other(other)

    def __le__(self, other):
        return self._items <= self._other(other)

    def __gt__(self, other):
        return self._items > self._other(other)

    def __ge__(self, other):
        return self._items >= self._other(other)

    def __repr__(self):
        parts = []
        for i in range(9):
            parts.append("%s=%d" % (_FIELDS[i], self._items[i]))
        return "time.struct_time(" + ", ".join(parts) + ")"


def _as_nine(t):
    """Whatever a caller passed where a time tuple was wanted, as nine
    ints -- CPython accepts any 9-sequence, not only a `struct_time`."""
    if isinstance(t, struct_time):
        return t._items
    items = tuple(t)
    if len(items) != 9:
        raise TypeError("function takes exactly 9 arguments (%d given)"
                        % (len(items),))
    return items


# ── converting between a timestamp and a calendar ───────────────────────


def gmtime(secs=None):
    """UTC broken down into a `struct_time`. `None` means now.

    TRUNCATED TOWARDS MINUS INFINITY, not towards zero: CPython floors,
    so a timestamp of -0.5 is the second BEFORE the epoch and not the
    second after it.
    """
    if secs is None:
        secs = time()
    whole = int(secs // 1)
    days, rem = divmod(whole, 86400)
    hour, rem = divmod(rem, 3600)
    minute, second = divmod(rem, 60)
    year, month, day = _ord2ymd(days + _EPOCH_ORDINAL)
    # 1970-01-01 WAS A THURSDAY, and CPython's `tm_wday` is Monday-based
    # (0 = Monday), so the ordinal-to-weekday shift is +3 rather than the
    # +6 a Sunday-based field would want.
    wday = (days + 3) % 7
    yday = _ymd2ord(year, month, day) - _days_before_year(year)
    # `'GMT'`, not `tzname[0]`: CPython's `gmtime()` names the zone that
    # way and its `localtime()` names the local one, so `%Z` tells the two
    # calls apart even where the offset is identical.
    return struct_time((year, month, day, hour, minute, second,
                        wday, yday, 0), "GMT")


def localtime(secs=None):
    """LOCAL TIME IS UTC HERE -- see the module docstring. `tm_isdst` is
    0 rather than -1: there is no database to be unsure about, and the
    zone is named `tzname[0]` rather than `'GMT'`, which is the one thing
    that distinguishes this from `gmtime`."""
    t = gmtime(secs)
    return struct_time(t._items, tzname[0])


def mktime(t):
    """A local `struct_time` back to a timestamp, as a float.

    THE INVERSE OF `localtime`, which is what CPython promises, so with
    local time being UTC this is the inverse of `gmtime` too. Fields out
    of range are NORMALISED rather than refused, exactly as `mktime` does
    (`tm_mon` 13 is January of the next year), and the answer is a float
    because CPython's is.
    """
    items = _as_nine(t)
    year, month = items[0], items[1]
    # NORMALISE THE MONTH FIRST, so the day count below is taken against
    # the year the month actually lands in.
    extra, month = divmod(month - 1, 12)
    year = year + extra
    month = month + 1
    days = _ymd2ord(year, month, 1) - _EPOCH_ORDINAL + (items[2] - 1)
    return float(days * 86400 + items[3] * 3600 + items[4] * 60 + items[5])


# ── rendering ───────────────────────────────────────────────────────────

_WDAY = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTH = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_WDAY_FULL = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
              "Saturday", "Sunday")
_MONTH_FULL = ("January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November",
               "December")


def asctime(t=None):
    """`'Sun Jun 20 23:21:05 1993'` -- a fixed 24-character form with a
    SPACE-PADDED day of month, which is the one field a reader trips
    over: `%2d` and not `%02d`, so the 5th is `' 5'`."""
    if t is None:
        t = localtime()
    items = _as_nine(t)
    return "%s %s %2d %02d:%02d:%02d %d" % (
        _WDAY[items[6] % 7], _MONTH[items[1] - 1], items[2],
        items[3], items[4], items[5], items[0])


def ctime(secs=None):
    """`asctime(localtime(secs))`, which is CPython's own definition."""
    if secs is None:
        return asctime(localtime())
    return asctime(localtime(secs))


def strftime(format, t=None):
    """The C-locale directives, over a nine-field time tuple.

    `%c`, `%x` and `%X` ARE THE C LOCALE'S, which is what CPython answers
    under `LC_TIME=C` and what every other locale makes
    platform-dependent -- see the module docstring.
    """
    if t is None:
        t = localtime()
    items = _as_nine(t)
    # `%Z` NAMES THE STRUCT'S OWN ZONE when it has one -- `gmtime()`'s is
    # `'GMT'` and `localtime()`'s is the local name. A plain 9-tuple has
    # none, and CPython falls back to the local zone for it.
    zone = tzname[0]
    if isinstance(t, struct_time):
        zone = t.tm_zone
    year, month, day = items[0], items[1], items[2]
    hour, minute, second = items[3], items[4], items[5]
    wday, yday = items[6], items[7]
    out = []
    i = 0
    n = len(format)
    while i < n:
        ch = format[i]
        if ch != "%":
            out.append(ch)
            i = i + 1
            continue
        i = i + 1
        if i >= n:
            # A TRAILING `%` IS ITSELF. CPython's platform `strftime` keeps
            # it rather than raising, and a format string ending in one is
            # nearly always a typo the program would rather see printed.
            out.append("%")
            break
        code = format[i]
        i = i + 1
        if code == "Y":
            out.append("%d" % (year,))
        elif code == "y":
            out.append("%02d" % (year % 100,))
        elif code == "m":
            out.append("%02d" % (month,))
        elif code == "d":
            out.append("%02d" % (day,))
        elif code == "H":
            out.append("%02d" % (hour,))
        elif code == "I":
            twelve = hour % 12
            if twelve == 0:
                twelve = 12
            out.append("%02d" % (twelve,))
        elif code == "M":
            out.append("%02d" % (minute,))
        elif code == "S":
            out.append("%02d" % (second,))
        elif code == "p":
            if hour < 12:
                out.append("AM")
            else:
                out.append("PM")
        elif code == "a":
            out.append(_WDAY[wday % 7])
        elif code == "A":
            out.append(_WDAY_FULL[wday % 7])
        elif code == "b" or code == "h":
            out.append(_MONTH[month - 1])
        elif code == "B":
            out.append(_MONTH_FULL[month - 1])
        elif code == "j":
            out.append("%03d" % (yday,))
        elif code == "w":
            # SUNDAY-BASED, unlike `tm_wday`: `%w` is 0 for Sunday and
            # `tm_wday` is 0 for Monday, and confusing the two is off by
            # one for six days in seven.
            out.append("%d" % ((wday + 1) % 7,))
        elif code == "u":
            out.append("%d" % (wday + 1,))
        elif code == "Z":
            out.append(zone)
        elif code == "z":
            out.append("+0000")
        elif code == "n":
            out.append("\n")
        elif code == "t":
            out.append("\t")
        elif code == "%":
            out.append("%")
        elif code == "c":
            out.append("%s %s %2d %02d:%02d:%02d %d" % (
                _WDAY[wday % 7], _MONTH[month - 1], day,
                hour, minute, second, year))
        elif code == "x":
            out.append("%02d/%02d/%02d" % (month, day, year % 100))
        elif code == "X":
            out.append("%02d:%02d:%02d" % (hour, minute, second))
        elif code == "D":
            out.append("%02d/%02d/%02d" % (month, day, year % 100))
        elif code == "F":
            out.append("%d-%02d-%02d" % (year, month, day))
        elif code == "T":
            out.append("%02d:%02d:%02d" % (hour, minute, second))
        elif code == "R":
            out.append("%02d:%02d" % (hour, minute))
        elif code == "e":
            out.append("%2d" % (day,))
        elif code == "C":
            out.append("%02d" % (year // 100,))
        elif code == "G":
            out.append("%d" % (_isocalendar(year, month, day)[0],))
        elif code == "V":
            out.append("%02d" % (_isocalendar(year, month, day)[1],))
        else:
            # AN UNKNOWN DIRECTIVE IS ITSELF. CPython hands the format to
            # the platform's `strftime`, and glibc's copies through what it
            # does not recognise -- `strftime('%Q')` answers `'%Q'` rather
            # than raising. Measured against it rather than assumed, since
            # raising here is the more obvious-looking choice and is wrong.
            out.append("%")
            out.append(code)
    return "".join(out)


def _isocalendar(year, month, day):
    """`(iso_year, iso_week, iso_weekday)`, for `%G` and `%V`.

    ISO 8601's week 1 is the one holding the first THURSDAY, which is why
    a date in early January can belong to the previous ISO year and one
    in late December to the next.
    """
    ordinal = _ymd2ord(year, month, day)
    weekday = (ordinal + 6) % 7 + 1
    thursday = ordinal - weekday + 4
    iso_year = _ord2ymd(thursday)[0]
    week = (thursday - _ymd2ord(iso_year, 1, 1)) // 7 + 1
    return iso_year, week, weekday


# ── parsing ─────────────────────────────────────────────────────────────

_MONTH_LOOKUP = {}
_WDAY_LOOKUP = {}


def _fill_lookups():
    for i in range(12):
        _MONTH_LOOKUP[_MONTH[i].lower()] = i + 1
        _MONTH_LOOKUP[_MONTH_FULL[i].lower()] = i + 1
    for i in range(7):
        _WDAY_LOOKUP[_WDAY[i].lower()] = i
        _WDAY_LOOKUP[_WDAY_FULL[i].lower()] = i


_fill_lookups()


def _digits(s, pos, most):
    """Up to `most` digits from `pos`, and where they ended. CPython's
    `strptime` is built on regexes that accept ONE OR TWO digits for a
    two-digit field, so `%d` matches both `05` and `5`."""
    end = pos
    limit = pos + most
    while end < len(s) and end < limit and s[end].isdigit():
        end = end + 1
    if end == pos:
        raise ValueError("time data did not match format")
    return int(s[pos:end]), end


def _word(s, pos):
    end = pos
    while end < len(s) and s[end].isalpha():
        end = end + 1
    return s[pos:end], end


def strptime(string, format="%a %b %d %H:%M:%S %Y"):
    """Parse `string` by `format` into a `struct_time`.

    THE DEFAULT FORMAT IS `asctime`'s, so `strptime(ctime(t))` round-trips
    -- CPython's own default and the reason it is that string.

    `tm_wday` and `tm_yday` are COMPUTED from the date rather than taken
    from `%a`/`%j`, which is what CPython does too: a format naming both a
    weekday and a date that disagree resolves to the date.
    """
    year = 1900
    month = 1
    day = 1
    hour = 0
    minute = 0
    second = 0
    hour12 = None
    ampm = None
    pos = 0
    i = 0
    n = len(format)
    while i < n:
        ch = format[i]
        if ch != "%":
            if ch == " ":
                # WHITESPACE MATCHES ANY RUN OF IT, including none, which
                # is how CPython's own `\s*` behaves and what makes a
                # space-padded `%e` day parse back.
                i = i + 1
                while pos < len(string) and string[pos] == " ":
                    pos = pos + 1
                continue
            if pos >= len(string) or string[pos] != ch:
                raise ValueError("time data %r does not match format %r"
                                 % (string, format))
            pos = pos + 1
            i = i + 1
            continue
        i = i + 1
        if i >= n:
            raise ValueError("stray %% in format %r" % (format,))
        code = format[i]
        i = i + 1
        if code == "Y":
            year, pos = _digits(string, pos, 4)
        elif code == "y":
            two, pos = _digits(string, pos, 2)
            # CPython'S OWN PIVOT: 69-99 are 1900s, 00-68 are 2000s.
            if two <= 68:
                year = 2000 + two
            else:
                year = 1900 + two
        elif code == "m":
            month, pos = _digits(string, pos, 2)
        elif code == "d" or code == "e":
            while pos < len(string) and string[pos] == " ":
                pos = pos + 1
            day, pos = _digits(string, pos, 2)
        elif code == "H":
            hour, pos = _digits(string, pos, 2)
        elif code == "I":
            hour12, pos = _digits(string, pos, 2)
        elif code == "M":
            minute, pos = _digits(string, pos, 2)
        elif code == "S":
            second, pos = _digits(string, pos, 2)
        elif code == "j":
            yday, pos = _digits(string, pos, 3)
            month = 1
            day = 1
            year, month, day = _ord2ymd(_days_before_year(year) + yday)
        elif code == "p":
            word, pos = _word(string, pos)
            ampm = word.upper()
            if ampm != "AM" and ampm != "PM":
                raise ValueError("time data %r does not match format %r"
                                 % (string, format))
        elif code == "b" or code == "B" or code == "h":
            word, pos = _word(string, pos)
            got = _MONTH_LOOKUP.get(word.lower())
            if got is None:
                raise ValueError("time data %r does not match format %r"
                                 % (string, format))
            month = got
        elif code == "a" or code == "A":
            word, pos = _word(string, pos)
            if _WDAY_LOOKUP.get(word.lower()) is None:
                raise ValueError("time data %r does not match format %r"
                                 % (string, format))
        elif code == "Z":
            word, pos = _word(string, pos)
        elif code == "%":
            if pos >= len(string) or string[pos] != "%":
                raise ValueError("time data %r does not match format %r"
                                 % (string, format))
            pos = pos + 1
        else:
            raise ValueError("'%%%s' is a bad directive in format %r"
                             % (code, format))
    if pos != len(string):
        raise ValueError("unconverted data remains: " + string[pos:])
    if hour12 is not None:
        if ampm == "PM" and hour12 != 12:
            hour = hour12 + 12
        elif ampm == "AM" and hour12 == 12:
            hour = 0
        else:
            hour = hour12
    ordinal = _ymd2ord(year, month, day)
    wday = (ordinal + 6) % 7
    yday = ordinal - _days_before_year(year)
    return struct_time((year, month, day, hour, minute, second,
                        wday, yday, -1))


# ── the timezone the process is in ──────────────────────────────────────
#
# ALL FOUR SAY "UTC", for the reason the module docstring gives. They are
# module-level VALUES rather than a lookup because CPython's are too:
# `time.timezone` is read once at import, from `tzset`, and a program that
# changes `TZ` afterwards has to call `tzset()` to see it.

#: Seconds WEST of UTC for the non-DST local zone. CPython's sign
#: convention, which is the opposite of the one most people expect.
timezone = 0
#: The same for the DST zone. Equal to `timezone` when there is no DST.
altzone = 0
#: Whether a DST zone is defined at all.
daylight = 0
#: `(standard, dst)`, both the same when `daylight` is 0.
tzname = ("UTC", "UTC")
