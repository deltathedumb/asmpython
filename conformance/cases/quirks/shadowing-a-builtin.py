# tier: cpython
# ref: reference/executionmodel.html#naming-and-binding
# expect:
# 2
# 5
# 2
print(len([1, 2]))
len = 5
print(len)
del len
print(len([1, 2]))
