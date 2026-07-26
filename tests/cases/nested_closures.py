# expect:
# 6
def a(x):
    def b(y):
        def c(z):
            return x + y + z
        return c
    return b
print(a(1)(2)(3))
# asmpython (beta/3.14.0) MISMATCH: prints '0\n' (wrong).
