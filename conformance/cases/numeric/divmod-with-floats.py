# tier: spec
# ref: library/functions.html#divmod
# expect:
# (3.0, 1.5)
# (-4.0, 0.5)
# 3.0 1.5
# float
print(divmod(7.5, 2))
print(divmod(-7.5, 2))
print(7.5 // 2, 7.5 % 2)
print(type(7.5 // 2).__name__)
