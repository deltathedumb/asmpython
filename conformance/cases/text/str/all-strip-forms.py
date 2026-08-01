# tier: spec
# ref: library/stdtypes.html#str.strip
# expect:
# 'ab'
# 'ab'
# 'ab'
# 'ab'
# ''
# 'ab'
print(repr("  ab  ".strip()))
print(repr("xxabxx".strip("x")))
print(repr("xyabyx".strip("xy")))
print(repr("ab".strip("z")))
print(repr("".strip()))
print(repr("\t\n ab \n\t".strip()))
