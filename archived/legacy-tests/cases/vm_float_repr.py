# probes: float formatting matches CPython
# expect:
# 0.30000000000000004
# 1.0
# 1e+20
# 1e-07
# 1.5
# 0.3333333333333333
print(0.1 + 0.2)
print(1.0)
print(1e20)
print(1e-7)
print(3.0 / 2.0)
print(1.0 / 3.0)
