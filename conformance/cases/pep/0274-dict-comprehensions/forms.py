# tier: spec
# ref: reference/expressions.html#dictionary-displays
# expect:
# {'a': 1, 'b': 2}
# {0: 0, 1: 1, 2: 4}
# {1: 1, 3: 3}
# [('A', 1)]
print({k: v for k, v in [("a", 1), ("b", 2)]})
print({v: v * v for v in range(3)})
print({v: v for v in range(5) if v % 2})
print(sorted({k.upper(): n for k, n in [("a", 1)]}.items()))
