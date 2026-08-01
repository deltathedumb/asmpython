# tier: spec
# ref: reference/lexical_analysis.html#operators
# expect:
# 1
# matmul-skipped
# True
# 7 -6 4 4
# 1 1 2
# 2
print(1 + 2 - 3 * 4 // 5 % 6 ** 7)
print(1 @ 1 if False else "matmul-skipped")
print(1 < 2 <= 3 != 4 == 4 >= 3 > 2)
print(1 & 2 | 3 ^ 4, ~5, 1 << 2, 8 >> 1)
print([1][0], (1,)[0], {1: 2}[1])
print(len({1, 2}))
