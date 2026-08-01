# tier: spec
# ref: reference/compound_stmts.html#the-try-statement
# expect:
# [1, 2]
# 1
def f():
    xs = [1]
    try:
        return xs
    finally:
        xs.append(2)

print(f())

def g():
    n = 1
    try:
        return n
    finally:
        n = 99

print(g())
