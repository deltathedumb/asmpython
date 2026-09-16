"""Basic statistics: averages, spread, and the normal distribution.

COVERAGE: `mean`, `fmean` (with `weights=`), `geometric_mean`,
`harmonic_mean` (with `weights=`), `median`, `median_low`, `median_high`,
`median_grouped`, `mode`, `multimode`, `pvariance`, `variance`, `pstdev`,
`stdev`, `quantiles` (`exclusive` and `inclusive`), `covariance`,
`correlation` (`linear` and `ranked`), `linear_regression` (with
`proportional=`), and `NormalDist` -- construction, `.mean`/`.stdev`/
`.variance`/`.median`/`.mode`, `.pdf`, `.cdf`, `.inv_cdf`, `.quantiles`,
`.zscore`, `.samples` (seeded, via the bundled `random`), `.from_samples`,
`+ - *` (a constant or another `NormalDist`) `/` (a constant), `==`, `!=`,
`hash`, `repr`. `StatisticsError`, CPython's own exception for every empty-
or too-short-input case below.

**THE EXACTNESS IS THE POINT, and it is why `fractions` had to exist
first.** `mean([0.1] * 10)` summed as an ordinary float loop (`total = 0.0;
for x in data: total += x; return total / len(data)`) is
`0.09999999999999999` -- one bit short of `0.1` -- because `0.1` has no
exact binary value and ten roundings do not cancel. CPython's real `mean`
never takes that loop: every value is converted to its EXACT `(numerator,
denominator)` pair (`float.as_integer_ratio`, exact by construction) and
summed as a `Fraction`, which has no rounding error at all, and only the
FINAL division back to `float` rounds -- once. `mean([0.1] * 10)` is `0.1`
exactly here for the same reason -- checked against CPython, which agrees
that a plain float loop gets this one wrong. `_sum` and `_ss` below are
that machinery, ported from CPython's own private helpers of the same name;
`variance`, `pvariance`,
`stdev`, `pstdev`, `harmonic_mean` and `NormalDist.from_samples` all go
through them, and `stdev`/`pstdev` finish with `_float_sqrt_of_frac` --
CPython's correctly-rounded square root of a big exact rational (`math.isqrt`
on a rescaled numerator, round-to-odd) -- rather than `sqrt(float(ss))`,
which rounds TWICE (once converting the exact sum of squares to a `float`,
once taking its square root) where the real answer rounds once and can
therefore land one bit away from the double-rounded one. `variance`/
`pvariance`/`stdev`/`pstdev` were all checked against CPython directly,
including on data with no exact `float` representation of its own mean.

`fmean`, `covariance`, `correlation` and `linear_regression` are exact in
the same SPIRIT but not the same MACHINERY: CPython computes them with
`math.fsum` and `math.sumprod`, which round once by tracking the sum to
extended precision internally rather than through `Fraction`. Neither is a
native call here (`modules.py`'s `_MATH` table has no `fsum`/`sumprod`), so
`_fsum`/`_sumprod` below reach the SAME single-rounding answer by the
`Fraction` route instead -- summing each value's exact ratio, and rounding
back to `float` only once at the end. Checked against `math.fsum`/
`math.sumprod` directly (`scratchpad`-style sweep, thousands of cases
including the `[1e50, 1, -1e50] * 1000` shape that defeats a naive running
sum): zero mismatches. `_sumprod` also preserves an EXACT `int` result when
every input is an `int`, matching `math.sumprod`'s own int/int shortcut,
which is what `linear_regression`'s `proportional=True` path relies on
(`sxy = _sumprod(x, y) + 0.0`, the `+ 0.0` being CPython's own spelling for
"coerce this exact value to `float` with one correct rounding").

`NormalDist.inv_cdf` is `_normal_dist_inv_cdf`, CPython's own rational
(Wichura's AS241) approximation, ported coefficient-for-coefficient --
`sqrt`, `log` and `fabs` are the only math it needs and all three are
native. Checked against CPython (3,000+ random `p` in `(0, 1)`, both
`mu=0, sigma=1` and `mu=3.2, sigma=1.7`, AND against the accelerated
`_statistics` C extension CPython itself loads when present): ZERO
mismatches, to the bit. `.samples` is `inv_cdf` fed by the bundled
`random.Random(seed).random()`, so its determinism rides on `random`'s own
already-verified bit-exactness -- checked directly against
`statistics.NormalDist(...).samples(n, seed=...)` for several seeds and it
agrees, digit for digit.

**Writing `quantiles` and `variance` found two compiler bugs, neither about
statistics.** `divmod(8, 4)` answered a pair of small integers that were
never 2 and 0 -- deterministic per program, wrong every time, because the
interpreter's `apy_divmod` pre-boxed its quotient and remainder into their
OWN handles and then wrapped THOSE HANDLES in a fresh tuple, so the tuple a
program unpacked held raw cell-table indices rather than the values
themselves (`_apy_dict_popitem`'s `h._value(d.popitem())`, right next to it,
already had the correct one-step shape). `quantiles(data, method='inclusive')`
was this module's first call to `divmod` that inspected both of its results,
which is how it turned up here -- and independently, the same run, in
`bundled/datetime.py`'s own `divmod`-heavy normalization, which landed the
fix (`objects/host.py`, plus a second, unrelated `__divmod__`/
`__rdivmod__` dispatch gap `timedelta` needed and this module does not) just
ahead of this one; both are commits (see `git log`) rather than one, and
this module's own test is what INDEPENDENTLY re-confirmed the fix rather
than what supplied it. The C runtime's `apy_divmod` builds its tuple the
ordinary way already and was never wrong, checked rather than touched. And a
generator expression WRITTEN INSIDE A FUNCTION, closing
over that function's own parameter or local, read the closed-over name back
as `None` once the generator actually ran -- `variance(data, xbar)`'s
`_ss`, in its first form, closed over `xbar` this way. A nested `def`, a
`lambda`, a list comprehension, and a generator expression at MODULE level
doing the identical capture were all checked and are all fine; only a
generator (genexpr sugar or an explicit nested `def` with `yield`) nested
inside a function and reading that function's own locals is affected. Not
fixed -- closure capture across generator suspension is a different kind of
problem than a library module -- but not needed either: `_ss` and
`covariance` below use list comprehensions instead, which `_sum`/`_sumprod`
were going to materialise into a list anyway.

A third: `multimode('')` -- an empty `Counter`, which `docs/STDLIB.md`
already covers as `class Counter(dict)` -- reads its own `if not counts:
return []` as unreachable and calls `max()` on nothing, because
`bool()`/`not` on a builtin-extending instance with no `__bool__` of its
own fell straight to asking a user-defined `__len__` (which `Counter`
does not write either, relying on the SAME "ask `held`" fallback `len()`
itself already has) and, finding neither, answered `True` unconditionally
-- `_apy_len` already asks `held` before walking the dunder; `__bool__`'s
fallback to `__len__` had not learned the same trick. Fixed in both
`Instance.__bool__` (`objects/host.py`) and `apy_truth`'s `APY_INST_K`
case (`objects/c/_inspect.py`), mirroring `apy_len`'s own "ask held before
the dunder walk" order in each.

NOT EXACT, and said so rather than left looking exact: `NormalDist.pdf` and
`.cdf` need `exp`/`sqrt` (native, exact) and, for `.cdf`, `erfc` -- which is
NOT a native call (`_MATH` has no `erf`/`erfc`) and CPython's own
`math.erf`/`erfc` are not the portable series-and-continued-fraction
fallback from `mathmodule.c` either: on this Linux build they call glibc's
`erf`/`erfc` DIRECTLY (checked: `math.erf(x) == libm.erf(x)` for 3,000
random `x`, zero mismatches). `_erfc` below IS that portable
series-and-continued-fraction algorithm, ported from `mathmodule.c` for
when glibc is unreachable -- and checked against `math.erfc` directly, it
disagrees in the last bit on roughly half of random inputs (a few times
`2**-53` relative, from the different operation order, not a wrong
algorithm). So `.cdf`'s answer is correct to about 1e-15 relative and NOT
guaranteed bit-identical to CPython's; `tests/stdlib/statistics.py` checks
it `round`ed, the same accommodation CPython's own docstrings make for
this exact function (`#doctest: +ELLIPSIS`, `round(x, 9)`). `.overlap`
(also `erfc`-based) and the whole `kde`/`kde_random` surface (`erfc`,
`cosh`, `asin`, `acos` -- none native) are past that same wall and are
REFUSED BY NAME rather than shipped inexact-and-silent.

NOT COVERED, deliberately: `Decimal` is not a supported input anywhere
(`decimal` is not yet built) -- every exactness claim above is for `int`,
`float` and `Fraction` data, which is what `_exact_ratio`/`_coerce`/
`_convert` below know about. `numbers.Real` is not consulted for the
single-value fast path in `harmonic_mean`; `isinstance(x, (int, float,
Fraction))` answers the same question for every type this module accepts.
`NormalDist.__getstate__`/`__setstate__` (pickling) are not written -- there
is no pickle module here to call them.
"""
import math
import random

from fractions import Fraction
from functools import reduce
from collections import Counter, namedtuple


class StatisticsError(ValueError):
    pass


## ── exact summation, the machinery `mean`/`variance`/... share ────────────
#
# Ported from CPython's own `statistics._sum`/`_ss`/`_exact_ratio`/
# `_coerce`/`_convert`, narrowed to the three numeric types this module
# supports (`int`, `float`, `Fraction` -- no `Decimal`). CPython's `_sum`
# groups terms by denominator in a dict before adding them as an
# optimisation; grouping cannot change the VALUE of an exact sum, only how
# fast it is reached, so this sums each term into one running `Fraction`
# directly and lands on the identical (unique, lowest-terms) answer.

def _exact_ratio(x):
    """`x` as an exact `(numerator, denominator)` pair -- `denominator` is
    `None` for a non-finite `float` (`nan`/`inf`), which every caller below
    treats as a signal to fall back to ordinary (non-exact) arithmetic for
    that one term, exactly as CPython's `_sum`/`_ss` do."""
    if isinstance(x, Fraction):
        return (x.numerator, x.denominator)
    if isinstance(x, bool):
        return (int(x), 1)
    if isinstance(x, int):
        return (x, 1)
    if isinstance(x, float):
        try:
            return x.as_integer_ratio()
        except (OverflowError, ValueError):
            return (x, None)
    raise TypeError("can't convert type '" + type(x).__name__
                    + "' to numerator/denominator")


def _coerce(T, S):
    """The result type of mixing `T` and `S` data, CPython's own rule."""
    if T is S:
        return T
    if S is int or S is bool:
        return T
    if T is int:
        return S
    if issubclass(S, T):
        return S
    if issubclass(T, S):
        return T
    if issubclass(T, int):
        return S
    if issubclass(S, int):
        return T
    if issubclass(T, Fraction) and issubclass(S, float):
        return S
    if issubclass(T, float) and issubclass(S, Fraction):
        return T
    raise TypeError("don't know how to coerce " + T.__name__
                    + " and " + S.__name__)


def _convert(value, T):
    """`value` (a `Fraction`, or a raw non-finite `float` -- see
    `_exact_ratio`) converted to `T`, CPython's own rule: an inexact `int`
    target falls back to `float` rather than truncating silently."""
    if type(value) is T:
        return value
    if issubclass(T, int) and value.denominator != 1:
        T = float
    return T(value)


def _sum(data):
    """`(type, exact sum, count)` -- see the module docstring."""
    data = list(data)
    count = 0
    types = set()
    total = Fraction(0)
    special = None
    for x in data:
        count += 1
        types.add(type(x))
        n, d = _exact_ratio(x)
        if d is None:
            special = n if special is None else special + n
        else:
            total = total + Fraction(n, d)
    if special is not None:
        total = special
    T = reduce(_coerce, types, int)
    return (T, total, count)


def _ss(data, c=None):
    """`(type, exact sum of squared deviations, mean used, count)`.

    With `c` given, the deviations are `(x - c) ** 2` summed exactly via
    `_sum` -- CPython's own shape. Without it, `sx`/`sxx` are accumulated
    directly and `ssd = (n*sxx - sx*sx) / n`, `c = sx/n` -- algebraically
    the same sum of squared deviations from the exact mean, and EXACT
    because `Fraction` arithmetic never rounds; the two routes agree to the
    bit; there is no "poor numeric properties" caveat here the way CPython's
    own comment carries for the float case, because this never touches a
    float until the final answer.
    """
    if c is not None:
        # A LIST COMPREHENSION, not a generator expression: a generator
        # expression defined inside a function and reading a free variable
        # from that function's own scope (here, `c`) reads it back as
        # `None` once the generator actually runs -- a real closure bug
        # (checked: a plain nested `def` or `lambda` doing the identical
        # capture is fine, and a generator expression at MODULE level
        # closing over a module-level name is fine too; only a generator
        # -- genexpr sugar or an explicit nested `def` with `yield` --
        # nested inside a function and reading THAT function's own locals
        # is affected). A list comprehension is eager and unaffected, and
        # `_sum` immediately materialises its argument into a list anyway,
        # so nothing here needed the laziness.
        T, ssd, count = _sum([(x - c) * (x - c) for x in data])
        return (T, ssd, c, count)
    data = list(data)
    count = 0
    types = set()
    sx = Fraction(0)
    sxx = Fraction(0)
    special = None
    for x in data:
        count += 1
        types.add(type(x))
        n, d = _exact_ratio(x)
        if d is None:
            special = n if special is None else special + n
        else:
            fn = Fraction(n, d)
            sx = sx + fn
            sxx = sxx + fn * fn
    if count == 0:
        ssd = c = Fraction(0)
    elif special is not None:
        ssd = c = special
    else:
        ssd = (count * sxx - sx * sx) / count
        c = sx / count
    T = reduce(_coerce, types, int)
    return (T, ssd, c, count)


def _fail_neg(values, errmsg):
    for x in values:
        if x < 0:
            raise StatisticsError(errmsg)
        yield x


## ── `fsum`/`sumprod`, by the same exact route ──────────────────────────────

def _fsum(values):
    """CPython's `math.fsum`: the correctly-rounded `float` sum, reached by
    summing every term's exact ratio and rounding back to `float` once --
    not natively available (`_MATH` has no `fsum`), so this is the
    `Fraction` route instead. See the module docstring for the sweep this
    was checked against."""
    total = Fraction(0)
    for x in values:
        fx = float(x)
        n, d = _exact_ratio(fx)
        if d is None:
            return fx
        total = total + Fraction(n, d)
    return float(total)


def _sumprod(xs, ys):
    """CPython's `math.sumprod`: `sum(x*y for x, y in zip(xs, ys))` with
    one correct rounding at the end -- an exact `int` when every input is
    an `int` (CPython's own shortcut), a correctly-rounded `float`
    otherwise."""
    total = Fraction(0)
    any_float = False
    for a, b in zip(xs, ys):
        if isinstance(a, float) or isinstance(b, float):
            any_float = True
        na, da = _exact_ratio(a)
        nb, db = _exact_ratio(b)
        if da is None or db is None:
            return float(a) * float(b)
        total = total + Fraction(na, da) * Fraction(nb, db)
    if any_float:
        return float(total)
    return int(total) if total.denominator == 1 else total


def _sqrtprod(x, y):
    """`sqrt(x * y)`, with CPython's own overflow/underflow rescue and a
    differential correction for the last bit -- ported directly."""
    h = math.sqrt(x * y)
    if not math.isfinite(h):
        if math.isinf(h) and not math.isinf(x) and not math.isinf(y):
            scale = 2.0 ** -512
            return _sqrtprod(scale * x, scale * y) / scale
        return h
    if not h:
        if x and y:
            scale = 2.0 ** 537
            return _sqrtprod(scale * x, scale * y) / scale
        return h
    d = _sumprod((x, h), (y, -h))
    return h + d / (2.0 * h)


def _rank(data, start):
    """Rank order, lowest first, ties averaged -- CPython's `_rank`,
    narrowed to what `correlation(method='ranked')` calls it with (no
    `key`/`reverse`/custom `ties`)."""
    indexed = sorted((v, i) for i, v in enumerate(data))
    n = len(indexed)
    result = [0.0] * n
    i = 0
    pos = start - 1
    while i < n:
        j = i
        while j < n and indexed[j][0] == indexed[i][0]:
            j += 1
        size = j - i
        rank = pos + (size + 1) / 2
        for k in range(i, j):
            result[indexed[k][1]] = rank
        pos += size
        i = j
    return result


## ── measures of central tendency ────────────────────────────────────────────

def mean(data):
    """The exact arithmetic mean, rounded once at the end."""
    T, total, n = _sum(data)
    if n < 1:
        raise StatisticsError('mean requires at least one data point')
    return _convert(total / n, T)


def fmean(data, weights=None):
    """`float(mean(data))`, by the `fsum`/`sumprod` route -- always a
    `float`, and faster than `mean` for data that already is."""
    if weights is None:
        data = list(data)
        n = len(data)
        if not n:
            raise StatisticsError('fmean requires at least one data point')
        return _fsum(data) / n
    if not isinstance(weights, (list, tuple)):
        weights = list(weights)
    data = list(data)
    if len(data) != len(weights):
        raise StatisticsError('data and weights must be the same length')
    num = _sumprod(data, weights)
    den = _fsum(weights)
    if not den:
        raise StatisticsError('sum of weights must be non-zero')
    return num / den


def geometric_mean(data):
    """`exp(fsum(log(x) for x in data) / n)`. Zero if the product is zero
    (unless the sum of logs is itself `inf`, which makes the answer `nan`);
    a `StatisticsError` for any negative value or an empty dataset."""
    data = list(data)
    n = len(data)
    if not n:
        raise StatisticsError('Must have a non-empty dataset')
    found_zero = False
    logs = []
    for x in data:
        if x > 0.0 or math.isnan(x):
            logs.append(math.log(x))
        elif x == 0.0:
            found_zero = True
        else:
            raise StatisticsError('No negative inputs allowed')
    total = _fsum(logs)
    if math.isnan(total):
        return math.nan
    if found_zero:
        return math.nan if total == math.inf else 0.0
    return math.exp(total / n)


def harmonic_mean(data, weights=None):
    """The reciprocal of the mean of the reciprocals -- weighted, if
    `weights` is given."""
    data = list(data)
    errmsg = 'harmonic mean does not support negative values'
    n = len(data)
    if n < 1:
        raise StatisticsError('harmonic_mean requires at least one data point')
    if n == 1 and weights is None:
        x = data[0]
        if isinstance(x, (int, float, Fraction)):
            if x < 0:
                raise StatisticsError(errmsg)
            return x
        raise TypeError('unsupported type')
    if weights is None:
        weights = [1] * n
        sum_weights = n
    else:
        weights = list(weights)
        if len(weights) != n:
            raise StatisticsError('Number of weights does not match data size')
        _, sum_weights, _ = _sum(_fail_neg(weights, errmsg))
    try:
        data = list(_fail_neg(data, errmsg))
        T, total, count = _sum(w / x if w else 0 for w, x in zip(weights, data))
    except ZeroDivisionError:
        return 0
    if total <= 0:
        raise StatisticsError('Weighted sum must be positive')
    return _convert(sum_weights / total, T)


def median(data):
    """The middle value, or the average of the two middle values."""
    data = sorted(data)
    n = len(data)
    if n == 0:
        raise StatisticsError('no median for empty data')
    if n % 2 == 1:
        return data[n // 2]
    i = n // 2
    return (data[i - 1] + data[i]) / 2


def median_low(data):
    """The middle value, or the SMALLER of the two middle values."""
    data = sorted(data)
    n = len(data)
    if n == 0:
        raise StatisticsError('no median for empty data')
    if n % 2 == 1:
        return data[n // 2]
    return data[n // 2 - 1]


def median_high(data):
    """The middle value, or the LARGER of the two middle values."""
    data = sorted(data)
    n = len(data)
    if n == 0:
        raise StatisticsError('no median for empty data')
    return data[n // 2]


def median_grouped(data, interval=1.0):
    """The 50th percentile of data grouped into `interval`-wide bins
    centred on each value -- CPython's interpolation formula, ported."""
    data = sorted(data)
    n = len(data)
    if not n:
        raise StatisticsError('no median for empty data')
    x = data[n // 2]
    i = _bisect_left(data, x)
    j = _bisect_right(data, x, i)
    try:
        interval = float(interval)
        x = float(x)
    except ValueError:
        raise TypeError('Value cannot be converted to a float')
    L = x - interval / 2.0
    cf = i
    f = j - i
    return L + interval * (n / 2 - cf) / f


def _bisect_left(a, x, lo=0):
    hi = len(a)
    while lo < hi:
        mid = (lo + hi) // 2
        if a[mid] < x:
            lo = mid + 1
        else:
            hi = mid
    return lo


def _bisect_right(a, x, lo=0):
    hi = len(a)
    while lo < hi:
        mid = (lo + hi) // 2
        if x < a[mid]:
            hi = mid
        else:
            lo = mid + 1
    return lo


def mode(data):
    """The single most common value -- ties broken by which was seen
    FIRST, `Counter.most_common`'s own rule."""
    pairs = Counter(data).most_common(1)
    if not pairs:
        raise StatisticsError('no mode for empty data')
    return pairs[0][0]


def multimode(data):
    """Every value tied for most common -- `[]` for empty data, never an
    error (unlike `mode`)."""
    counts = Counter(data)
    if not counts:
        return []
    maxcount = max(counts.values())
    return [value for value, count in counts.items() if count == maxcount]


## ── measures of spread ──────────────────────────────────────────────────────

def variance(data, xbar=None):
    """The exact sample variance (divides by `n - 1`), rounded once."""
    T, ss, c, n = _ss(data, xbar)
    if n < 2:
        raise StatisticsError('variance requires at least two data points')
    return _convert(ss / (n - 1), T)


def pvariance(data, mu=None):
    """The exact population variance (divides by `n`), rounded once."""
    T, ss, c, n = _ss(data, mu)
    if n < 1:
        raise StatisticsError('pvariance requires at least one data point')
    return _convert(ss / n, T)


def stdev(data, xbar=None):
    """`sqrt(variance(data))`, correctly rounded from the exact ratio --
    NOT `sqrt(float(variance(data)))`, which would round twice."""
    T, ss, c, n = _ss(data, xbar)
    if n < 2:
        raise StatisticsError('stdev requires at least two data points')
    mss = ss / (n - 1)
    return _float_sqrt_of_frac(mss.numerator, mss.denominator)


def pstdev(data, mu=None):
    """`sqrt(pvariance(data))`, correctly rounded -- see `stdev`."""
    T, ss, c, n = _ss(data, mu)
    if n < 1:
        raise StatisticsError('pstdev requires at least one data point')
    mss = ss / n
    return _float_sqrt_of_frac(mss.numerator, mss.denominator)


def _integer_sqrt_of_frac_rto(n, m):
    """`round(sqrt(n / m))` for INTEGER `n`, `m`, using round-to-odd so the
    caller's later rounding step sees whether the true root was exact."""
    a = math.isqrt(n // m)
    flag = 1 if a * a * m != n else 0
    return a | flag


_SQRT_BIT_WIDTH = 2 * 53 + 3  # `2 * sys.float_info.mant_dig + 3`, hardcoded
                              # -- `sys.float_info` is not in `_SYS`.


def _float_sqrt_of_frac(n, m):
    """`sqrt(n / m)` as a correctly-rounded `float` -- CPython's own
    algorithm, via `math.isqrt` on a scaled-up numerator or denominator so
    the integer root carries enough bits to round the double correctly."""
    q = (n.bit_length() - m.bit_length() - _SQRT_BIT_WIDTH) // 2
    if q >= 0:
        numerator = _integer_sqrt_of_frac_rto(n, m << 2 * q) << q
        denominator = 1
    else:
        numerator = _integer_sqrt_of_frac_rto(n << -2 * q, m)
        denominator = 1 << -q
    return numerator / denominator


def _mean_stdev(data):
    """The mean and sample stdev, both `float`, in one pass -- what
    `NormalDist.from_samples` needs."""
    T, ss, xbar, n = _ss(data)
    if n < 2:
        raise StatisticsError('stdev requires at least two data points')
    mss = ss / (n - 1)
    try:
        return float(xbar), _float_sqrt_of_frac(mss.numerator, mss.denominator)
    except AttributeError:
        return float(xbar), float(xbar) / float(ss)


## ── two-input statistics ────────────────────────────────────────────────────

def covariance(x, y):
    """The sample covariance of `x` and `y`."""
    n = len(x)
    if len(y) != n:
        raise StatisticsError(
            'covariance requires that both inputs have same number of data points')
    if n < 2:
        raise StatisticsError('covariance requires at least two data points')
    xbar = _fsum(x) / n
    ybar = _fsum(y) / n
    # List comprehensions, not generator expressions -- see `_ss`'s
    # comment on the closure bug this sidesteps.
    sxy = _sumprod([xi - xbar for xi in x], [yi - ybar for yi in y])
    return sxy / (n - 1)


def correlation(x, y, method='linear'):
    """Pearson's `r` (`method='linear'`, the default) or Spearman's
    rank correlation (`method='ranked'`)."""
    n = len(x)
    if len(y) != n:
        raise StatisticsError(
            'correlation requires that both inputs have same number of data points')
    if n < 2:
        raise StatisticsError('correlation requires at least two data points')
    if method not in ('linear', 'ranked'):
        raise ValueError('Unknown method: ' + repr(method))
    if method == 'ranked':
        start = (n - 1) / -2
        x = _rank(x, start)
        y = _rank(y, start)
    else:
        xbar = _fsum(x) / n
        ybar = _fsum(y) / n
        x = [xi - xbar for xi in x]
        y = [yi - ybar for yi in y]
    sxy = _sumprod(x, y)
    sxx = _sumprod(x, x)
    syy = _sumprod(y, y)
    try:
        return sxy / _sqrtprod(sxx, syy)
    except ZeroDivisionError:
        raise StatisticsError('at least one of the inputs is constant')


LinearRegression = namedtuple('LinearRegression', ('slope', 'intercept'))


def linear_regression(x, y, proportional=False):
    """Ordinary-least-squares slope and intercept, as a
    `LinearRegression(slope=..., intercept=...)`."""
    n = len(x)
    if len(y) != n:
        raise StatisticsError(
            'linear regression requires that both inputs have same number '
            'of data points')
    if n < 2:
        raise StatisticsError('linear regression requires at least two data points')
    if not proportional:
        xbar = _fsum(x) / n
        ybar = _fsum(y) / n
        x = [xi - xbar for xi in x]
        y = [yi - ybar for yi in y]
    sxy = _sumprod(x, y) + 0.0
    sxx = _sumprod(x, x)
    try:
        slope = sxy / sxx
    except ZeroDivisionError:
        raise StatisticsError('x is constant')
    intercept = 0.0 if proportional else ybar - slope * xbar
    return LinearRegression(slope=slope, intercept=intercept)


## ── quantiles ────────────────────────────────────────────────────────────

def quantiles(data, n=4, method='exclusive'):
    """`n - 1` cut points splitting `data` into `n` equal-probability
    intervals -- `method='exclusive'` (the default, for a SAMPLE) or
    `'inclusive'` (for data covering the whole population)."""
    if n < 1:
        raise StatisticsError('n must be at least 1')
    data = sorted(data)
    ld = len(data)
    if ld < 2:
        if ld == 1:
            return data * (n - 1)
        raise StatisticsError('must have at least one data point')
    if method == 'inclusive':
        m = ld - 1
        result = []
        for i in range(1, n):
            j, delta = divmod(i * m, n)
            interpolated = (data[j] * (n - delta) + data[j + 1] * delta) / n
            result.append(interpolated)
        return result
    if method == 'exclusive':
        m = ld + 1
        result = []
        for i in range(1, n):
            j = i * m // n
            j = 1 if j < 1 else (ld - 1 if j > ld - 1 else j)
            delta = i * m - j * n
            interpolated = (data[j - 1] * (n - delta) + data[j] * delta) / n
            result.append(interpolated)
        return result
    raise ValueError('Unknown method: ' + repr(method))


## ── the normal distribution ─────────────────────────────────────────────

_SQRT2 = math.sqrt(2.0)

# CPython's own portable erf/erfc -- see the module docstring for why this
# exists (no native `erf`/`erfc`) and why it is NOT bit-identical to
# CPython's answer (which calls glibc directly on this platform).
_ERF_SERIES_CUTOFF = 1.5
_ERF_SERIES_TERMS = 25
_ERFC_CONTFRAC_CUTOFF = 30.0
_ERFC_CONTFRAC_TERMS = 50
_SQRTPI = 1.772453850905516027298167483341145182798


def _erf_series(x):
    x2 = x * x
    acc = 0.0
    fk = _ERF_SERIES_TERMS + 0.5
    for _ in range(_ERF_SERIES_TERMS):
        acc = 2.0 + x2 * acc / fk
        fk -= 1.0
    return acc * x * math.exp(-x2) / _SQRTPI


def _erfc_contfrac(x):
    if x >= _ERFC_CONTFRAC_CUTOFF:
        return 0.0
    x2 = x * x
    a = 0.0
    da = 0.5
    p = 1.0
    p_last = 0.0
    q = da + x2
    q_last = 1.0
    for _ in range(_ERFC_CONTFRAC_TERMS):
        a += da
        da += 2.0
        b = da + x2
        p, p_last = b * p - a * p_last, p
        q, q_last = b * q - a * q_last, q
    return p / q * x * math.exp(-x2) / _SQRTPI


def _erfc(x):
    if x != x:
        return x
    absx = math.fabs(x)
    if absx < _ERF_SERIES_CUTOFF:
        return 1.0 - _erf_series(x)
    cf = _erfc_contfrac(absx)
    return cf if x > 0.0 else 2.0 - cf


def _normal_dist_inv_cdf(p, mu, sigma):
    """Wichura's AS241 rational approximation to the inverse normal CDF --
    CPython's `_normal_dist_inv_cdf`, ported coefficient-for-coefficient.
    See the module docstring for how thoroughly this was checked."""
    q = p - 0.5
    if math.fabs(q) <= 0.425:
        r = 0.180625 - q * q
        num = (((((((2.5090809287301226727e+3 * r +
                     3.3430575583588128105e+4) * r +
                     6.7265770927008700853e+4) * r +
                     4.5921953931549871457e+4) * r +
                     1.3731693765509461125e+4) * r +
                     1.9715909503065514427e+3) * r +
                     1.3314166789178437745e+2) * r +
                     3.3871328727963666080e+0) * q
        den = (((((((5.2264952788528545610e+3 * r +
                     2.8729085735721942674e+4) * r +
                     3.9307895800092710610e+4) * r +
                     2.1213794301586595867e+4) * r +
                     5.3941960214247511077e+3) * r +
                     6.8718700749205790830e+2) * r +
                     4.2313330701600911252e+1) * r +
                     1.0)
        x = num / den
        return mu + (x * sigma)
    r = p if q <= 0.0 else 1.0 - p
    r = math.sqrt(-math.log(r))
    if r <= 5.0:
        r = r - 1.6
        num = (((((((7.7454501427834140764e-4 * r +
                     2.2723844989269184583e-2) * r +
                     2.4178072517745061177e-1) * r +
                     1.2704582524523683826e+0) * r +
                     3.6478483247632046050e+0) * r +
                     5.7694972214606914055e+0) * r +
                     4.6303378461565452959e+0) * r +
                     1.4234371107496835773e+0)
        den = (((((((1.0507500716444168432e-9 * r +
                     5.4759380849953449460e-4) * r +
                     1.5198666563616457197e-2) * r +
                     1.4810397642748007459e-1) * r +
                     6.8976733498510000455e-1) * r +
                     1.6763848301838038494e+0) * r +
                     2.0531916266377588219e+0) * r +
                     1.0)
    else:
        r = r - 5.0
        num = (((((((2.0103343992922881327e-7 * r +
                     2.7115555687434875782e-5) * r +
                     1.2426609473880784386e-3) * r +
                     2.6532189526576123093e-2) * r +
                     2.9656057182850489123e-1) * r +
                     1.7848265399172913358e+0) * r +
                     5.4637849111641143699e+0) * r +
                     6.6579046435011037772e+0)
        den = (((((((2.0442631033899397856e-15 * r +
                     1.4215117583164458887e-7) * r +
                     1.8463183175100546818e-5) * r +
                     7.8686913114561325910e-4) * r +
                     1.4875361290850614852e-2) * r +
                     1.3692988092273580531e-1) * r +
                     5.9983220655588793769e-1) * r +
                     1.0)
    x = num / den
    if q < 0.0:
        x = -x
    return mu + (x * sigma)


class NormalDist:
    """A normal (Gaussian) distribution, parameterised by `mu` and
    `sigma`."""

    def __init__(self, mu=0.0, sigma=1.0):
        if sigma < 0.0:
            raise StatisticsError('sigma must be non-negative')
        self._mu = float(mu)
        self._sigma = float(sigma)

    @classmethod
    def from_samples(cls, data):
        """A `NormalDist` fit to `data`'s mean and sample stdev."""
        mu, sigma = _mean_stdev(data)
        return cls(mu, sigma)

    def samples(self, n, seed=None):
        """`n` random draws -- deterministic when `seed` is given, via the
        bundled `random.Random`."""
        rnd = random.random if seed is None else random.Random(seed).random
        mu = self._mu
        sigma = self._sigma
        out = []
        for _ in range(n):
            out.append(_normal_dist_inv_cdf(rnd(), mu, sigma))
        return out

    def pdf(self, x):
        """Probability DENSITY at `x`."""
        variance = self._sigma * self._sigma
        if not variance:
            raise StatisticsError('pdf() not defined when sigma is zero')
        diff = x - self._mu
        return math.exp(diff * diff / (-2.0 * variance)) / math.sqrt(math.tau * variance)

    def cdf(self, x):
        """`P(X <= x)`. See the module docstring: correct to ~1e-15
        relative, not guaranteed bit-identical to CPython (glibc `erfc`)."""
        if not self._sigma:
            raise StatisticsError('cdf() not defined when sigma is zero')
        return 0.5 * _erfc((self._mu - x) / (self._sigma * _SQRT2))

    def inv_cdf(self, p):
        """The value `x` such that `cdf(x) == p` -- the quantile function."""
        if p <= 0.0 or p >= 1.0:
            raise StatisticsError('p must be in the range 0.0 < p < 1.0')
        return _normal_dist_inv_cdf(p, self._mu, self._sigma)

    def quantiles(self, n=4):
        """`n - 1` cut points splitting this distribution into `n`
        equal-probability intervals."""
        return [self.inv_cdf(i / n) for i in range(1, n)]

    def zscore(self, x):
        """`(x - mean) / stdev`."""
        if not self._sigma:
            raise StatisticsError('zscore() not defined when sigma is zero')
        return (x - self._mu) / self._sigma

    @property
    def mean(self):
        return self._mu

    @property
    def median(self):
        return self._mu

    @property
    def mode(self):
        return self._mu

    @property
    def stdev(self):
        return self._sigma

    @property
    def variance(self):
        return self._sigma * self._sigma

    def __add__(self, other):
        if isinstance(other, NormalDist):
            return NormalDist(self._mu + other._mu,
                              math.hypot(self._sigma, other._sigma))
        return NormalDist(self._mu + other, self._sigma)

    def __radd__(self, other):
        return self.__add__(other)

    def __sub__(self, other):
        if isinstance(other, NormalDist):
            return NormalDist(self._mu - other._mu,
                              math.hypot(self._sigma, other._sigma))
        return NormalDist(self._mu - other, self._sigma)

    def __rsub__(self, other):
        return -(self - other)

    def __mul__(self, other):
        return NormalDist(self._mu * other, self._sigma * math.fabs(other))

    def __rmul__(self, other):
        return self.__mul__(other)

    def __truediv__(self, other):
        return NormalDist(self._mu / other, self._sigma / math.fabs(other))

    def __pos__(self):
        return NormalDist(self._mu, self._sigma)

    def __neg__(self):
        return NormalDist(-self._mu, self._sigma)

    def __eq__(self, other):
        if not isinstance(other, NormalDist):
            return NotImplemented
        return self._mu == other._mu and self._sigma == other._sigma

    def __ne__(self, other):
        got = self.__eq__(other)
        return got if got is NotImplemented else not got

    def __hash__(self):
        return hash((self._mu, self._sigma))

    def __repr__(self):
        return ('NormalDist(mu=' + repr(self._mu) + ', sigma='
                + repr(self._sigma) + ')')
