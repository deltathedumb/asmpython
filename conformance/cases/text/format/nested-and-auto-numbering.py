# tier: spec
# ref: library/stdtypes.html#str.format
# expect:
#     42|
# aba
# ab
# 9
# 1.5
# {literal}
print("{:{}}".format(42, ">6") + "|")
print("{0}{1}{0}".format("a", "b"))
print("{}{}".format("a", "b"))
print("{x[0]}".format(x=[9]))
print("{a.real}".format(a=1.5))
print("{{literal}}".format())
