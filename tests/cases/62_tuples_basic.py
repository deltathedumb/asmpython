# expect:
# 10
# hi
# 3.5
# len: 3
# 30
# first: 10
# dyn: 20

# A heterogeneous tuple: int, str, float in one value.
t = (10, "hi", 3.5)
print(t[0])
print(t[1])
print(t[2])
print("len:", len(t))

nums = (10, 20, 30)
# Negative index.
print(nums[-1])
# Constant index.
print("first:", nums[0])
# Dynamic index is allowed because this tuple is homogeneous.
i = 1
print("dyn:", nums[i])
