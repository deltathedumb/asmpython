# expect:
# [1, 2, 3, 4]
# [0, 1, 2]
# [1, 3]
# ['x', 'x', 'x']
# [1, 3, 6, 10]
# [1, 3, 5]

from itertools import chain, islice, repeat, accumulate, compress

a: list[int] = [1, 2]
b: list[int] = [3, 4]
print(chain(a, b))

nums: list[int] = [0, 1, 2, 3, 4, 5]
print(islice(nums, 3))
print(islice(nums, 1, 5, 2))

xs: list[str] = repeat("x", 3)
print(xs)

vals: list[int] = [1, 2, 3, 4]
print(accumulate(vals))

data: list[int] = [1, 2, 3, 4, 5]
sel: list[int] = [1, 0, 1, 0, 1]
print(compress(data, sel))
