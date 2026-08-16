# expect:
# finally
def f():
    try:
        return 'try'
    finally:
        return 'finally'
print(f())
# asmpython (beta/3.14.0) MISMATCH: prints 'try\n' (wrong).
