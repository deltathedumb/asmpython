# expect:
# 6.0
def outer(x):
    def inner(y):
        return x * y
    return inner(2.0)
print(outer(3.0))
# asmpython (beta/3.14.0) MISMATCH: prints '24056584798208\n' (wrong).
