"""Dates and times.

Arithmetic on a calendar is arithmetic on a COUNT OF DAYS: every date is
converted to a day number, the sum or difference is taken there, and the
result is converted back. That is why `_to_ordinal`/`_from_ordinal` are the
only hard part -- once a date is a number, `+ timedelta` is addition.

WHAT IS NOT HERE: `now`, `today` and `utcnow`, because there is no clock to
read; parsing beyond `fromisoformat`; and the `%`-format of `strftime`. A
stub of `now()` returning a fixed instant would be a wrong answer rather than
a missing feature, so it is absent and says so.
"""

MINYEAR = 1
MAXYEAR = 9999

_DAYS_IN_MONTH = (0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
_DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
              "Saturday", "Sunday")


def _is_leap(year):
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _days_in_month(year, month):
    if month == 2 and _is_leap(year):
        return 29
    return _DAYS_IN_MONTH[month]


def _to_ordinal(year, month, day):
    """Days since 0001-01-01, which is ordinal 1 -- the proleptic Gregorian
    count `date.toordinal` answers."""
    total = 0
    y = year - 1
    total = y * 365 + y // 4 - y // 100 + y // 400
    for m in range(1, month):
        total = total + _days_in_month(year, m)
    return total + day


def _from_ordinal(n):
    """The inverse, by walking down the year and then the month. Linear in
    the number of years, which is fine for a calendar and much easier to read
    than the closed form."""
    year = 1
    while True:
        span = 366 if _is_leap(year) else 365
        if n <= span:
            break
        n = n - span
        year = year + 1
    month = 1
    while n > _days_in_month(year, month):
        n = n - _days_in_month(year, month)
        month = month + 1
    return (year, month, n)


def _two(n):
    return ("0" + str(n)) if n < 10 else str(n)


def _four(n):
    text = str(n)
    while len(text) < 4:
        text = "0" + text
    return text


class timedelta:
    """A DURATION, normalised to days, seconds and microseconds.

    Normalised at construction so that two equal durations compare equal
    whichever way they were spelled: `timedelta(hours=24)` and
    `timedelta(days=1)` are one value.
    """

    def __init__(self, days=0, seconds=0, microseconds=0, milliseconds=0,
                 minutes=0, hours=0, weeks=0):
        micro = (microseconds + milliseconds * 1000
                 + seconds * 1000000 + minutes * 60000000
                 + hours * 3600000000
                 + (days + weeks * 7) * 86400000000)
        self._micro = int(micro)

    @property
    def days(self):
        # FLOOR DIVISION, so a negative duration has a negative day count and
        # a non-negative remainder -- which is how CPython normalises one.
        return self._micro // 86400000000

    @property
    def seconds(self):
        return (self._micro % 86400000000) // 1000000

    @property
    def microseconds(self):
        return self._micro % 1000000

    def total_seconds(self):
        return self._micro / 1000000

    def __add__(self, other):
        if isinstance(other, timedelta):
            return _delta(self._micro + other._micro)
        return NotImplemented

    def __sub__(self, other):
        if isinstance(other, timedelta):
            return _delta(self._micro - other._micro)
        return NotImplemented

    def __neg__(self):
        return _delta(-self._micro)

    def __abs__(self):
        return _delta(abs(self._micro))

    def __mul__(self, other):
        if isinstance(other, int) and not isinstance(other, bool):
            return _delta(self._micro * other)
        return NotImplemented

    def __rmul__(self, other):
        return self.__mul__(other)

    def __bool__(self):
        return self._micro != 0

    def __eq__(self, other):
        if isinstance(other, timedelta):
            return self._micro == other._micro
        return NotImplemented

    def __ne__(self, other):
        got = self.__eq__(other)
        return got if got is NotImplemented else not got

    def __lt__(self, other):
        return self._micro < other._micro

    def __le__(self, other):
        return self._micro <= other._micro

    def __gt__(self, other):
        return self._micro > other._micro

    def __ge__(self, other):
        return self._micro >= other._micro

    def __hash__(self):
        return hash(self._micro)

    def __repr__(self):
        parts = []
        if self.days:
            parts.append("days=" + str(self.days))
        if self.seconds:
            parts.append("seconds=" + str(self.seconds))
        if self.microseconds:
            parts.append("microseconds=" + str(self.microseconds))
        return "datetime.timedelta(" + ", ".join(parts) + ")"

    def __str__(self):
        days, rest = self.days, self.seconds
        head = (str(days) + (" day, " if abs(days) == 1 else " days, ")) \
            if days else ""
        return head + str(rest // 3600) + ":" + _two((rest % 3600) // 60) \
            + ":" + _two(rest % 60)


def _delta(micro):
    made = timedelta()
    made._micro = micro
    return made


class timezone:
    """A fixed offset from UTC. The only kind of zone without a database."""

    def __init__(self, offset, name=None):
        self._offset = offset
        self._name = name

    def utcoffset(self, dt):
        return self._offset

    def tzname(self, dt):
        if self._name is not None:
            return self._name
        total = int(self._offset.total_seconds())
        if total == 0:
            return "UTC"
        sign = "+" if total > 0 else "-"
        total = abs(total)
        return "UTC" + sign + _two(total // 3600) + ":" \
            + _two((total % 3600) // 60)

    def dst(self, dt):
        return None

    def __repr__(self):
        return "datetime.timezone(" + repr(self._offset) + ")"


timezone.utc = timezone(timedelta(0), "UTC")


class date:
    def __init__(self, year, month, day):
        if month < 1 or month > 12:
            raise ValueError("month must be in 1..12")
        if day < 1 or day > _days_in_month(year, month):
            raise ValueError("day is out of range for month")
        self._year = year
        self._month = month
        self._day = day

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
        return _to_ordinal(self._year, self._month, self._day)

    def weekday(self):
        # 0001-01-01 WAS A MONDAY in the proleptic Gregorian calendar, which
        # is what makes this `(ordinal - 1) % 7`.
        return (self.toordinal() - 1) % 7

    def isoweekday(self):
        return self.weekday() + 1

    def isoformat(self):
        return _four(self._year) + "-" + _two(self._month) + "-" \
            + _two(self._day)

    def replace(self, year=None, month=None, day=None):
        return date(self._year if year is None else year,
                    self._month if month is None else month,
                    self._day if day is None else day)

    def __str__(self):
        return self.isoformat()

    def __repr__(self):
        return "datetime.date(" + str(self._year) + ", " + str(self._month) \
            + ", " + str(self._day) + ")"

    def _key(self):
        return self.toordinal()

    def __eq__(self, other):
        if isinstance(other, date) and not isinstance(other, datetime):
            return self._key() == other._key()
        return NotImplemented

    def __ne__(self, other):
        got = self.__eq__(other)
        return got if got is NotImplemented else not got

    def __lt__(self, other):
        return self._key() < other._key()

    def __le__(self, other):
        return self._key() <= other._key()

    def __gt__(self, other):
        return self._key() > other._key()

    def __ge__(self, other):
        return self._key() >= other._key()

    def __hash__(self):
        return hash(self._key())

    def __add__(self, other):
        if isinstance(other, timedelta):
            y, m, d = _from_ordinal(self.toordinal() + other.days)
            return date(y, m, d)
        return NotImplemented

    def __sub__(self, other):
        if isinstance(other, timedelta):
            y, m, d = _from_ordinal(self.toordinal() - other.days)
            return date(y, m, d)
        if isinstance(other, date):
            return timedelta(days=self.toordinal() - other.toordinal())
        return NotImplemented

    @staticmethod
    def fromordinal(n):
        y, m, d = _from_ordinal(n)
        return date(y, m, d)

    @staticmethod
    def fromisoformat(text):
        return date(int(text[0:4]), int(text[5:7]), int(text[8:10]))


class time:
    def __init__(self, hour=0, minute=0, second=0, microsecond=0, tzinfo=None,
                 fold=0):
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

    def isoformat(self):
        out = _two(self._hour) + ":" + _two(self._minute) + ":" \
            + _two(self._second)
        if self._microsecond:
            frac = str(self._microsecond)
            while len(frac) < 6:
                frac = "0" + frac
            out = out + "." + frac
        return out

    def __str__(self):
        return self.isoformat()

    def __repr__(self):
        return "datetime.time(" + str(self._hour) + ", " + str(self._minute) \
            + ", " + str(self._second) + ")"


class datetime(date):
    """A date and a time together.

    Inherits `date` so `isinstance(dt, date)` holds, which is the relationship
    CPython has -- and the reason `date.__eq__` refuses a datetime: comparing
    one to a date is False in CPython rather than an ordering.
    """

    def __init__(self, year, month, day, hour=0, minute=0, second=0,
                 microsecond=0, tzinfo=None, fold=0):
        date.__init__(self, year, month, day)
        self._hour = hour
        self._minute = minute
        self._second = second
        self._microsecond = microsecond
        self._tzinfo = tzinfo
        # PEP 495: which of two identical wall clocks this one is. It takes
        # part in NO comparison and no arithmetic -- two datetimes differing
        # only in `fold` are equal -- and exists so a program can say which
        # side of a repeated hour it meant.
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

    def date(self):
        return date(self._year, self._month, self._day)

    def time(self):
        return time(self._hour, self._minute, self._second, self._microsecond)

    def timetz(self):
        return time(self._hour, self._minute, self._second, self._microsecond,
                    self._tzinfo, self._fold)

    def utcoffset(self):
        return self._tzinfo.utcoffset(self) if self._tzinfo is not None \
            else None

    def tzname(self):
        return self._tzinfo.tzname(self) if self._tzinfo is not None else None

    def dst(self):
        return self._tzinfo.dst(self) if self._tzinfo is not None else None

    def replace(self, year=None, month=None, day=None, hour=None, minute=None,
                second=None, microsecond=None, tzinfo=False, fold=None):
        # `tzinfo=False` IS THE SENTINEL, not None: replacing it WITH None is
        # how a program drops the zone, so None cannot also mean "unchanged".
        return datetime(
            self._year if year is None else year,
            self._month if month is None else month,
            self._day if day is None else day,
            self._hour if hour is None else hour,
            self._minute if minute is None else minute,
            self._second if second is None else second,
            self._microsecond if microsecond is None else microsecond,
            self._tzinfo if tzinfo is False else tzinfo,
            self._fold if fold is None else fold)

    def isoformat(self, sep="T"):
        out = _four(self._year) + "-" + _two(self._month) + "-" \
            + _two(self._day) + sep + _two(self._hour) + ":" \
            + _two(self._minute) + ":" + _two(self._second)
        if self._microsecond:
            frac = str(self._microsecond)
            while len(frac) < 6:
                frac = "0" + frac
            out = out + "." + frac
        off = self.utcoffset()
        if off is not None:
            total = int(off.total_seconds())
            sign = "+" if total >= 0 else "-"
            total = abs(total)
            out = out + sign + _two(total // 3600) + ":" \
                + _two((total % 3600) // 60)
        return out

    def _micro(self):
        """The whole value as microseconds since the epoch of ordinal 1."""
        return (self.toordinal() * 86400000000
                + self._hour * 3600000000 + self._minute * 60000000
                + self._second * 1000000 + self._microsecond)

    def _key(self):
        return self._micro()

    def __eq__(self, other):
        if isinstance(other, datetime):
            return self._micro() == other._micro()
        return NotImplemented

    def __ne__(self, other):
        got = self.__eq__(other)
        return got if got is NotImplemented else not got

    def __hash__(self):
        return hash(self._micro())

    def __add__(self, other):
        if isinstance(other, timedelta):
            return _from_micro(self._micro() + other._micro, self._tzinfo,
                               self._fold)
        return NotImplemented

    def __sub__(self, other):
        if isinstance(other, timedelta):
            return _from_micro(self._micro() - other._micro, self._tzinfo,
                               self._fold)
        if isinstance(other, datetime):
            return _delta(self._micro() - other._micro())
        return NotImplemented

    def __str__(self):
        return self.isoformat(" ")

    def __repr__(self):
        return "datetime.datetime(" + str(self._year) + ", " \
            + str(self._month) + ", " + str(self._day) + ", " \
            + str(self._hour) + ", " + str(self._minute) + ")"

    @staticmethod
    def combine(d, t):
        return datetime(d.year, d.month, d.day, t.hour, t.minute, t.second,
                        t.microsecond, t.tzinfo)

    @staticmethod
    def fromisoformat(text):
        y, m, d = int(text[0:4]), int(text[5:7]), int(text[8:10])
        if len(text) <= 10:
            return datetime(y, m, d)
        return datetime(y, m, d, int(text[11:13]), int(text[14:16]),
                        int(text[17:19]) if len(text) > 18 else 0)


def _from_micro(micro, tzinfo, fold):
    days = micro // 86400000000
    rest = micro % 86400000000
    y, m, d = _from_ordinal(days)
    return datetime(y, m, d, rest // 3600000000,
                    (rest % 3600000000) // 60000000,
                    (rest % 60000000) // 1000000, rest % 1000000, tzinfo, fold)
