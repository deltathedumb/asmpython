# tier: spec
# ref: library/stdtypes.html#numeric-types-int-float-complex
# expect:
# 142857142857142857142857142857
# 1
# True
# -142857142857142857142857142858 6
# 648999181
a = 10 ** 30
print(a // 7)
print(a % 7)
print(divmod(a, 7)[0] * 7 + divmod(a, 7)[1] == a)
print((-a) // 7, (-a) % 7)
print(pow(a, 2, 1000000007))
