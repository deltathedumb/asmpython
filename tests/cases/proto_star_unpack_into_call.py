# probes: *seq spreads into a call
# expect:
# 6
def add3(a, b, c):
    return a + b + c


print(add3(*[1, 2, 3]))
