# tier: spec
# ref: library/stdtypes.html#common-sequence-operations
# expect:
# ababab
#
# ''
# 0
print("ab" * 3)
print("ab" * 0)
print(repr("ab" * -1))
print(len("ab" * 0))
