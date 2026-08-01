# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# [[1, 2], [1, 2]]
# True
inner = [1]
outer = [inner, inner]
outer[0].append(2)
print(outer)
print(outer[0] is outer[1])
