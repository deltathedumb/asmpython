# COVERAGE: heappush, heappop, heappushpop, heapify, heapreplace, merge (with
# key= and reverse=), nlargest, nsmallest (both with key=). NOT covered: the
# 3.14 heapq_max family (heappush_max, heappop_max, heapify_max,
# heapreplace_max, heappushpop_max).
#
# Run under CPython and under uasm; the outputs must be identical. So
# every assertion below is written against what the module IS SPECIFIED to
# do, not against what uasm currently does.
import heapq


def lcg(seed, n):
    """A tiny deterministic pseudo-random generator, so "random-ish" data
    needs no randomness module -- the sequence is identical on every run and
    under both runtimes."""
    values = []
    x = seed
    i = 0
    while i < n:
        x = (x * 1103515245 + 12345) % 2147483648
        values.append(x % 1000)
        i = i + 1
    return values


# ---- heappush/heappop maintain the heap invariant over unsorted data ----
data = lcg(12345, 60)
heap = []
for v in data:
    heapq.heappush(heap, v)
popped = []
while heap:
    popped.append(heapq.heappop(heap))
print(popped == sorted(data))
print(popped[:10])
print(popped[-10:])

# ---- heapify on an unsorted list: repeated heappop must return sorted order
data2 = lcg(999, 45)
h2 = list(data2)
heapq.heapify(h2)
out2 = []
while h2:
    out2.append(heapq.heappop(h2))
print(out2 == sorted(data2))
print(out2[:8])

# ---- heapreplace / heappushpop ----
h3 = [5, 7, 9, 1, 3]
heapq.heapify(h3)
print(heapq.heapreplace(h3, 0))
print(sorted(h3))
print(heapq.heappushpop(h3, 100))
print(sorted(h3))
print(heapq.heappushpop(h3, -5))
print(sorted(h3))

# ---- ties and duplicates ----
data3 = [4, 2, 4, 2, 4, 1, 1, 4, 2, 1]
h4 = list(data3)
heapq.heapify(h4)
out3 = []
while h4:
    out3.append(heapq.heappop(h4))
print(out3)
print(out3 == sorted(data3))

# ---- a heap of tuples, the priority-queue shape (priority, item) ----
pq = []
heapq.heappush(pq, (3, "c"))
heapq.heappush(pq, (1, "a"))
heapq.heappush(pq, (2, "b"))
heapq.heappush(pq, (1, "z"))
heapq.heappush(pq, (2, "a"))
pqorder = []
while pq:
    pqorder.append(heapq.heappop(pq))
print(pqorder)
print(pqorder == sorted([(3, "c"), (1, "a"), (2, "b"), (1, "z"), (2, "a")]))

# ---- nsmallest / nlargest, no key ----
data4 = lcg(42, 30)
print(heapq.nsmallest(5, data4))
print(heapq.nlargest(5, data4))
print(heapq.nsmallest(5, data4) == sorted(data4)[:5])
print(heapq.nlargest(5, data4) == sorted(data4, reverse=True)[:5])
print(heapq.nsmallest(1, data4) == sorted(data4)[:1])
print(heapq.nlargest(1, data4) == sorted(data4, reverse=True)[:1])
# n bigger than the input, and n == 0
print(heapq.nsmallest(100, [3, 1, 2]))
print(heapq.nlargest(100, [3, 1, 2]))
print(heapq.nsmallest(0, [3, 1, 2]))
print(heapq.nlargest(0, [3, 1, 2]))

# ---- nsmallest / nlargest with key=, sorting dicts by a field ----
people = [
    {"name": "eve", "age": 30},
    {"name": "bob", "age": 25},
    {"name": "carl", "age": 25},
    {"name": "amy", "age": 40},
    {"name": "dee", "age": 20},
    {"name": "flo", "age": 25},
]
youngest = heapq.nsmallest(3, people, key=lambda p: p["age"])
oldest = heapq.nlargest(3, people, key=lambda p: p["age"])
print([p["name"] for p in youngest])
print([p["name"] for p in oldest])

# ---- merge of several pre-sorted iterables into one sorted output ----
a = [1, 4, 7, 10]
b = [2, 3, 11]
c = []
d = [0, 5, 6, 8, 9]
merged = heapq.merge(a, b, c, d)
print(hasattr(merged, "__next__"))  # an iterator, not a materialised list
result = list(merged)
print(result)
print(result == sorted(a + b + c + d))

# ---- merge with reverse=True ----
ra = [10, 7, 4, 1]
rb = [11, 3, 2]
rc = [9, 8, 6, 5, 0]
rmerged = list(heapq.merge(ra, rb, rc, reverse=True))
print(rmerged)
print(rmerged == sorted(ra + rb + rc, reverse=True))

# ---- merge with key= ----
short_words = ["dog", "horse"]
other_words = ["cat", "fish", "kangaroo"]
print(list(heapq.merge(short_words, other_words, key=len)))

# ---- merge ties across iterables: equal keys keep input order ----
t1 = [1, 1, 3]
t2 = [1, 2, 3]
print(list(heapq.merge(t1, t2)))
t3 = [5, 5]
t4 = [5, 5]
t5 = [5]
print(list(heapq.merge(t3, t4, t5)))

# ---- merge is a genuine lazy k-way merge, not sorted(chain(*iterables)) ----
# `poison` would be read to exhaustion by anything that materialises its
# inputs up front; a real k-way merge only ever holds ONE buffered item per
# iterable, and only asks for a new one once the buffered one has been
# yielded and the caller comes back for more.
def poison():
    yield 1
    yield 2
    raise AssertionError("merge pulled a third item from poison() too soon")


lazy = heapq.merge([100, 200], poison())
print(next(lazy))
print(next(lazy))
try:
    next(lazy)
    print("BUG: no exception pulling past poison()'s second item")
except AssertionError as exc:
    print("lazy pull confirmed:", str(exc))

print("done")
