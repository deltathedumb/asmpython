# probe89: complex list manipulations
# list.sort with key
data = [("bob", 3), ("alice", 1), ("charlie", 2)]
data.sort(key=lambda x: x[1])
print(data[0][0])   # alice
print(data[1][0])   # charlie
print(data[2][0])   # bob

# list.sort with reverse
nums = [3, 1, 4, 1, 5, 9, 2, 6]
nums.sort(reverse=True)
print(nums[0])   # 9
print(nums[-1])  # 1

# list * int
repeated = [0] * 5
print(len(repeated))   # 5
print(repeated[0])     # 0

# list concat
a = [1, 2, 3]
b = [4, 5, 6]
c = a + b
print(len(c))   # 6
print(c[3])     # 4

# in-place addition
a += b
print(len(a))   # 6

# list.clear
lst = [1, 2, 3, 4, 5]
lst.clear()
print(len(lst))   # 0

# list.index and count
nums2 = [1, 2, 3, 2, 1, 2]
print(nums2.index(2))    # 1
print(nums2.count(2))    # 3
