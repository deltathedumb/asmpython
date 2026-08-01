# tier: spec
# ref: reference/lexical_analysis.html#f-strings
# expect:
# 'q'
# q
# 3.14
# n=3.14
# ab
v = "q"
n = 3.14159
print(f"{v!r}")
print(f"{v!s}")
print(f"{n:.2f}")
print(f"{n=:.2f}")
print(f"{'a' + 'b'}")
