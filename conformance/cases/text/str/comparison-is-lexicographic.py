# tier: spec
# ref: library/stdtypes.html#comparisons
# expect:
# True
# True
# True
# True
# ['A', 'B', 'a', 'b']
print("a" < "b")
print("abc" < "abd")
print("Z" < "a")
print("" < "a")
print(sorted(["b", "A", "a", "B"]))
