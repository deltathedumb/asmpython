# tier: spec
# ref: reference/compound_stmts.html#the-try-statement
# expect:
# ValueError v
def f():
    try:
        raise ValueError("v")
    except ValueError:
        raise

try:
    f()
except ValueError as e:
    print("ValueError", e)
