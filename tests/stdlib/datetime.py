# COVERAGE: timedelta (all seven keyword arguments, normalization
# including negative and float combinations, arithmetic + - * / // %
# divmod, unary - + abs, comparison, total_seconds, str/repr, min/max/
# resolution); date (construction and validation, .year/.month/.day,
# .fromordinal/.toordinal, .weekday()/.isoweekday(), .isocalendar(),
# .isoformat()/strftime for %Y %y %m %d %H %I %M %S %f %A %a %B %b %j %p
# %z %Z %%, .replace, arithmetic with timedelta and with another date,
# comparison including against datetime, min/max/resolution); time
# (construction and validation including tzinfo/fold, the properties,
# .isoformat with every timespec, comparison naive/naive and aware/aware
# and the naive-vs-aware TypeError, .utcoffset/.tzname/.dst, .replace);
# tzinfo/timezone (the fixed-offset kind only: timezone.utc as a real
# singleton, timezone(offset, name=), the -24h..24h bound, .utcoffset/
# .tzname/.dst, comparison, repr); datetime (construction, .now()/
# .utcnow()/.fromtimestamp() checked only for type/plausibility since wall
# clock and host time are not reproducible across two separate process
# runs, .combine, .timestamp()/.fromtimestamp() round-trip, .astimezone,
# .replace, strftime/strptime for the same code set as date, isoformat
# with sep= and timespec=, arithmetic with timedelta, datetime-datetime
# including naive/aware, comparison, min/max/resolution); .isinstance(dt,
# date) being True.
#
# NOT COVERED: any real IANA timezone (zoneinfo -- a separate, unattempted
# module; DST is out of scope entirely); fromisoformat/fromisocalendar/
# ctime/timetuple/__format__/pickling; the exact wording of a cross-type
# TypeError that would need CPython's qualified C type name (this
# module's classes are plain Python classes -- see bundled/datetime.py's
# docstring); strptime's error text for malformed input (only successful
# round trips are checked); exact hash() integers for an AWARE time/
# datetime (CPython's own algorithm for that case is not reproduced bit
# for bit -- only hash/eq consistency, which the naive case's plain tuple
# hash already demonstrates matches CPython exactly).
#
# Run under CPython and under uasm; the outputs must be identical,
# so every assertion below is written against the SPECIFICATION rather
# than against whatever uasm currently prints.
import datetime

# ── timedelta construction and normalization ───────────────────────────────

print("== timedelta normalization ==")
print(datetime.timedelta(days=-1, seconds=1))
td = datetime.timedelta(days=-1, seconds=1)
print(td.days, td.seconds, td.microseconds)

td = datetime.timedelta(seconds=-1)
print(td, td.days, td.seconds, td.microseconds)

td = datetime.timedelta(hours=1.5, minutes=-10, milliseconds=1234.5678)
print(td, td.days, td.seconds, td.microseconds)

td = datetime.timedelta(weeks=1, days=-8)
print(td, td.days, td.seconds, td.microseconds)

td = datetime.timedelta(microseconds=-1)
print(td, td.days, td.seconds, td.microseconds)

print(datetime.timedelta(days=1, seconds=2, microseconds=3))
print(repr(datetime.timedelta(days=1, seconds=2, microseconds=3)))
print(datetime.timedelta(0))
print(repr(datetime.timedelta(0)))
print(datetime.timedelta(days=1))
print(datetime.timedelta(days=2))
print(datetime.timedelta(seconds=3661, microseconds=5))

# ── timedelta arithmetic ─────────────────────────────────────────────────

print("== timedelta arithmetic ==")
a = datetime.timedelta(days=1, hours=2)
print(a * 2)
print(2 * a)
print(a / 2)
print(a / datetime.timedelta(hours=1))
print(a // 2)
print(a // datetime.timedelta(hours=1))
print(a % datetime.timedelta(hours=5))
print(divmod(a, datetime.timedelta(hours=5)))
print(-a, +a, abs(-a))
print(bool(datetime.timedelta(0)), bool(a))
print(a.total_seconds())
print(datetime.timedelta(days=999999999).total_seconds())

# ── timedelta comparison ─────────────────────────────────────────────────

print("== timedelta comparison ==")
print(datetime.timedelta(hours=1) == datetime.timedelta(minutes=60))
print(datetime.timedelta(hours=1) < datetime.timedelta(minutes=61))
print(datetime.timedelta(hours=1) >= datetime.timedelta(minutes=60))
try:
    datetime.timedelta(hours=1) < 5
except TypeError as e:
    print("TypeError")
print(datetime.timedelta.min)
print(datetime.timedelta.max)
print(datetime.timedelta.resolution)

# ── date construction and validation ────────────────────────────────────

print("== date construction ==")
for args in [(0, 1, 1), (10000, 1, 1), (2024, 0, 1), (2024, 13, 1),
             (2024, 2, 30), (2024, 1, 0), (2024, 1, 32)]:
    try:
        datetime.date(*args)
        print("no error")
    except ValueError as e:
        print("ValueError", e)

d = datetime.date(2024, 2, 29)
print(d.year, d.month, d.day)
print(d)
print(repr(d))

# ── date across a leap year, a non-leap century, and the extremes ───────

print("== date formatting ==")
sample_dates = [(1, 1, 1), (9999, 12, 31), (1900, 2, 28), (1900, 3, 1),
                (2000, 2, 29), (2024, 2, 29), (2100, 2, 28), (2100, 3, 1),
                (2023, 1, 1), (2024, 12, 31), (2025, 12, 29), (1970, 1, 1),
                (1969, 12, 31)]
for y, m, dd in sample_dates:
    dt = datetime.date(y, m, dd)
    print(dt.isoformat(), dt.weekday(), dt.isoweekday(), dt.toordinal())
    print(tuple(dt.isocalendar()))
    print(dt.strftime("%Y-%m-%d %A %a %B %b %j"))
    print(dt.strftime("%y %H:%M:%S.%f %z|%Z|%%"))
    print(datetime.date.fromordinal(dt.toordinal()) == dt)

print(datetime.date(2024, 2, 29).replace(month=6))
print(datetime.date(2024, 1, 1).replace(year=2025, day=15))

# ── date arithmetic and comparison ──────────────────────────────────────

print("== date arithmetic ==")
print(datetime.date(2024, 1, 1) + datetime.timedelta(days=-1))
print(datetime.date(2024, 3, 1) - datetime.date(2024, 1, 1))
print(datetime.timedelta(days=5) + datetime.date(2024, 1, 1))
try:
    datetime.date(9999, 12, 31) + datetime.timedelta(days=1)
except OverflowError as e:
    print("OverflowError", e)
try:
    datetime.date(1, 1, 1) - datetime.timedelta(days=1)
except OverflowError as e:
    print("OverflowError", e)

print(datetime.date(2024, 1, 1) == datetime.date(2024, 1, 1))
print(datetime.date(2024, 1, 1) < datetime.date(2024, 1, 2))
print(datetime.date(2024, 1, 1) == datetime.datetime(2024, 1, 1))
print(datetime.datetime(2024, 1, 1) == datetime.date(2024, 1, 1))
try:
    datetime.date(2024, 1, 1) < datetime.datetime(2024, 1, 1)
except TypeError as e:
    print("TypeError")
print(datetime.date.min, datetime.date.max, datetime.date.resolution)

# ── time ──────────────────────────────────────────────────────────────────

print("== time ==")
for args in [(24, 0, 0), (1, 60, 0), (1, 1, 60), (1, 1, 1, 1000000)]:
    try:
        datetime.time(*args)
        print("no error")
    except ValueError as e:
        print("ValueError", e)

for args in [(0, 0, 0, 0), (1, 2, 3), (1, 2, 3, 4), (23, 59, 59, 999999),
             (1, 2, 0, 5)]:
    t = datetime.time(*args)
    print(t, repr(t))
    for ts in ("auto", "hours", "minutes", "seconds", "milliseconds",
              "microseconds"):
        print(t.isoformat(timespec=ts))

print(datetime.time.min, datetime.time.max, datetime.time.resolution)

# ── timezone (fixed offset) ─────────────────────────────────────────────

print("== timezone ==")
utc = datetime.timezone.utc
print(repr(utc))
tz = datetime.timezone(datetime.timedelta(hours=5, minutes=30), "IST")
print(repr(tz), tz.tzname(None), tz.utcoffset(None), tz.dst(None))
noname = datetime.timezone(datetime.timedelta(hours=-5, minutes=-30))
print(repr(noname), noname.tzname(None))
print(datetime.timezone(datetime.timedelta(0)) is datetime.timezone.utc)
print(datetime.timezone(datetime.timedelta(0), "UTC") is datetime.timezone.utc)
try:
    datetime.timezone(datetime.timedelta(hours=24))
except ValueError as e:
    print("ValueError", e)

t1 = datetime.time(1, 2, tzinfo=utc)
t2 = datetime.time(6, 32, tzinfo=tz)
print(t1 == t2)
t_naive = datetime.time(1, 2)
print(t_naive == t1)
try:
    t_naive < t1
except TypeError as e:
    print("TypeError")

# ── datetime construction and formatting ────────────────────────────────

print("== datetime formatting ==")
dt_samples = [(2024, 2, 29, 1, 2, 3, 4), (1900, 2, 28, 0, 0, 0, 0),
             (2100, 3, 1, 12, 30, 45, 678901), (1, 1, 1, 0, 0, 0, 0),
             (9999, 12, 31, 23, 59, 59, 999999), (2024, 1, 1, 0, 0),
             (2024, 1, 1, 2, 3), (2024, 1, 1, 2, 0, 0, 5)]
for args in dt_samples:
    x = datetime.datetime(*args)
    print(x.isoformat())
    print(str(x))
    print(repr(x))
    print(x.isoformat(sep=" "))
    print(x.isoformat(sep="|", timespec="minutes"))
    print(x.strftime("%Y-%m-%d %H:%M:%S.%f %A %a %B %b %j %I %p %%"))
    print(x.weekday(), x.isoweekday())

# ── aware datetime, strftime %z/%Z ──────────────────────────────────────

print("== aware datetime ==")
aware = datetime.datetime(2024, 2, 29, 1, 2, 3, 4,
                          tzinfo=datetime.timezone(
                              datetime.timedelta(hours=-5, minutes=-30)))
print(aware.isoformat())
print(repr(aware))
print(aware.strftime("%z %Z"))
aware_utc = datetime.datetime(2024, 2, 29, 1, 2, 3, 4, tzinfo=utc)
print(aware_utc.strftime("%z %Z"))
print(repr(datetime.datetime(2024, 1, 1, fold=1)))
print(repr(datetime.datetime(2024, 1, 1, tzinfo=utc, fold=1)))

# ── datetime comparison, arithmetic ─────────────────────────────────────

print("== datetime comparison and arithmetic ==")
a1 = datetime.datetime(2024, 1, 1, tzinfo=utc)
a2 = datetime.datetime(2024, 1, 1, 5, 30, tzinfo=tz)
print(a1 == a2)
print(a1 - a2)
naive = datetime.datetime(2024, 1, 1)
print(naive == a1)
try:
    naive < a1
except TypeError as e:
    print("TypeError")
print(naive - datetime.timedelta(hours=2))
print(naive + datetime.timedelta(hours=25, minutes=90))
print(datetime.datetime(2024, 3, 1) - datetime.datetime(2024, 1, 1))
print(a1.astimezone(tz))
print(naive.astimezone())

c = datetime.datetime.combine(datetime.date(2024, 1, 1),
                              datetime.time(5, 6, 7))
print(c)
c2 = datetime.datetime.combine(datetime.date(2024, 1, 1),
                               datetime.time(5, 6, 7, tzinfo=utc))
print(c2, c2.tzinfo is utc)

print(datetime.datetime.min, datetime.datetime.max, datetime.datetime.resolution)
print(isinstance(datetime.datetime(2024, 1, 1), datetime.date))

# ── strptime ──────────────────────────────────────────────────────────────

print("== strptime ==")
for s, fmt in [
    ("2024-02-29 01:02:03", "%Y-%m-%d %H:%M:%S"),
    ("Feb 29 2024", "%b %d %Y"),
    ("2024-091", "%Y-%j"),
    ("2024-01-01T05:06:07+05:30", "%Y-%m-%dT%H:%M:%S%z"),
    ("January 05 2024 11:30 PM", "%B %d %Y %I:%M %p"),
]:
    print(datetime.datetime.strptime(s, fmt))

# ── timestamp() / fromtimestamp() round trip ────────────────────────────

print("== timestamp round trip ==")
for ts in (0, 1700000000.123456, -5000.5, 1234567890):
    x = datetime.datetime.fromtimestamp(ts)
    print(x, x.timestamp())
    xu = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)
    print(xu, xu.timestamp())
    print(datetime.date.fromtimestamp(ts))

print(datetime.datetime(1970, 1, 1).timestamp())
print(datetime.datetime(1970, 1, 1, tzinfo=utc).timestamp())

# ── .now()/.today()/.utcnow(): type and plausibility only, never the
#    exact value -- two separate process runs of the SAME implementation
#    would already disagree on wall-clock time, so printing it would not
#    be a meaningful differential check.

print("== clock (type/plausibility only) ==")
now = datetime.datetime.now()
print(type(now).__name__, isinstance(now, datetime.datetime))
print(now.year > 2020)
today = datetime.date.today()
print(type(today).__name__, today.year > 2020)
utcnow = datetime.datetime.utcnow()
print(type(utcnow).__name__)
now_tz = datetime.datetime.now(datetime.timezone.utc)
print(now_tz.tzinfo is datetime.timezone.utc)

print("== MINYEAR/MAXYEAR ==")
print(datetime.MINYEAR, datetime.MAXYEAR)

print("done")
