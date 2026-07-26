# expect:
# 212.0
def f(c):
    return c * 9 / 5 + 32
print(f(100))
# asmpython (beta/3.14.0) MISMATCH: prints '1\n' (wrong).
