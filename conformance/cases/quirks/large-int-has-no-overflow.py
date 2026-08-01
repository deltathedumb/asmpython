# tier: spec
# ref: library/stdtypes.html#numeric-types-int-float-complex
# expect:
# 18446744073709551616
# 18446744073709551615
# 340282366920938463463374607431768211456
# 510646
# 151
n = 2 ** 64
print(n)
print(n - 1)
print(n * n)
print((2 ** 1000) % 1000003)
print(len(str(2 ** 500)))
