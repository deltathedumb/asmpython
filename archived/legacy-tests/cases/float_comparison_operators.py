# expect:
# True False True False False True
a, b = 1.5, 2.5
print(a < b, a > b, a <= b, a >= b, a == b, a != b)
# asmpython (beta/3.14.0) rejects at compile: [E144] tuple assign target: float values aren't supported in parallel assignment yet (assign separately)
