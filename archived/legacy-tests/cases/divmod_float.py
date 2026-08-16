# expect:
# (3.0, 1.5)
print(divmod(7.5, 2.0))
# divmod() rejects float args ([E022]); CPython returns (3.0, 1.5).
