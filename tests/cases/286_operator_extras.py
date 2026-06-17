# expect:
# -1
# 1
# 0
# 1

from operator import invert, inv, is_, is_not, index, setitem, delitem
from operator import iadd, isub, imul, ior, iand, ixor

print(invert(0))
print(is_(1, 1))
print(is_not(2, 2))
print(index(1))
