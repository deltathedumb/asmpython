# tier: spec
# ref: reference/lexical_analysis.html#explicit-line-joining
# expect:
# 3
# [1, 2]
# ab
total = 1 + \
    2
print(total)
xs = [
    1,
    2,
]
print(xs)
s = ("a"
     "b")
print(s)
