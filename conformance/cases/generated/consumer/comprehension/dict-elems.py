# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# [{'a': 1}, {'b': 2}]
xs = [{'a': 1}, {'b': 2}]
print([v for v in xs])
