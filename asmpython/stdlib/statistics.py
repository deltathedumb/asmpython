"""statistics module: mathematical statistics functions.

Pure-Python implementation of common statistical functions over
lists of ints/floats.
"""
from __future__ import annotations

import math


def mean(data: list) -> float:
    """Return the arithmetic mean of the data."""
    n: int = len(data)
    if n == 0:
        return 0.0
    total: float = 0.0
    i: int = 0
    while i < n:
        total = total + data[i]
        i = i + 1
    return total / n


def fmean(data: list) -> float:
    """Return the mean as a float."""
    return mean(data)


def geometric_mean(data: list) -> float:
    """Return the geometric mean of positive data."""
    n: int = len(data)
    if n == 0:
        return 0.0
    log_sum: float = 0.0
    i: int = 0
    while i < n:
        log_sum = log_sum + math.log(float(data[i]))
        i = i + 1
    return math.exp(log_sum / n)


def harmonic_mean(data: list) -> float:
    """Return the harmonic mean of positive data."""
    n: int = len(data)
    if n == 0:
        return 0.0
    recip_sum: float = 0.0
    i: int = 0
    while i < n:
        recip_sum = recip_sum + 1.0 / data[i]
        i = i + 1
    return n / recip_sum


def median(data: list) -> float:
    """Return the median (middle value) of the data."""
    n: int = len(data)
    if n == 0:
        return 0.0
    sorted_data: list = _sort(data)
    if n % 2 == 1:
        return sorted_data[n // 2]
    return (sorted_data[n // 2 - 1] + sorted_data[n // 2]) / 2.0


def median_low(data: list) -> float:
    """Return the low median of the data."""
    n: int = len(data)
    if n == 0:
        return 0.0
    sorted_data: list = _sort(data)
    if n % 2 == 1:
        return sorted_data[n // 2]
    return sorted_data[n // 2 - 1]


def median_high(data: list) -> float:
    """Return the high median of the data."""
    n: int = len(data)
    if n == 0:
        return 0.0
    sorted_data: list = _sort(data)
    return sorted_data[n // 2]


def mode(data: list) -> int:
    """Return the most common value in the data."""
    n: int = len(data)
    if n == 0:
        return 0
    best_val: int = data[0]
    best_count: int = 0
    i: int = 0
    while i < n:
        count: int = 0
        j: int = 0
        while j < n:
            if data[j] == data[i]:
                count = count + 1
            j = j + 1
        if count > best_count:
            best_count = count
            best_val = data[i]
        i = i + 1
    return best_val


def multimode(data: list) -> list:
    """Return a list of the most common values."""
    n: int = len(data)
    if n == 0:
        return []
    best_count: int = 0
    i: int = 0
    while i < n:
        count: int = 0
        j: int = 0
        while j < n:
            if data[j] == data[i]:
                count = count + 1
            j = j + 1
        if count > best_count:
            best_count = count
        i = i + 1
    result: list = []
    i = 0
    while i < n:
        count2: int = 0
        j: int = 0
        while j < n:
            if data[j] == data[i]:
                count2 = count2 + 1
            j = j + 1
        if count2 == best_count:
            already: int = 0
            k: int = 0
            while k < len(result):
                if result[k] == data[i]:
                    already = 1
                k = k + 1
            if already == 0:
                result.append(data[i])
        i = i + 1
    return result


def variance(data: list) -> float:
    """Return the sample variance of the data."""
    n: int = len(data)
    if n < 2:
        return 0.0
    m: float = mean(data)
    total: float = 0.0
    i: int = 0
    while i < n:
        diff: float = data[i] - m
        total = total + diff * diff
        i = i + 1
    return total / (n - 1)


def pvariance(data: list) -> float:
    """Return the population variance of the data."""
    n: int = len(data)
    if n == 0:
        return 0.0
    m: float = mean(data)
    total: float = 0.0
    i: int = 0
    while i < n:
        diff: float = data[i] - m
        total = total + diff * diff
        i = i + 1
    return total / n


def stdev(data: list) -> float:
    """Return the sample standard deviation of the data."""
    return math.sqrt(variance(data))


def pstdev(data: list) -> float:
    """Return the population standard deviation of the data."""
    return math.sqrt(pvariance(data))


def _sort(data: list) -> list:
    """Return a sorted copy of data (insertion sort)."""
    result: list = []
    i: int = 0
    while i < len(data):
        result.append(data[i])
        i = i + 1
    n: int = len(result)
    i = 1
    while i < n:
        key: int = result[i]
        j: int = i - 1
        while j >= 0 and result[j] > key:
            result[j + 1] = result[j]
            j = j - 1
        result[j + 1] = key
        i = i + 1
    return result


def quantiles(data: list, n: int = 4) -> list:
    """Return n-1 cut points dividing the data into n equal intervals."""
    sorted_data: list = _sort(data)
    m: int = len(sorted_data)
    result: list = []
    i: int = 1
    while i < n:
        idx: float = i * (m - 1) / n
        lo: int = idx
        hi: int = lo + 1
        if hi >= m:
            result.append(sorted_data[m - 1])
        else:
            frac: float = idx - lo
            result.append(sorted_data[lo] + frac * (sorted_data[hi] - sorted_data[lo]))
        i = i + 1
    return result


def covariance(x: list, y: list) -> float:
    """Return the sample covariance of two datasets."""
    n: int = len(x)
    if n < 2:
        return 0.0
    mx: float = mean(x)
    my: float = mean(y)
    total: float = 0.0
    i: int = 0
    while i < n:
        total = total + (x[i] - mx) * (y[i] - my)
        i = i + 1
    return total / (n - 1)


def correlation(x: list, y: list) -> float:
    """Return the Pearson correlation coefficient for two datasets."""
    cov: float = covariance(x, y)
    sx: float = stdev(x)
    sy: float = stdev(y)
    if sx == 0.0 or sy == 0.0:
        return 0.0
    return cov / (sx * sy)
