# tier: cpython
# ref: reference/compound_stmts.html#the-try-statement
# expect:
# finally
# cleanup 0
# 0
def f():
    try:
        return "try"
    finally:
        return "finally"

print(f())

def g():
    for i in range(3):
        try:
            break
        finally:
            print("cleanup", i)
    return i

print(g())
