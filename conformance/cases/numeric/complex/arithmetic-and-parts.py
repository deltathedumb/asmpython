# tier: spec
# ref: library/stdtypes.html#numeric-types-int-float-complex
# expect:
# (3+4j) 3.0 4.0
# 5.0
# (4+4j) (6+8j)
# (3-4j)
# (-5+10j)
# (1+2j)
z = complex(3, 4)
print(z, z.real, z.imag)
print(abs(z))
print(z + 1, z * 2)
print(z.conjugate())
print((1 + 2j) * (3 + 4j))
print(complex("1+2j"))
