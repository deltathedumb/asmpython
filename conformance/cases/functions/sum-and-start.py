# tier: spec
# ref: library/functions.html#sum
# expect:
# 16
# 1.0
# 2
# int
# TypeError
print(sum([1, 2, 3], 10))
print(sum([0.5, 0.5]))
print(sum([True, True, False]))
print(type(sum([True])).__name__)
try:
    sum(["a", "b"], "")
except TypeError:
    print("TypeError")
