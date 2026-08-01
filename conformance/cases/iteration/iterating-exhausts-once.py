# tier: spec
# ref: reference/datamodel.html#object.__next__
# expect:
# [1, 2]
# []
it = iter([1, 2])
print(list(it))
print(list(it))
