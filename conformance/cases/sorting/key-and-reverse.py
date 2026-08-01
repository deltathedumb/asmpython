# tier: spec
# ref: library/functions.html#sorted
# expect:
# ['a', 'cc', 'bbb']
# ['bbb', 'cc', 'a']
# ['a', 'bbb', 'cc']
# ['bbb', 'a', 'cc']
xs = ["bbb", "a", "cc"]
print(sorted(xs, key=len))
print(sorted(xs, key=len, reverse=True))
print(sorted(xs))
print(xs)
