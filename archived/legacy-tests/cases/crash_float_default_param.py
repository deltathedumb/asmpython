# expect:
# 3.0 6.0
def f(x=1.5):
    return x * 2
print(f(), f(3.0))
# asmpython (beta/3.14.0) MISMATCH: prints '2.9505442e-317 9.751022e-317\n' (wrong).
