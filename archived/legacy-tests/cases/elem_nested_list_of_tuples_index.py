# probes: list.index locates a tuple element by value
# expect:
# 1
# True
rows = [(1, "a"), (2, "b")]
print(rows.index((2, "b")))
print((1, "a") in rows)
