# tier: spec
# ref: library/string.html#format-specification-mini-language
# min-python: 3.11
# expect:
# 0.0
# -0.0
# 0.0
# 0.0
# 0.00
# -1.5
print(format(-0.0, "z.1f"))
print(format(-0.0, ".1f"))
print(format(0.0, "z.1f"))
print(format(-0.001, "z.1f"))
print(f"{-0.0:z.2f}")
print(format(-1.5, "z.1f"))
