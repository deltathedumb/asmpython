# tier: spec
# ref: reference/expressions.html#conditional-expressions
# expect:
# 1
# ab
# [1, 0]
# pos
print(1 if True else 2)
print("a" + ("b" if True else "c"))
print([1 if v else 0 for v in (True, False)])
x = 5
print("neg" if x < 0 else "zero" if x == 0 else "pos")
