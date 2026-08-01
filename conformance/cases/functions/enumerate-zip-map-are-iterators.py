# tier: spec
# ref: library/functions.html#enumerate
# expect:
# (0, 'a')
# [(1, 'b')]
# []
# ['1', '2'] []
# enumerate map
e = enumerate("ab")
print(next(e))
print(list(e))
print(list(e))
m = map(str, [1, 2])
print(list(m), list(m))
print(type(enumerate([])).__name__, type(map(str, [])).__name__)
