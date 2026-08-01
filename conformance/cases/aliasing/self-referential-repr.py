# tier: spec
# ref: library/stdtypes.html#list
# expect:
# [1, [...]]
# 2
# True
xs = [1]
xs.append(xs)
print(xs)
print(len(xs))
print(xs[1] is xs)
