"""`statistics`, as ordinary Python this compiler compiles.

Textbook definitions, written out. The one thing worth saying is which
variance: `stdev` is the SAMPLE standard deviation, dividing by n-1, and
`pstdev` the population one dividing by n. Picking the wrong one is a silently
plausible answer, which is exactly the kind of mistake writing the formula out
in the open prevents.
"""


class StatisticsError(ValueError):
    """What every function here raises for data it cannot answer about.

    A `ValueError` subclass, so a caller that catches the general kind still
    catches these -- which is the whole reason CPython made it one.
    """


def mean(data):
    """The arithmetic mean."""
    items = list(data)
    if not items:
        raise ValueError("mean requires at least one data point")
    return sum(items) / len(items)


def median(data):
    """The middle value, or the mean of the two middle values."""
    items = sorted(data)
    n = len(items)
    if not n:
        raise ValueError("no median for empty data")
    half = n // 2
    if n % 2 == 1:
        return items[half]
    return (items[half - 1] + items[half]) / 2


def median_low(data):
    """The lower of the two middle values, for an even count."""
    items = sorted(data)
    n = len(items)
    if not n:
        raise ValueError("no median for empty data")
    if n % 2 == 1:
        return items[n // 2]
    return items[n // 2 - 1]


def median_high(data):
    """The upper of the two middle values, for an even count."""
    items = sorted(data)
    n = len(items)
    if not n:
        raise ValueError("no median for empty data")
    return items[n // 2]


def mode(data):
    """The most common value; the FIRST one at that count when several tie,
    which is what makes the answer depend on order rather than on a set."""
    items = list(data)
    if not items:
        raise StatisticsError("no mode for empty data")
    best = items[0]
    best_n = 0
    for candidate in items:
        seen = 0
        for other in items:
            if other == candidate:
                seen = seen + 1
        if seen > best_n:
            best = candidate
            best_n = seen
    return best


def variance(data, *centre):
    """The SAMPLE variance, dividing by n-1."""
    items = list(data)
    if len(items) < 2:
        raise StatisticsError("variance requires at least two data points")
    mid = centre[0] if centre else mean(items)
    total = 0.0
    for x in items:
        total = total + (x - mid) * (x - mid)
    return total / (len(items) - 1)


def pvariance(data, *centre):
    """The POPULATION variance, dividing by n."""
    items = list(data)
    if not items:
        raise StatisticsError("pvariance requires at least one data point")
    mid = centre[0] if centre else mean(items)
    total = 0.0
    for x in items:
        total = total + (x - mid) * (x - mid)
    return total / len(items)


def stdev(data, *centre):
    """The sample standard deviation."""
    return variance(data, *centre) ** 0.5


def pstdev(data, *centre):
    """The population standard deviation."""
    return pvariance(data, *centre) ** 0.5
