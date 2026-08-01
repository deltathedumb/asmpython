# probes: reversed works on list, tuple and str
# expect:
# [3, 2, 1]
# [2, 1]
# cba
print(list(reversed([1, 2, 3])))
print(list(reversed((1, 2))))
print("".join(reversed("abc")))
