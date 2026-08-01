# tier: spec
# ref: library/stdtypes.html#str.ljust
# expect:
# 'ab   '
# '---ab'
# '**ab**'
# '005'
# '-05'
print(repr("ab".ljust(5)))
print(repr("ab".rjust(5, "-")))
print(repr("ab".center(6, "*")))
print(repr("5".zfill(3)))
print(repr("-5".zfill(3)))
