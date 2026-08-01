# tier: spec
# ref: reference/expressions.html#displays-for-lists-sets-and-dictionaries
# expect:
# [0, 2, 4] outer
# [0, 1]
# NameError
i = "outer"
squares = [i * 2 for i in range(3)]
print(squares, i)
gen = list(j for j in range(2))
print(gen)
try:
    print(j)
except NameError:
    print("NameError")
