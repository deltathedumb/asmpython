# COVERAGE: mean, fmean (with weights=), geometric_mean, harmonic_mean (with
# weights=), median, median_low, median_high, median_grouped, mode,
# multimode, pvariance, variance, pstdev, stdev, quantiles (exclusive and
# inclusive), covariance, correlation (linear and ranked), linear_regression
# (with proportional=), and NormalDist -- construction, .mean/.stdev/
# .variance/.median/.mode, .pdf, .cdf, .inv_cdf, .quantiles, .zscore,
# .samples (seeded), .from_samples, + - * / (constant or another NormalDist),
# ==, !=, hash, repr. StatisticsError on every empty-/too-short-input case.
# NOT covered: Decimal interop (not built), NormalDist.overlap, kde/
# kde_random (all need erf/cosh/asin/acos, none native here -- see
# bundled/statistics.py's docstring).
#
# Several assertions here deliberately use data that would show ordinary
# floating-point summation error if this module computed the naive way --
# mean/variance/stdev must still agree with CPython to the bit, because both
# sides compute them exactly via Fraction arithmetic rather than a running
# float total.
#
# Run under CPython and under uasm; the outputs must be identical, so
# every assertion below is written against the SPECIFICATION rather than
# against whatever uasm currently prints. NormalDist.pdf/.cdf are the
# one exception: .cdf goes through a hand-rolled erfc (see the module
# docstring for why) that is correct to ~1e-15 relative but not guaranteed
# bit-identical to CPython's glibc-backed erfc, so those two are printed
# rounded -- the same accommodation CPython's own docstrings make for them.
import statistics
from statistics import StatisticsError, NormalDist

# ── mean / fmean ─────────────────────────────────────────────────────────

print("== mean ==")
print(statistics.mean([1, 2, 3, 4, 4]))
print(statistics.mean([-1.0, 2.5, 3.25, 5.75]))
# The classic float-summation trap: a naive running total is off by 1 ULP.
print(statistics.mean([0.1] * 10))
print(statistics.mean([0.1] * 10) == 0.1)
print(statistics.mean([1e50, 1, -1e50] * 1000))       # naive sum() gives 0
print(statistics.mean([2, 2, 2]))                      # exact -> int
from fractions import Fraction as F
print(statistics.mean([F(3, 7), F(1, 21), F(5, 3), F(1, 3)]))
try:
    statistics.mean([])
except StatisticsError as e:
    print("StatisticsError", e)

print("== fmean ==")
print(statistics.fmean([3.5, 4.0, 5.25]))
print(statistics.fmean([0.1] * 10))
print(statistics.fmean([1, 2, 3, 4]))
print(statistics.fmean([40, 60], weights=[5, 30]))
try:
    statistics.fmean([])
except StatisticsError as e:
    print("StatisticsError", e)
try:
    statistics.fmean([1, 2], weights=[1])
except StatisticsError as e:
    print("StatisticsError", e)

print("== geometric_mean ==")
print(round(statistics.geometric_mean([54, 24, 36]), 9))
print(round(statistics.geometric_mean([1, 2, 3, 4, 5]), 9))
print(statistics.geometric_mean([0, 1, 2]))
try:
    statistics.geometric_mean([1, -2, 3])
except StatisticsError as e:
    print("StatisticsError")
try:
    statistics.geometric_mean([])
except StatisticsError as e:
    print("StatisticsError", e)

print("== harmonic_mean ==")
print(statistics.harmonic_mean([40, 60]))
print(statistics.harmonic_mean([40, 60], weights=[5, 30]))
print(statistics.harmonic_mean([2.5]))
print(statistics.harmonic_mean([3]))
try:
    statistics.harmonic_mean([])
except StatisticsError as e:
    print("StatisticsError", e)
try:
    statistics.harmonic_mean([1, -2])
except StatisticsError as e:
    print("StatisticsError", e)

# ── medians ──────────────────────────────────────────────────────────────

print("== median ==")
print(statistics.median([1, 3, 5]))
print(statistics.median([1, 3, 5, 7]))
print(statistics.median([5, 1, 3]))
print(statistics.median_low([1, 3, 5, 7]))
print(statistics.median_high([1, 3, 5, 7]))
print(statistics.median_grouped([2, 2, 3, 3, 3, 4]))
demographics = [25] * 172 + [35] * 484 + [45] * 387 + [55] * 22 + [65] * 6
print(statistics.median(demographics))
print(round(statistics.median_grouped(demographics, interval=10), 1))
for fn in (statistics.median, statistics.median_low, statistics.median_high,
          statistics.median_grouped):
    try:
        fn([])
    except StatisticsError as e:
        print("StatisticsError", e)

# ── mode / multimode ─────────────────────────────────────────────────────

print("== mode ==")
print(statistics.mode([1, 1, 2, 3, 3, 3, 3, 4]))
print(statistics.mode(["red", "blue", "blue", "red", "green", "red", "red"]))
print(statistics.mode(['red', 'red', 'green', 'blue', 'blue']))  # tie: first
try:
    statistics.mode([])
except StatisticsError as e:
    print("StatisticsError", e)

print("== multimode ==")
print(statistics.multimode('aabbbbbbbbcc'))
print(statistics.multimode('aabbbbccddddeeffffgg'))
print(statistics.multimode(''))
print(statistics.multimode([1, 1, 2, 2, 3]))

# ── spread ───────────────────────────────────────────────────────────────

print("== variance / pvariance ==")
data = [2.75, 1.75, 1.25, 0.25, 0.5, 1.25, 3.5]
print(statistics.variance(data))
m = statistics.mean(data)
print(statistics.variance(data, m))
data2 = [0.0, 0.25, 0.25, 1.25, 1.5, 1.75, 2.75, 3.25]
print(statistics.pvariance(data2))
mu = statistics.mean(data2)
print(statistics.pvariance(data2, mu))
print(statistics.variance([F(1, 6), F(1, 2), F(5, 3)]))
print(statistics.pvariance([F(1, 4), F(5, 4), F(1, 2)]))
# floating-point-summation-trap data again, through variance this time.
noisy = [1.0 + 1e-16 * i for i in range(20)]
print(statistics.variance(noisy) >= 0.0)  # exact algorithm never goes negative
try:
    statistics.variance([1])
except StatisticsError as e:
    print("StatisticsError", e)
try:
    statistics.pvariance([])
except StatisticsError as e:
    print("StatisticsError", e)

print("== stdev / pstdev ==")
print(statistics.stdev([1.5, 2.5, 2.5, 2.75, 3.25, 4.75]))
print(statistics.pstdev([1.5, 2.5, 2.5, 2.75, 3.25, 4.75]))
print(statistics.stdev([2.5, 3.25, 5.5, 11.25, 11.75]))
try:
    statistics.stdev([1])
except StatisticsError as e:
    print("StatisticsError", e)

# ── quantiles ────────────────────────────────────────────────────────────

print("== quantiles ==")
qdata = [1, 2, 3, 4, 5, 6, 7, 8, 9]
print(statistics.quantiles(qdata))
print(statistics.quantiles(qdata, n=10))
print(statistics.quantiles(qdata, n=4, method='inclusive'))
print(statistics.quantiles([5]))
print(statistics.quantiles([5], n=4))
try:
    statistics.quantiles([])
except StatisticsError as e:
    print("StatisticsError", e)
try:
    statistics.quantiles(qdata, n=0)
except StatisticsError as e:
    print("StatisticsError", e)
try:
    statistics.quantiles(qdata, method='bogus')
except ValueError as e:
    print("ValueError", e)

# ── two-input statistics ─────────────────────────────────────────────────

print("== covariance / correlation / linear_regression ==")
x = [1, 2, 3, 4, 5, 6, 7, 8, 9]
y = [1, 2, 3, 1, 2, 3, 1, 2, 3]
print(statistics.covariance(x, y))
z = [9, 8, 7, 6, 5, 4, 3, 2, 1]
print(statistics.covariance(x, z))
print(statistics.covariance(z, x))
print(statistics.correlation(x, x))
print(round(statistics.correlation(x, y), 9))
print(statistics.correlation(x, z))
print(round(statistics.correlation(x, y, method='ranked'), 9))
print(statistics.linear_regression(x, y))
print(statistics.linear_regression(x, y, proportional=True))
try:
    statistics.covariance([1, 2], [1, 2, 3])
except StatisticsError as e:
    print("StatisticsError", e)
try:
    statistics.covariance([1], [1])
except StatisticsError as e:
    print("StatisticsError", e)
try:
    statistics.correlation([1, 1, 1], [1, 2, 3])
except StatisticsError as e:
    print("StatisticsError", e)
try:
    statistics.linear_regression([1, 1, 1], [1, 2, 3], proportional=True)
except StatisticsError as e:
    print("StatisticsError", e)

# ── NormalDist ───────────────────────────────────────────────────────────

print("== NormalDist construction ==")
nd = NormalDist()
print(nd.mean, nd.stdev, nd.variance)
nd2 = NormalDist(5, 2)
print(nd2.mean, nd2.stdev, nd2.variance, nd2.median, nd2.mode)
print(repr(nd2))
try:
    NormalDist(0, -1)
except StatisticsError as e:
    print("StatisticsError", e)

print("== NormalDist.pdf ==")
# pdf uses only exp/sqrt/tau -- all native -- so this is compared exactly.
print(NormalDist(0, 1).pdf(0.0))
print(NormalDist(0, 1).pdf(1.0))
print(NormalDist(5, 2).pdf(5.0))
print(NormalDist(5, 2).pdf(7.0))
try:
    NormalDist(5, 0).pdf(5.0)
except StatisticsError as e:
    print("StatisticsError", e)

print("== NormalDist.cdf ==")
# cdf goes through a hand-rolled erfc -- see the module docstring -- so
# these are checked rounded rather than bit-for-bit.
print(round(NormalDist(0, 1).cdf(0.0), 9))
print(round(NormalDist(0, 1).cdf(1.0), 9))
print(round(NormalDist(0, 1).cdf(-1.0), 9))
print(round(NormalDist(0, 1).cdf(1.96), 9))
print(round(NormalDist(5, 2).cdf(5.0), 9))
print(round(NormalDist(5, 2).cdf(9.0), 9))
try:
    NormalDist(5, 0).cdf(5.0)
except StatisticsError as e:
    print("StatisticsError", e)

print("== NormalDist.inv_cdf ==")
# inv_cdf is CPython's own rational approximation, no erfc involved --
# checked bit-for-bit against CPython directly (see the module docstring),
# so these ARE compared exactly.
print(NormalDist(0, 1).inv_cdf(0.5))
print(NormalDist(0, 1).inv_cdf(0.975))
print(NormalDist(0, 1).inv_cdf(0.025))
print(NormalDist(5, 2).inv_cdf(0.5))
print(NormalDist(5, 2).inv_cdf(0.1))
print(NormalDist(5, 2).inv_cdf(0.9))
for bad in (0.0, 1.0, -0.1, 1.1):
    try:
        NormalDist().inv_cdf(bad)
    except StatisticsError as e:
        print("StatisticsError", e)
print(NormalDist(5, 2).quantiles())
print(NormalDist(0, 1).quantiles(n=10))

print("== NormalDist.zscore ==")
print(NormalDist(5, 2).zscore(9.0))
print(NormalDist(5, 2).zscore(5.0))
try:
    NormalDist(5, 0).zscore(9.0)
except StatisticsError as e:
    print("StatisticsError", e)

print("== NormalDist arithmetic ==")
a = NormalDist(2.4, 1.6)
b = NormalDist(3.2, 2.0)
print(a + b)
print(a + 3)
print(3 + a)
print(a - b)
print(a - 1)
print(a * 2)
print(2 * a)
print(a / 2)
print(+a)
print(-a)
print(a == NormalDist(2.4, 1.6))
print(a == b)
print(a != b)
print(a != NormalDist(2.4, 1.6))
print(a == 5)
print(hash(a) == hash(NormalDist(2.4, 1.6)))

print("== NormalDist.from_samples ==")
sample = [1, 2, 3, 4, 5, 6]
fitted = NormalDist.from_samples(sample)
print(fitted.mean, fitted.stdev)
print(fitted.mean == statistics.mean(sample))
print(fitted.stdev == statistics.stdev(sample))
try:
    NormalDist.from_samples([1])
except StatisticsError as e:
    print("StatisticsError", e)

print("== NormalDist.samples ==")
# Deterministic given a seed -- exercises the bundled random module's own
# already-verified bit-exactness (NormalDist.samples draws through
# random.Random(seed).random() and CPython's own inv_cdf formula).
s1 = NormalDist(0, 1).samples(8, seed=42)
print(s1)
s2 = NormalDist(0, 1).samples(8, seed=42)
print(s1 == s2)
print(NormalDist(100, 15).samples(5, seed=1234))
