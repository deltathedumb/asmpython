"""datetime module: date and time types.

Pure-Python implementation of date, time, datetime, timedelta.
"""
from __future__ import annotations


_DAYS_IN_MONTH: list = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
_DAYS_BEFORE_MONTH: list = [0, 0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]


def _is_leap(year: int) -> int:
    if year % 4 != 0:
        return 0
    if year % 100 != 0:
        return 1
    if year % 400 != 0:
        return 0
    return 1


def _days_in_month(year: int, month: int) -> int:
    if month == 2 and _is_leap(year):
        return 29
    return _DAYS_IN_MONTH[month]


def _days_before_year(year: int) -> int:
    """Number of days before January 1st of year (from year 1)."""
    y: int = year - 1
    return y * 365 + y // 4 - y // 100 + y // 400


def _ymd_to_ordinal(year: int, month: int, day: int) -> int:
    """Days since Jan 1, year 1 (1-based)."""
    return _days_before_year(year) + _DAYS_BEFORE_MONTH[month] + day + (1 if month > 2 and _is_leap(year) else 0)


def _ordinal_to_ymd(n: int) -> list:
    """Convert ordinal day number to [year, month, day]."""
    n400: int = (n - 1) // 146097
    n = n - n400 * 146097
    n100: int = (n - 1) // 36524
    if n100 == 4:
        n100 = 3
    n = n - n100 * 36524
    n4: int = (n - 1) // 1461
    n = n - n4 * 1461
    n1: int = (n - 1) // 365
    if n1 == 4:
        n1 = 3
    year: int = n400 * 400 + n100 * 100 + n4 * 4 + n1 + 1
    n = n - n1 * 365
    month: int = 1
    while month < 12 and n > _days_in_month(year, month):
        n = n - _days_in_month(year, month)
        month = month + 1
    return [year, month, n]


def _zfill2(n: int) -> str:
    if n < 10:
        return "0" + str(n)
    return str(n)


def _zfill4(n: int) -> str:
    s: str = str(n)
    while len(s) < 4:
        s = "0" + s
    return s


def _zfill6(n: int) -> str:
    s: str = str(n)
    while len(s) < 6:
        s = "0" + s
    return s


class timedelta:
    """Represent a duration: days, seconds, microseconds."""

    def __init__(self, days: int = 0, seconds: int = 0,
                 microseconds: int = 0, minutes: int = 0,
                 hours: int = 0, weeks: int = 0) -> None:
        total_us: int = microseconds
        total_us = total_us + seconds * 1000000
        total_us = total_us + minutes * 60 * 1000000
        total_us = total_us + hours * 3600 * 1000000
        total_us = total_us + days * 86400 * 1000000
        total_us = total_us + weeks * 7 * 86400 * 1000000
        us: int = total_us % 1000000
        total_sec: int = total_us // 1000000
        sec: int = total_sec % 86400
        d: int = total_sec // 86400
        self.days: int = d
        self.seconds: int = sec
        self.microseconds: int = us

    def total_seconds(self) -> float:
        return float(self.days * 86400 + self.seconds) + float(self.microseconds) / 1000000.0

    def _to_us(self) -> int:
        return self.days * 86400 * 1000000 + self.seconds * 1000000 + self.microseconds

    def _from_us(self, total_us: int) -> timedelta:
        us: int = total_us % 1000000
        total_sec: int = total_us // 1000000
        sec: int = total_sec % 86400
        d: int = total_sec // 86400
        td: timedelta = timedelta()
        td.days = d
        td.seconds = sec
        td.microseconds = us
        return td

    def __add__(self, other: timedelta) -> timedelta:
        return self._from_us(self._to_us() + other._to_us())

    def __sub__(self, other: timedelta) -> timedelta:
        return self._from_us(self._to_us() - other._to_us())

    def __neg__(self) -> timedelta:
        return self._from_us(-self._to_us())

    def __abs__(self) -> timedelta:
        if self._to_us() < 0:
            return self._from_us(-self._to_us())
        return self._from_us(self._to_us())

    def __mul_int(self, n: int) -> timedelta:
        return self._from_us(self._to_us() * n)

    def __eq__(self, other: timedelta) -> int:
        return 1 if self._to_us() == other._to_us() else 0

    def __lt__(self, other: timedelta) -> int:
        return 1 if self._to_us() < other._to_us() else 0

    def __le__(self, other: timedelta) -> int:
        return 1 if self._to_us() <= other._to_us() else 0

    def __gt__(self, other: timedelta) -> int:
        return 1 if self._to_us() > other._to_us() else 0

    def __ge__(self, other: timedelta) -> int:
        return 1 if self._to_us() >= other._to_us() else 0

    def __bool__(self) -> int:
        return 1 if self._to_us() != 0 else 0

    def __str__(self) -> str:
        total: int = self.seconds
        h: int = total // 3600
        m: int = (total % 3600) // 60
        s: int = total % 60
        base: str = str(self.days) + " day" + ("s" if self.days != 1 else "")
        base = base + ", " + _zfill2(h) + ":" + _zfill2(m) + ":" + _zfill2(s)
        if self.microseconds > 0:
            base = base + "." + _zfill6(self.microseconds)
        return base

    def __repr__(self) -> str:
        return "datetime.timedelta(days=" + str(self.days) + ", seconds=" + str(self.seconds) + ")"


timedelta.min = timedelta(days=-999999999)
timedelta.max = timedelta(days=999999999, hours=23, minutes=59, seconds=59, microseconds=999999)
timedelta.resolution = timedelta(microseconds=1)


class date:
    """Date: year, month, day."""

    def __init__(self, year: int, month: int, day: int) -> None:
        self.year: int = year
        self.month: int = month
        self.day: int = day

    def toordinal(self) -> int:
        return _ymd_to_ordinal(self.year, self.month, self.day)

    def weekday(self) -> int:
        """Return weekday: 0=Monday, 6=Sunday."""
        return (self.toordinal() + 6) % 7

    def isoweekday(self) -> int:
        """Return weekday: 1=Monday, 7=Sunday."""
        return self.toordinal() % 7 + 1

    def isoformat(self) -> str:
        return _zfill4(self.year) + "-" + _zfill2(self.month) + "-" + _zfill2(self.day)

    def ctime(self) -> str:
        _WDAY: list = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        _MON: list = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                      "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        wd: int = self.weekday()
        d: str = str(self.day)
        if len(d) < 2:
            d = " " + d
        return _WDAY[wd] + " " + _MON[self.month] + " " + d + " 00:00:00 " + str(self.year)

    def strftime(self, fmt: str) -> str:
        return _strftime_date(fmt, self.year, self.month, self.day, 0, 0, 0, 0)

    def replace(self, year: int = -1, month: int = -1, day: int = -1) -> date:
        y: int = self.year if year == -1 else year
        m: int = self.month if month == -1 else month
        d2: int = self.day if day == -1 else day
        return date(y, m, d2)

    def __add__(self, delta: timedelta) -> date:
        ordinal: int = self.toordinal() + delta.days
        parts: list = _ordinal_to_ymd(ordinal)
        return date(parts[0], parts[1], parts[2])

    def __sub_date(self, other: date) -> timedelta:
        return timedelta(days=self.toordinal() - other.toordinal())

    def __sub_delta(self, delta: timedelta) -> date:
        ordinal: int = self.toordinal() - delta.days
        parts: list = _ordinal_to_ymd(ordinal)
        return date(parts[0], parts[1], parts[2])

    def __eq__(self, other: date) -> int:
        return 1 if self.year == other.year and self.month == other.month and self.day == other.day else 0

    def __lt__(self, other: date) -> int:
        return 1 if self.toordinal() < other.toordinal() else 0

    def __le__(self, other: date) -> int:
        return 1 if self.toordinal() <= other.toordinal() else 0

    def __gt__(self, other: date) -> int:
        return 1 if self.toordinal() > other.toordinal() else 0

    def __ge__(self, other: date) -> int:
        return 1 if self.toordinal() >= other.toordinal() else 0

    def __hash__(self) -> int:
        return self.toordinal()

    def __str__(self) -> str:
        return self.isoformat()

    def __repr__(self) -> str:
        return "datetime.date(" + str(self.year) + ", " + str(self.month) + ", " + str(self.day) + ")"

    def timetuple(self) -> list:
        return [self.year, self.month, self.day, 0, 0, 0, self.weekday(), self.toordinal(), -1]


def _strftime_date(fmt: str, year: int, month: int, day: int,
                   hour: int, minute: int, second: int, usec: int) -> str:
    """Simple strftime implementation for date/datetime."""
    _WDAY_LONG: list = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    _WDAY_SHORT: list = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    _MON_LONG: list = ["", "January", "February", "March", "April", "May", "June",
                        "July", "August", "September", "October", "November", "December"]
    _MON_SHORT: list = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                         "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    wd: int = (_ymd_to_ordinal(year, month, day) + 6) % 7
    result: str = ""
    i: int = 0
    while i < len(fmt):
        c: str = fmt[i]
        if c == "%" and i + 1 < len(fmt):
            i = i + 1
            spec: str = fmt[i]
            if spec == "Y":
                result = result + _zfill4(year)
            elif spec == "y":
                result = result + _zfill2(year % 100)
            elif spec == "m":
                result = result + _zfill2(month)
            elif spec == "d":
                result = result + _zfill2(day)
            elif spec == "H":
                result = result + _zfill2(hour)
            elif spec == "M":
                result = result + _zfill2(minute)
            elif spec == "S":
                result = result + _zfill2(second)
            elif spec == "f":
                result = result + _zfill6(usec)
            elif spec == "A":
                result = result + _WDAY_LONG[wd]
            elif spec == "a":
                result = result + _WDAY_SHORT[wd]
            elif spec == "B":
                result = result + _MON_LONG[month]
            elif spec == "b" or spec == "h":
                result = result + _MON_SHORT[month]
            elif spec == "j":
                doy: int = _ymd_to_ordinal(year, month, day) - _days_before_year(year)
                result = result + str(doy)
            elif spec == "p":
                result = result + ("AM" if hour < 12 else "PM")
            elif spec == "I":
                h12: int = hour % 12
                if h12 == 0:
                    h12 = 12
                result = result + _zfill2(h12)
            elif spec == "n":
                result = result + "\n"
            elif spec == "t":
                result = result + "\t"
            elif spec == "%":
                result = result + "%"
            else:
                result = result + "%" + spec
        else:
            result = result + c
        i = i + 1
    return result


class time:
    """Time: hour, minute, second, microsecond."""

    def __init__(self, hour: int = 0, minute: int = 0, second: int = 0,
                 microsecond: int = 0) -> None:
        self.hour: int = hour
        self.minute: int = minute
        self.second: int = second
        self.microsecond: int = microsecond

    def isoformat(self) -> str:
        base: str = _zfill2(self.hour) + ":" + _zfill2(self.minute) + ":" + _zfill2(self.second)
        if self.microsecond > 0:
            base = base + "." + _zfill6(self.microsecond)
        return base

    def strftime(self, fmt: str) -> str:
        return _strftime_date(fmt, 1900, 1, 1, self.hour, self.minute, self.second, self.microsecond)

    def replace(self, hour: int = -1, minute: int = -1,
                second: int = -1, microsecond: int = -1) -> time:
        return time(
            self.hour if hour == -1 else hour,
            self.minute if minute == -1 else minute,
            self.second if second == -1 else second,
            self.microsecond if microsecond == -1 else microsecond,
        )

    def __eq__(self, other: time) -> int:
        return 1 if (self.hour == other.hour and self.minute == other.minute and
                     self.second == other.second and self.microsecond == other.microsecond) else 0

    def __lt__(self, other: time) -> int:
        a: int = self.hour * 3600000000 + self.minute * 60000000 + self.second * 1000000 + self.microsecond
        b: int = other.hour * 3600000000 + other.minute * 60000000 + other.second * 1000000 + other.microsecond
        return 1 if a < b else 0

    def __le__(self, other: time) -> int:
        a: int = self.hour * 3600000000 + self.minute * 60000000 + self.second * 1000000 + self.microsecond
        b: int = other.hour * 3600000000 + other.minute * 60000000 + other.second * 1000000 + other.microsecond
        return 1 if a <= b else 0

    def __gt__(self, other: time) -> int:
        a: int = self.hour * 3600000000 + self.minute * 60000000 + self.second * 1000000 + self.microsecond
        b: int = other.hour * 3600000000 + other.minute * 60000000 + other.second * 1000000 + other.microsecond
        return 1 if a > b else 0

    def __ge__(self, other: time) -> int:
        a: int = self.hour * 3600000000 + self.minute * 60000000 + self.second * 1000000 + self.microsecond
        b: int = other.hour * 3600000000 + other.minute * 60000000 + other.second * 1000000 + other.microsecond
        return 1 if a >= b else 0

    def __hash__(self) -> int:
        return self.hour * 3600000000 + self.minute * 60000000 + self.second * 1000000 + self.microsecond

    def __str__(self) -> str:
        return self.isoformat()

    def __repr__(self) -> str:
        return ("datetime.time(" + str(self.hour) + ", " + str(self.minute) + ", " +
                str(self.second) + ")")


class datetime:
    """Combined date and time."""

    def __init__(self, year: int, month: int, day: int,
                 hour: int = 0, minute: int = 0, second: int = 0,
                 microsecond: int = 0) -> None:
        self.year: int = year
        self.month: int = month
        self.day: int = day
        self.hour: int = hour
        self.minute: int = minute
        self.second: int = second
        self.microsecond: int = microsecond

    def date_part(self) -> date:
        return date(self.year, self.month, self.day)

    def time_part(self) -> time:
        return time(self.hour, self.minute, self.second, self.microsecond)

    def toordinal(self) -> int:
        return _ymd_to_ordinal(self.year, self.month, self.day)

    def weekday(self) -> int:
        return (self.toordinal() + 6) % 7

    def isoweekday(self) -> int:
        return self.toordinal() % 7 + 1

    def isoformat(self) -> str:
        base: str = (_zfill4(self.year) + "-" + _zfill2(self.month) + "-" + _zfill2(self.day) +
                     "T" + _zfill2(self.hour) + ":" + _zfill2(self.minute) + ":" + _zfill2(self.second))
        if self.microsecond > 0:
            base = base + "." + _zfill6(self.microsecond)
        return base

    def strftime(self, fmt: str) -> str:
        return _strftime_date(fmt, self.year, self.month, self.day,
                              self.hour, self.minute, self.second, self.microsecond)

    def replace(self, year: int = -1, month: int = -1, day: int = -1,
                hour: int = -1, minute: int = -1, second: int = -1,
                microsecond: int = -1) -> datetime:
        return datetime(
            self.year if year == -1 else year,
            self.month if month == -1 else month,
            self.day if day == -1 else day,
            self.hour if hour == -1 else hour,
            self.minute if minute == -1 else minute,
            self.second if second == -1 else second,
            self.microsecond if microsecond == -1 else microsecond,
        )

    def timestamp(self) -> float:
        """Return POSIX timestamp (seconds since 1970-01-01T00:00:00)."""
        epoch_ord: int = _ymd_to_ordinal(1970, 1, 1)
        days: int = self.toordinal() - epoch_ord
        secs: int = days * 86400 + self.hour * 3600 + self.minute * 60 + self.second
        return float(secs) + float(self.microsecond) / 1000000.0

    def timetuple(self) -> list:
        return [self.year, self.month, self.day, self.hour, self.minute,
                self.second, self.weekday(), self.toordinal() - _days_before_year(self.year), -1]

    def __add__(self, delta: timedelta) -> datetime:
        total_us: int = (self.toordinal() * 86400 * 1000000 +
                         self.hour * 3600 * 1000000 +
                         self.minute * 60 * 1000000 +
                         self.second * 1000000 +
                         self.microsecond +
                         delta._to_us())
        us: int = total_us % 1000000
        total_sec: int = total_us // 1000000
        sec: int = total_sec % 60
        total_min: int = total_sec // 60
        minute: int = total_min % 60
        total_hr: int = total_min // 60
        hour: int = total_hr % 24
        ordinal: int = total_hr // 24
        parts: list = _ordinal_to_ymd(ordinal)
        return datetime(parts[0], parts[1], parts[2], hour, minute, sec, us)

    def __sub_delta(self, delta: timedelta) -> datetime:
        neg: timedelta = delta.__neg__()
        return self.__add__(neg)

    def __sub_datetime(self, other: datetime) -> timedelta:
        a_us: int = (self.toordinal() * 86400 * 1000000 +
                     self.hour * 3600 * 1000000 + self.minute * 60 * 1000000 +
                     self.second * 1000000 + self.microsecond)
        b_us: int = (other.toordinal() * 86400 * 1000000 +
                     other.hour * 3600 * 1000000 + other.minute * 60 * 1000000 +
                     other.second * 1000000 + other.microsecond)
        td: timedelta = timedelta()
        diff: int = a_us - b_us
        td.microseconds = diff % 1000000
        total_sec: int = diff // 1000000
        td.seconds = total_sec % 86400
        td.days = total_sec // 86400
        return td

    def __eq__(self, other: datetime) -> int:
        return 1 if (self.year == other.year and self.month == other.month and
                     self.day == other.day and self.hour == other.hour and
                     self.minute == other.minute and self.second == other.second and
                     self.microsecond == other.microsecond) else 0

    def __lt__(self, other: datetime) -> int:
        return 1 if self.isoformat() < other.isoformat() else 0

    def __le__(self, other: datetime) -> int:
        return 1 if self.isoformat() <= other.isoformat() else 0

    def __gt__(self, other: datetime) -> int:
        return 1 if self.isoformat() > other.isoformat() else 0

    def __ge__(self, other: datetime) -> int:
        return 1 if self.isoformat() >= other.isoformat() else 0

    def __hash__(self) -> int:
        return self.toordinal() * 1000000 + self.microsecond

    def __str__(self) -> str:
        return self.isoformat()

    def __repr__(self) -> str:
        return ("datetime.datetime(" + str(self.year) + ", " + str(self.month) + ", " +
                str(self.day) + ", " + str(self.hour) + ", " + str(self.minute) + ", " +
                str(self.second) + ")")


MINYEAR: int = 1
MAXYEAR: int = 9999
