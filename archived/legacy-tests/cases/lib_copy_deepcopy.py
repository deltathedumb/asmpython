# expect:
# [[1, 2], [3, 4]] [[9, 2], [3, 4]]
import copy
a = [[1, 2], [3, 4]]
b = copy.deepcopy(a)
b[0][0] = 9
print(a, b)
# asmpython (beta/3.14.0) rejects at compile: [E017] cannot index a int
