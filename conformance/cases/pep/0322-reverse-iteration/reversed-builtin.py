# tier: spec
# ref: library/functions.html#reversed
# expect:
# [3, 2, 1]
# ['c', 'b', 'a']
# [2, 1, 0]
# TypeError
print(list(reversed([1, 2, 3])))
print(list(reversed("abc")))
print(list(reversed(range(3))))
try:
    reversed({1, 2})
except TypeError:
    print("TypeError")
