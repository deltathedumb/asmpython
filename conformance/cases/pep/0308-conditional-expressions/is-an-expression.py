# tier: spec
# ref: reference/expressions.html#conditional-expressions
# expect:
# ['even', 'odd', 'even']
# y
# {'k': 1}
# 3
print([("even" if v % 2 == 0 else "odd") for v in range(3)])
print((lambda v: "y" if v else "n")(1))
d = {"k": 1 if True else 2}
print(d)
print(1 if 0 else 2 if 0 else 3)
