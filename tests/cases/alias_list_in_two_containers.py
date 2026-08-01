# probes: one list reachable from two containers
# expect:
# [1, 2]
# [1, 2]
# 2
shared = [1]
left = [shared]
right = {"v": shared}
shared.append(2)
print(left[0])
print(right["v"])
print(len(left[0]))
