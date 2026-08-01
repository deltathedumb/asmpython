# probes: int is immutable; += rebinds
# expect:
# 1
# 2
n = 1
m = n
m += 1
print(n)
print(m)
