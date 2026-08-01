# tier: spec
# ref: reference/expressions.html#comparisons
# expect:
# True True
# TypeError
# True
# False True
a, b = 1 + 2j, 3 + 4j
print(a == a, a != b)
try:
    a < b
except TypeError:
    print("TypeError")
print(complex(1, 0) == 1)
print(bool(0j), bool(1j))
