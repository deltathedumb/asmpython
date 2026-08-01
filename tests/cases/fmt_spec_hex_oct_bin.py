# probes: x/o/b render integer bases
# expect:
# ff
# FF
# 10
# 101
print(format(255, "x"))
print(format(255, "X"))
print(format(8, "o"))
print(format(5, "b"))
