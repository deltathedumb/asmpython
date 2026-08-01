# tier: spec
# ref: reference/expressions.html#comparisons
# expect:
# True
# True
# True
# True
# True
# True
print(1 < 2 < 3)
print(1 < 3 > 2)
print(1 == 1 == 1)
print("a" < "b" < "c")
print(1 < 2 != 3)
x = [1, 2]
print(0 < len(x) <= 2)
