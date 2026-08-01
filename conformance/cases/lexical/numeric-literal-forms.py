# tier: spec
# ref: reference/lexical_analysis.html#numeric-literals
# expect:
# 1000000
# 255 10 15
# 1000.0 1000.0 0.001
# 1.5j complex
# 0.5 5.0
# 3735928559
print(1_000_000)
print(0x_FF, 0b_1010, 0o_17)
print(1e3, 1E3, 1e-3)
print(1.5j, type(1.5j).__name__)
print(.5, 5.)
print(0xdeadBEEF)
