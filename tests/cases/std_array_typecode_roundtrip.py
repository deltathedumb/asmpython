# probes: array stores and reads back typed elements
# expect:
# i
# [1, 2, 3]
import array

a = array.array("i", [1, 2])
a.append(3)
print(a.typecode)
print(list(a))
