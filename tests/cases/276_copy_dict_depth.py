# expect:
# 3
# 1
# hello
# 10
# 1

import copy

a: list = [10, 20, 30]
b: list = copy.copy(a)
b.append(40)
print(len(a))
print(a[0] // 10)
print("hello")
print(a[0])
print(a[0] // 10)
