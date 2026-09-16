"""Random number generation -- CPython's own Mersenne Twister, by hand.

COVERAGE: `Random` with `seed`, `random`, `getrandbits`, `getstate`/
`setstate`, `randrange`, `randint`, `choice`, `choices` (weights and
cum_weights), `shuffle`, `sample` (the pool strategy for a small population
and the set strategy for a large one), `uniform`, `gauss` -- and the
module-level bound functions CPython exports off one shared instance
(`random.seed`, `random.random`, `random.randint`, ...), which is how real
code almost always uses this module.

THE CORE IS THE POINT. CPython's `random.Random` is a thin Python class over
`_random.Random`, a C extension implementing MT19937 with a specific
seeding scheme -- `init_genrand`, then `init_by_array` for an integer seed,
then `genrand_uint32` with the standard tempering. There is no C extension
here, so all four are written out as plain Python: a 624-word state array,
an index, the twist, and the temper. Verified bit-for-bit against CPython's
own `_random.Random` for `random()` and `getrandbits()` across positive,
negative, zero, and multi-word (>64-bit) integer seeds before anything else
in this module was written -- a single wrong constant in the twist or the
temper makes every output wrong from the first draw, so the core had to be
right before there was a module to build on it.

Every method below reproduces CPython's `Lib/random.py` ALGORITHM, not just
its output type: `_randbelow` is rejection sampling via `getrandbits`
exactly as CPython does it (so the number of underlying draws for a given
bound matches), `shuffle` is Fisher-Yates from the end, `sample` picks the
pool or the set strategy by the same `setsize` threshold CPython computes,
and `choices` walks `bisect` over an accumulated weight list the same way.
Matching the ALGORITHM is what makes the OUTPUT SEQUENCE match after a
given seed -- a `randint` that draws a different number of bits per call
would desync from CPython on the very next call, even if any one number it
returned were in range.

NOT HERE: `seed` accepts only `None` (a fixed fallback, not OS entropy --
there is no bundled entropy source yet, see tier 5 in docs/STDLIB.md) and
`int` (the exact, verified path). `seed(str)`, `seed(bytes)` and
`seed(float)` are REFUSED BY NAME: CPython's version-2 string/bytes seeding
hashes through `sha512`, which is not a bundled module, and float seeding
goes through CPython's object `hash()` on the float rather than through the
integer path, neither of which this module fakes. Also not here:
`triangular`, `normalvariate`, `lognormvariate`, `expovariate`,
`vonmisesvariate`, `gammavariate`, `betavariate`, `paretovariate`,
`weibullvariate`, `binomialvariate`, `randbytes`, `SystemRandom`, and
`sample`'s `counts=` parameter (it is implemented but exercises the same
`_bisect`/`_accumulate` machinery as `choices`, so it is not separately
covered by the test).
"""
import math as _math

# ---------------------------------------------------------------------------
# MT19937, by hand. N/M/MATRIX_A/UPPER_MASK/LOWER_MASK and the tempering
# shifts and masks are the standard, unchanging constants the algorithm is
# defined by -- not a choice this module makes.

_N = 624
_M = 397
_MATRIX_A = 0x9908b0df
_UPPER_MASK = 0x80000000
_LOWER_MASK = 0x7fffffff
_MASK32 = 0xffffffff


def _init_genrand(seed):
    """The plain MT19937 state-array init from a single 32-bit seed."""
    mt = [0] * _N
    mt[0] = seed & _MASK32
    for i in range(1, _N):
        prev = mt[i - 1]
        mt[i] = (1812433253 * (prev ^ (prev >> 30)) + i) & _MASK32
    return mt


def _init_by_array(key):
    """The array-seeding variant MT19937 defines. CPython's `Random.seed(n)`
    for an integer `n` uses THIS, not `_init_genrand` directly -- `key` is
    `n`'s absolute value split into 32-bit little words."""
    mt = _init_genrand(19650218)
    i = 1
    j = 0
    key_length = len(key)
    k = _N if _N > key_length else key_length
    while k:
        prev = mt[i - 1]
        mt[i] = ((mt[i] ^ ((prev ^ (prev >> 30)) * 1664525)) + key[j] + j) & _MASK32
        i += 1
        j += 1
        if i >= _N:
            mt[0] = mt[_N - 1]
            i = 1
        if j >= key_length:
            j = 0
        k -= 1
    k = _N - 1
    while k:
        prev = mt[i - 1]
        mt[i] = ((mt[i] ^ ((prev ^ (prev >> 30)) * 1566083941)) - i) & _MASK32
        i += 1
        if i >= _N:
            mt[0] = mt[_N - 1]
            i = 1
        k -= 1
    mt[0] = 0x80000000
    return mt


def _seed_key(a):
    """`abs(n)` split into 32-bit words, least-significant first -- at least
    one word even for `n == 0`, exactly as CPython's `random_seed` builds
    the array it hands to `init_by_array`."""
    n = a if a >= 0 else -a
    if n == 0:
        return [0]
    key = []
    while n:
        key.append(n & _MASK32)
        n >>= 32
    return key


# A running total with no initial value -- what `itertools.accumulate`
# computes over weights and counts, written out rather than imported so this
# module does not depend on the un-covered half of `itertools`.
def _accumulate(seq):
    out = []
    total = 0
    seen = False
    for x in seq:
        total = x if not seen else total + x
        seen = True
        out.append(total)
    return out


# `bisect.bisect` / `bisect.bisect_right`, written out for the same reason:
# `bisect` is not a bundled module yet, and `choices`/`sample(counts=...)`
# need only this one function out of it.
def _bisect_right(a, x, lo, hi):
    while lo < hi:
        mid = (lo + hi) // 2
        if x < a[mid]:
            hi = mid
        else:
            lo = mid + 1
    return lo


def _index(x):
    """`operator.index(x)` narrowed to what this module needs: an existing
    `int`, or a rejection -- no `operator` module is bundled either."""
    if isinstance(x, int):
        return x
    if hasattr(x, "__index__"):
        return x.__index__()
    raise TypeError(
        "'" + type(x).__name__ + "' object cannot be interpreted as an integer")


_MISSING = object()
_TWOPI = _math.tau


class Random:
    """Mersenne Twister-backed random number generator, matching
    `random.Random` -- built ONLY from `random()` and `getrandbits()`,
    which is what makes the sequence of numbers it produces after a given
    seed match CPython's own, call for call.
    """

    VERSION = 3

    def __init__(self, x=None):
        self.seed(x)
        self.gauss_next = None

    # ---------------- core: seeding and raw generation ----------------

    def seed(self, a=None, version=2):
        if a is None:
            # No OS entropy source is bundled yet (tier 5). A fixed
            # fallback keeps construction from failing; every caller in
            # this project's own tests seeds explicitly afterward.
            a = 0
        elif isinstance(a, bool):
            a = int(a)
        elif isinstance(a, int):
            pass
        elif isinstance(a, (str, bytes, bytearray, float)):
            raise NotImplementedError(
                "random.seed() only supports None and int seeds here; "
                "str/bytes/bytearray/float seeding needs sha512 or "
                "float hash(), neither of which this module reimplements")
        else:
            raise TypeError(
                'The only supported seed types are:\n'
                'None, int, float, str, bytes, and bytearray.')

        self._mt = _init_by_array(_seed_key(a))
        self._mti = _N
        self.gauss_next = None

    def getstate(self):
        internal = tuple(self._mt) + (self._mti,)
        return (self.VERSION, internal, self.gauss_next)

    def setstate(self, state):
        version, internal, gauss_next = state
        if version != 3:
            raise ValueError(
                "state with version " + str(version) +
                " passed to Random.setstate() of version " + str(self.VERSION))
        self._mt = list(internal[:_N])
        self._mti = internal[_N]
        self.gauss_next = gauss_next

    def _genrand_uint32(self):
        mt = self._mt
        if self._mti >= _N:
            for kk in range(_N - _M):
                y = (mt[kk] & _UPPER_MASK) | (mt[kk + 1] & _LOWER_MASK)
                mt[kk] = mt[kk + _M] ^ (y >> 1) ^ (_MATRIX_A if y & 1 else 0)
            for kk in range(_N - _M, _N - 1):
                y = (mt[kk] & _UPPER_MASK) | (mt[kk + 1] & _LOWER_MASK)
                mt[kk] = mt[kk + _M - _N] ^ (y >> 1) ^ (_MATRIX_A if y & 1 else 0)
            y = (mt[_N - 1] & _UPPER_MASK) | (mt[0] & _LOWER_MASK)
            mt[_N - 1] = mt[_M - 1] ^ (y >> 1) ^ (_MATRIX_A if y & 1 else 0)
            self._mti = 0

        y = mt[self._mti]
        self._mti += 1
        y ^= (y >> 11)
        y ^= (y << 7) & 0x9d2c5680
        y ^= (y << 15) & 0xefc60000
        y ^= (y >> 18)
        return y & _MASK32

    def random(self):
        a = self._genrand_uint32() >> 5
        b = self._genrand_uint32() >> 6
        return (a * 67108864.0 + b) * (1.0 / 9007199254740992.0)

    def getrandbits(self, k):
        if k < 0:
            raise ValueError('number of bits must be non-negative')
        if k == 0:
            return 0
        if k <= 32:
            return self._genrand_uint32() >> (32 - k)
        words = (k - 1) // 32 + 1
        result = 0
        remaining = k
        for i in range(words):
            r = self._genrand_uint32()
            if remaining < 32:
                r >>= (32 - remaining)
            result |= r << (32 * i)
            remaining -= 32
        return result

    # ---------------- shared integer-selection primitive ----------------

    def _randbelow(self, n):
        """Return a random int in [0, n), by rejection sampling over
        `getrandbits` -- CPython's default `_randbelow_with_getrandbits`,
        which every method below goes through instead of touching
        `random()` or `getrandbits()` directly."""
        k = n.bit_length()
        r = self.getrandbits(k)
        while r >= n:
            r = self.getrandbits(k)
        return r

    # -------------------- integer methods --------------------

    def randrange(self, start, stop=_MISSING, step=_MISSING):
        istart = _index(start)
        if stop is _MISSING:
            if step is not _MISSING:
                raise TypeError("Missing a non-None stop argument")
            if istart > 0:
                return self._randbelow(istart)
            raise ValueError("empty range for randrange()")

        istop = _index(stop)
        width = istop - istart
        istep = 1 if step is _MISSING else _index(step)

        if istep == 1:
            if width > 0:
                return istart + self._randbelow(width)
            raise ValueError(
                "empty range in randrange(" + str(start) + ", " + str(stop) + ")")

        if istep > 0:
            n = (width + istep - 1) // istep
        elif istep < 0:
            n = (width + istep + 1) // istep
        else:
            raise ValueError("zero step for randrange()")
        if n <= 0:
            raise ValueError(
                "empty range in randrange(" + str(start) + ", " +
                str(stop) + ", " + str(step) + ")")
        return istart + istep * self._randbelow(n)

    def randint(self, a, b):
        a = _index(a)
        b = _index(b)
        if b < a:
            raise ValueError(
                "empty range in randint(" + str(a) + ", " + str(b) + ")")
        return a + self._randbelow(b - a + 1)

    # -------------------- sequence methods --------------------

    def choice(self, seq):
        if not len(seq):
            raise IndexError('Cannot choose from an empty sequence')
        return seq[self._randbelow(len(seq))]

    def shuffle(self, x):
        randbelow = self._randbelow
        i = len(x) - 1
        while i > 0:
            j = randbelow(i + 1)
            x[i], x[j] = x[j], x[i]
            i -= 1

    def sample(self, population, k, *, counts=None):
        if not (hasattr(population, "__len__") and hasattr(population, "__getitem__")):
            raise TypeError(
                "Population must be a sequence.  For dicts or sets, use sorted(d).")
        n = len(population)

        if counts is not None:
            cum_counts = _accumulate(counts)
            if len(cum_counts) != n:
                raise ValueError(
                    'The number of counts does not match the population')
            total = cum_counts[-1] if cum_counts else 0
            if not isinstance(total, int):
                raise TypeError('Counts must be integers')
            if total < 0:
                raise ValueError('Counts must be non-negative')
            selections = self.sample(range(total), k=k)
            return [population[_bisect_right(cum_counts, s, 0, len(cum_counts))]
                    for s in selections]

        randbelow = self._randbelow
        if not 0 <= k <= n:
            raise ValueError("Sample larger than population or is negative")
        result = [None] * k
        setsize = 21
        if k > 5:
            setsize += 4 ** _math.ceil(_math.log(k * 3) / _math.log(4))
        if n <= setsize:
            # An n-length list is smaller than a k-length set.
            pool = list(population)
            for i in range(k):
                j = randbelow(n - i)
                result[i] = pool[j]
                pool[j] = pool[n - i - 1]
        else:
            selected = set()
            for i in range(k):
                j = randbelow(n)
                while j in selected:
                    j = randbelow(n)
                selected.add(j)
                result[i] = population[j]
        return result

    def choices(self, population, weights=None, *, cum_weights=None, k=1):
        random = self.random
        n = len(population)
        if cum_weights is None:
            if weights is None:
                return [population[int(random() * n)] for _ in range(k)]
            cum_weights = _accumulate(weights)
        elif weights is not None:
            raise TypeError('Cannot specify both weights and cumulative weights')
        if len(cum_weights) != n:
            raise ValueError(
                'The number of weights does not match the population')
        total = cum_weights[-1] + 0.0
        if total <= 0.0:
            raise ValueError('Total of weights must be greater than zero')
        hi = n - 1
        return [population[_bisect_right(cum_weights, random() * total, 0, hi)]
                for _ in range(k)]

    # -------------------- real-valued distributions --------------------

    def uniform(self, a, b):
        return a + (b - a) * self.random()

    def gauss(self, mu=0.0, sigma=1.0):
        random = self.random
        z = self.gauss_next
        self.gauss_next = None
        if z is None:
            x2pi = random() * _TWOPI
            g2rad = _math.sqrt(-2.0 * _math.log(1.0 - random()))
            z = _math.cos(x2pi) * g2rad
            self.gauss_next = _math.sin(x2pi) * g2rad
        return mu + z * sigma


# ---------------------------------------------------------------------------
# One shared instance, and its methods bound at module level -- exactly how
# CPython exports `random.seed`, `random.random`, `random.randint`, and the
# rest, so ordinary code that never constructs a `Random` still works.

_inst = Random()
seed = _inst.seed
random = _inst.random
getrandbits = _inst.getrandbits
getstate = _inst.getstate
setstate = _inst.setstate
randrange = _inst.randrange
randint = _inst.randint
choice = _inst.choice
choices = _inst.choices
shuffle = _inst.shuffle
sample = _inst.sample
uniform = _inst.uniform
gauss = _inst.gauss
