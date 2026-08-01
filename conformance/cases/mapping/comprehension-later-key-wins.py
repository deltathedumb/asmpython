# tier: spec
# ref: reference/expressions.html#dictionary-displays
# expect:
# {'a': 2}
# {'a': 2}
# {'x': 2}
print({k: v for k, v in [("a", 1), ("a", 2)]})
print({"a": 1, "a": 2})
d = dict([("x", 1), ("x", 2)])
print(d)
