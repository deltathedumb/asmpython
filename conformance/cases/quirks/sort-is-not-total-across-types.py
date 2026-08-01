# tier: spec
# ref: library/stdtypes.html#list.sort
# expect:
# [1.5, 2, 3]
# [0, True, 2]
# TypeError
# ['A', 'b']
print(sorted([3, 1.5, 2]))
print(sorted([True, 0, 2]))
try:
    sorted([1, "a"])
except TypeError:
    print("TypeError")
print(sorted(["b", "A"], key=str.lower))
