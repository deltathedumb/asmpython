# probes: float repr is the shortest round-tripping form
# expect:
# 0.3
# 0.3333333333333333
# 0.6666666666666666
# 1e+16
# 1e+17
print(0.3)
print(1 / 3)
print(2 / 3)
print(1e16)
print(1e17)
