# tier: spec
# ref: reference/simple_stmts.html#assignment-statements
# expect:
# ValueError
# ValueError
# 1 2
# 1 2 3
try:
    a, b = [1]
except ValueError as e:
    print("ValueError")
try:
    a, b = [1, 2, 3]
except ValueError:
    print("ValueError")
a, b = [1, 2]
print(a, b)
(a, b), c = (1, 2), 3
print(a, b, c)
