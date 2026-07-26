# expect:
# f1
# f2
# inner
def f():
    try:
        try:
            return 'inner'
        finally:
            print('f1')
    finally:
        print('f2')
print(f())
# asmpython (beta/3.14.0) MISMATCH: prints 'inner\n' (wrong).
