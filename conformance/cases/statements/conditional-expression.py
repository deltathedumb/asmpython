# tier: spec
# ref: reference/expressions.html#conditional-expressions
# expect:
# yes
# no
# 1 [1]
print("yes" if 1 else "no")
print("yes" if 0 else "no")
calls = []

def side(v):
    calls.append(v)
    return v

r = side(1) if True else side(2)
print(r, calls)
