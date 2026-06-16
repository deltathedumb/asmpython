# expect:
# 1
# 3
# a
# b
# 10
# 20

pairs: list[tuple[int, str]] = [(1, "a"), (3, "b")]
nums = [n for n, _ in pairs]
strs = [s for _, s in pairs]
print(nums[0])
print(nums[1])
print(strs[0])
print(strs[1])

triples: list[tuple[int, int, int]] = [(10, 20, 30), (40, 50, 60)]
xs = [x for x, y, _ in triples]
ys = [y for _, y, z in triples]
print(xs[0])
print(ys[0])
