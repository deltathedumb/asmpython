# expect:
# 1 20 30
# 3
# 1 99 4
# 3
# 1 100 30
# 1 2 2
# 20 30 2
# 1 2 2

# `{**other, ...}` (PEP 448) merges another dict's entries in, in source
# order; later entries (spreads or explicit keys) win on key conflicts.
d1 = {"a": 1, "b": 2}
d2 = {"b": 20, "c": 30}

merged = {**d1, **d2}
print(merged["a"], merged["b"], merged["c"])
print(len(merged))

with_override = {**d1, "b": 99, "d": 4}
print(with_override["a"], with_override["b"], with_override["d"])
print(len(with_override))

# Spread first, then a literal key overrides it.
overrides = {**d1, **d2, "b": 100}
print(overrides["a"], overrides["b"], overrides["c"])

# Neither source dict is mutated by being spread.
print(d1["a"], d1["b"], len(d1))
print(d2["b"], d2["c"], len(d2))

# Spread-only literal: a shallow copy.
copy = {**d1}
print(copy["a"], copy["b"], len(copy))
