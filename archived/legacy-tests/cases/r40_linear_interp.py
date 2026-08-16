# expect:
# 5.0 25.0
def lerp(a, b, t):
    return a + (b - a) * t
print(lerp(0, 10, 0.5), lerp(0, 100, 0.25))
# asmpython (beta/3.14.0) MISMATCH: prints '0.0 0.0\n' (wrong).
