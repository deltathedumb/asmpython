# probe106: complex number operations, math patterns
# Integer division edge cases
print(7 // 2)     # 3
print(-7 // 2)    # -4 (floor division)
print(7 // -2)    # -4
print(-7 // -2)   # 3

# Modulo edge cases
print(7 % 3)      # 1
print(-7 % 3)     # 2 (Python semantics)
print(7 % -3)     # -2
print(-7 % -3)    # -1

# Large numbers
big = 10 ** 18
print(big)          # 1000000000000000000
print(big + 1)      # 1000000000000000001
print(big * 2)      # 2000000000000000000

# Integer overflow behavior (Python: arbitrary precision, asmpython: 64-bit)
# This is just testing what asmpython does
n = 2 ** 62
print(n)   # 4611686018427387904

# GCD via Euclidean algorithm
def gcd(a: int, b: int) -> int:
    while b != 0:
        a, b = b, a % b
    return a

print(gcd(48, 18))   # 6
print(gcd(100, 75))  # 25
print(gcd(17, 5))    # 1
