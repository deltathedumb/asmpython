# tier: spec
# ref: reference/expressions.html#displays-for-lists-sets-and-dictionaries
# expect:
# ['a', 'b'] outer
# [1, 3, 6] 6
v = "outer"
result = [v for v in "ab"]
print(result, v)

total = 0
sums = [total := total + n for n in (1, 2, 3)]
print(sums, total)
