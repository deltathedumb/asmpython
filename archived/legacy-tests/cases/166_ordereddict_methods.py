# expect:
# b
# c
# a
# ---
# c
# b
# a
# ---
# 2
# 1

from collections import OrderedDict

od = OrderedDict()
od["a"] = 1
od["b"] = 2
od["c"] = 3

od.move_to_end("a")
for k in od.keys():
    print(k)

print("---")

od.move_to_end("c", 0)
for k in od.keys():
    print(k)

print("---")

od.popitem()
print(len(od))

od.popitem(0)
print(len(od))
