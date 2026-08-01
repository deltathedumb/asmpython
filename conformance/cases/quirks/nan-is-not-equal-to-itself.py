# tier: spec
# ref: library/stdtypes.html#numeric-types-int-float-complex
# expect:
# False
# True
# True
# True
# True
nan = float("nan")
print(nan == nan)
print(nan != nan)
print([nan] == [nan])
print(nan in [nan])
print(sorted([1.0, nan, 2.0]) == [1.0, nan, 2.0])
