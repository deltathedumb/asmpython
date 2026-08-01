# tier: spec
# ref: reference/expressions.html#membership-test-operations
# expect:
# True True
# True False
# True
# True
# True True
# TypeError
print(1 in [1, 2], 3 not in [1, 2])
print("a" in {"a": 1}, 1 in {"a": 1})
print("a" in {"a", "b"})
print("ab" in "xaby")
print(1 in (1, 2), 1 in range(3))
try:
    1 in 1
except TypeError:
    print("TypeError")
