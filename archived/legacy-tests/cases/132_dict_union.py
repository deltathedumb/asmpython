# expect:
# 1 20 30
# 3
# 1 20 30
# 3
# 20 30
# 2
# 2 4 5
# 3
# alpha beta

# `d1 | d2` builds a new dict: d1's entries with d2's entries merged on top
# (d2 wins on key conflicts). Neither operand is mutated.
d1 = {"a": 1, "b": 2}
d2 = {"b": 20, "c": 30}

d3 = d1 | d2
print(d3["a"], d3["b"], d3["c"])
print(len(d3))

# `d1 |= d2` merges d2 into d1 in place.
d1 |= d2
print(d1["a"], d1["b"], d1["c"])
print(len(d1))
print(d2["b"], d2["c"])
print(len(d2))

# Chained union: rightmost operand wins on conflicting keys.
chained = {"x": 1} | {"x": 2, "y": 3} | {"y": 4, "z": 5}
print(chained["x"], chained["y"], chained["z"])
print(len(chained))

# str-valued dicts.
names1 = {"a": "alpha"}
names2 = {"b": "beta"}
merged_names = names1 | names2
print(merged_names["a"], merged_names["b"])
