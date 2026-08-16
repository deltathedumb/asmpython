# probes: a sequence pattern destructures and binds
# expect:
# origin
# on x at 3
# at 1,2
def describe(point):
    match point:
        case [0, 0]:
            return "origin"
        case [x, 0]:
            return "on x at " + str(x)
        case [x, y]:
            return "at " + str(x) + "," + str(y)
        case _:
            return "unknown"


print(describe([0, 0]))
print(describe([3, 0]))
print(describe([1, 2]))
