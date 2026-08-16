# expect:
# 212.0 32.0 98.6
def c_to_f(c):
    return c * 9 / 5 + 32
print(c_to_f(100), c_to_f(0), c_to_f(37))
# asmpython (beta/3.14.0) MISMATCH: prints '1 1 1\n' (wrong).
