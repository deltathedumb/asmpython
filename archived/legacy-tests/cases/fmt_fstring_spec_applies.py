# probes: a spec after : applies inside an f-string
# expect:
# 3.14
# 00042
v = 3.14159
print(f"{v:.2f}")
print(f"{42:05}")
