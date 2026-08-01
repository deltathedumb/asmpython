# tier: cpython
# ref: reference/compound_stmts.html#the-try-statement
# expect:
# finally wins
# ValueError kept
def f():
    try:
        raise ValueError("lost")
    finally:
        return "finally wins"

print(f())

def g():
    try:
        raise ValueError("kept")
    finally:
        pass

try:
    g()
except ValueError as e:
    print("ValueError", e)
