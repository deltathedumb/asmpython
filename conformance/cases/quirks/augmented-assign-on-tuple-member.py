# tier: cpython
# ref: reference/simple_stmts.html#augmented-assignment-statements
# expect:
# TypeError
# ([1, 2],)
t = ([1],)
try:
    t[0] += [2]
except TypeError:
    print("TypeError")
print(t)
