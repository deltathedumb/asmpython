# tier: spec
# ref: reference/expressions.html#dictionary-displays
# expect:
# {'a': 1, 'b': 2}
# {'a': 1, 'b': 2}
# [('a', 1), ('b', 2)]
keys = ["a", "b"]
vals = [1, 2]
print({k: v for k, v in zip(keys, vals)})
print(dict(zip(keys, vals)))
print(sorted(dict(zip(keys, vals)).items()))
