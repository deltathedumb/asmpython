# tier: spec
# ref: library/stdtypes.html#common-sequence-operations
# expect:
# abab [0, 0, 0] (1, 1)
# '' [] ()
# 0
# -----
print("ab" * 2, [0] * 3, (1,) * 2)
print(repr("ab" * -1), [0] * -1, () * 5)
print(len("a" * 0))
print("-" * 5)
