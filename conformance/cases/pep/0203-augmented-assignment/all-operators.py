# tier: spec
# ref: reference/simple_stmts.html#augmented-assignment-statements
# expect:
# 11
# 9
# 27
# 13
# 6
# 36
# 72
# 18
# 26
# 8
# 13
n = 10
n += 1; print(n)
n -= 2; print(n)
n *= 3; print(n)
n //= 2; print(n)
n %= 7; print(n)
n **= 2; print(n)
n <<= 1; print(n)
n >>= 2; print(n)
n |= 8; print(n)
n &= 12; print(n)
n ^= 5; print(n)
