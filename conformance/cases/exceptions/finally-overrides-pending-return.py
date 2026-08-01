# tier: spec
# ref: reference/compound_stmts.html#the-try-statement
# expect:
# from-finally
def f():
    try:
        return 'from-try'
    finally:
        return 'from-finally'

print(f())
