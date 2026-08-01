# tier: spec
# ref: peps.python.org/pep-0515/
# expect:
# 1000000
# 255
# 170
# 15
# 10.01
# 1000
print(1_000_000)
print(0x_FF)
print(0b1010_1010)
print(0o1_7)
print(1_0.0_1)
print(int('1_000'))
