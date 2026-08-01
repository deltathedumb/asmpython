# tier: spec
# ref: library/stdtypes.html#printf-style-string-formatting
# expect:
# 1 a
# 03.14
# ff
# 'q'
# %%
print("%d %s" % (1, "a"))
print("%05.2f" % 3.14159)
print("%x" % 255)
print("%r" % ("q",))
print("%%")
